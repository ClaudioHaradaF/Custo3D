import re
import base64
import io
import os

GCODE_HEADER_SIZE = 1024 * 200
GCODE_TAIL_SIZE = 1024 * 200
GCODE_MID_SIZE = 1024 * 512
E_SCAN_CHUNK_SIZE = 1024 * 512

class GCodeParser:
    def __init__(self, filepath):
        self.filepath = filepath
        self.filament_length_mm = 0
        self.print_time_seconds = 0
        self.layer_count = 0
        self.thumbnail_data = None
        self.thumbnail_size = None
        self.total_lines = 0
        self.is_relative_extrusion = True
        self.filament_density = 0.0
        self.filament_diameter = 1.75
        self.filament_weight_g = 0.0

    def parse(self):
        try:
            filesize = os.path.getsize(self.filepath)
            if filesize == 0:
                return {'error': 'Arquivo vazio'}

            with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
                header = f.read(min(GCODE_HEADER_SIZE, filesize))

            tail = ''
            if filesize > GCODE_HEADER_SIZE:
                with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(max(0, filesize - GCODE_TAIL_SIZE))
                    tail = f.read()

            self._extract_thumbnail(header)
            self._parse_print_time(header, tail)
            self._parse_extrusion_mode(header)
            self._count_layers(header, tail)
            self._parse_filament_density(header)
            self._parse_filament_diameter(header)
            self._parse_filament_from_comments(tail)

            if self.filament_length_mm <= 0:
                self._parse_extrusion_fast()

            if self.print_time_seconds <= 0:
                self._estimate_time_from_filename()

            return {
                'filament_length_mm': round(self.filament_length_mm, 2),
                'print_time_seconds': self.print_time_seconds,
                'layer_count': self.layer_count,
                'estimated_weight_grams': round(self._calculate_weight(), 2),
                'thumbnail_data': self.thumbnail_data,
                'thumbnail_size': self.thumbnail_size,
                'filament_weight_g': round(self.filament_weight_g, 2) if self.filament_weight_g > 0 else 0,
            }
        except Exception as e:
            return {'error': str(e)}

    def _parse_filament_density(self, content):
        match = re.search(r';\s*filament_density\s*[:=]\s*([0-9.,\s]+)', content, re.IGNORECASE)
        if match:
            densities = re.findall(r'[0-9.]+', match.group(1))
            if densities:
                self.filament_density = float(densities[0])

    def _parse_filament_diameter(self, content):
        match = re.search(r';\s*filament_diameter\s*[:=]\s*([0-9.,\s]+)', content, re.IGNORECASE)
        if match:
            diameters = re.findall(r'[0-9.]+', match.group(1))
            if diameters:
                self.filament_diameter = float(diameters[0])

    def _parse_filament_from_comments(self, tail):
        if not tail:
            return
        try:
            # Length first (sum multi-filament values)
            length_match = re.search(r';\s*filament\s+used\s*\[mm\]\s*=\s*([0-9.,\s]+)', tail, re.IGNORECASE)
            if length_match:
                lengths = re.findall(r'[0-9.]+', length_match.group(1))
                if lengths:
                    total_length = sum(float(l) for l in lengths)
                    if total_length > 0:
                        self.filament_length_mm = total_length

            # Weight: try total first, then sum multi-filament values
            total_weight = None
            weight_match = re.search(r';\s*total\s+filament\s+used\s*\[g\]\s*=\s*([0-9.]+)', tail, re.IGNORECASE)
            if weight_match:
                total_weight = float(weight_match.group(1))
            else:
                weight_match = re.search(r';\s*filament\s+used\s*\[g\]\s*=\s*([0-9.,\s]+)', tail, re.IGNORECASE)
                if weight_match:
                    weights = re.findall(r'[0-9.]+', weight_match.group(1))
                    if weights:
                        total_weight = sum(float(w) for w in weights)

            if total_weight is not None:
                self.filament_weight_g = total_weight
        except:
            pass

    def _extract_thumbnail(self, content):
        pattern = r';\s*thumbnail\s+begin\s+(\d+)x(\d+)\s+\d+\s*\n(.*?);\s*thumbnail\s+end'
        matches = re.findall(pattern, content, re.DOTALL)
        if not matches:
            pattern2 = r';gimage:([A-Za-z0-9+/=]+)'
            match2 = re.search(pattern2, content)
            if match2:
                try:
                    self.thumbnail_data = base64.b64decode(match2.group(1))
                    self.thumbnail_size = 'ColPic'
                except:
                    pass
            return

        best_match = None
        best_area = 0
        for width, height, data in matches:
            area = int(width) * int(height)
            if area > best_area:
                best_area = area
                best_match = (width, height, data)

        if best_match:
            width, height, data = best_match
            cleaned = ''
            for line in data.split('\n'):
                line = line.strip()
                if not line:
                    continue
                if line.startswith(';'):
                    line = line[1:].strip()
                if line:
                    cleaned += line
            try:
                self.thumbnail_data = base64.b64decode(cleaned)
                self.thumbnail_size = (int(width), int(height))
            except:
                pass

    def _parse_print_time(self, header, tail):
        content = header
        if tail:
            content = header + '\n' + tail

        patterns = [
            r';TIME:(\d+)',
            r'; Print time: (\d+)',
            r';\s*estimated\s+printing\s+time\s*\(normal\s+mode\)\s*=\s*(\d+)h\s*(\d+)m\s*(\d+)s',
            r';\s*estimated\s+printing\s+time\s*\(normal\s+mode\)\s*=\s*(\d+)m\s*(\d+)s',
            r';\s*estimated\s+printing\s+time\s*\(normal\s+mode\)\s*=\s*(\d+)s',
            r';\s*estimated\s+printing\s+time\s*=\s*(\d+)\s*s',
            r';\s*estimated\s+printing\s+time\s*=\s*(\d+)h\s*(\d+)m\s*(\d+)s',
            r';\s*estimated\s+printing\s+time\s*=\s*(\d+)m\s*(\d+)s',
            r'M73 P\d+\s+R(\d+)',
            r'M73 P\d+R(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    groups = match.groups()
                    if len(groups) == 1:
                        self.print_time_seconds = int(groups[0])
                    elif len(groups) == 2:
                        m, s = int(groups[0]), int(groups[1])
                        self.print_time_seconds = m * 60 + s
                    elif len(groups) == 3:
                        h, m, s = int(groups[0]), int(groups[1]), int(groups[2])
                        self.print_time_seconds = h * 3600 + m * 60 + s
                    if self.print_time_seconds > 0:
                        return
                except:
                    continue

    def _estimate_time_from_filename(self):
        filename = os.path.splitext(os.path.basename(self.filepath))[0]
        patterns = [
            r'(\d+)h(\d+)m(\d+)s',
            r'(\d+)h(\d+)m',
            r'(\d+)m(\d+)s',
            r'_(\d+)h_',
            r'_(\d+)m_',
        ]
        for pat in patterns:
            match = re.search(pat, filename)
            if match:
                try:
                    groups = match.groups()
                    if len(groups) == 3:
                        h, m, s = int(groups[0]), int(groups[1]), int(groups[2])
                        self.print_time_seconds = h * 3600 + m * 60 + s
                    elif len(groups) == 2:
                        if 'h' in pat:
                            h, m = int(groups[0]), int(groups[1])
                            self.print_time_seconds = h * 3600 + m * 60
                        else:
                            m, s = int(groups[0]), int(groups[1])
                            self.print_time_seconds = m * 60 + s
                    elif len(groups) == 1:
                        if 'h' in pat:
                            self.print_time_seconds = int(groups[0]) * 3600
                        else:
                            self.print_time_seconds = int(groups[0]) * 60
                    if self.print_time_seconds > 0:
                        return
                except:
                    continue

    def _parse_extrusion_mode(self, content):
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('M82'):
                self.is_relative_extrusion = False
                return
            elif line.startswith('M83'):
                self.is_relative_extrusion = True
                return
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('G21'):
                break
            if line.startswith(';'):
                if 'relative extrusion' in line.lower() and 'absolute' in line.lower():
                    self.is_relative_extrusion = 'absolute' in line.lower()
                    return
                if 'relative' in line.lower() and 'extrusion' in line.lower():
                    self.is_relative_extrusion = True
                    return
                if 'absolute' in line.lower() and 'extrusion' in line.lower():
                    self.is_relative_extrusion = False
                    return

    def _parse_extrusion_fast(self):
        filesize = os.path.getsize(self.filepath)
        total_e = 0.0
        last_e = None
        found_moves = False
        need_time_estimate = self.print_time_seconds <= 0
        prev_x = prev_y = prev_z = 0.0
        prev_f = 60.0
        distance_sum = 0.0
        feedrate_sum = 0.0
        move_count = 0

        with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
            chunk_start = 0

            while chunk_start < filesize:
                chunk_size = min(E_SCAN_CHUNK_SIZE, filesize - chunk_start)
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                for line in chunk.split('\n'):
                    line = line.strip()
                    if not line or line.startswith(';') or line.startswith('('):
                        continue
                    if not (line.startswith('G0') or line.startswith('G1')):
                        continue

                    e_match = re.search(r'E([-0-9.]+)', line)
                    if e_match:
                        e_val = float(e_match.group(1))
                        if e_val != 0:
                            found_moves = True
                            if self.is_relative_extrusion:
                                if e_val > 0:
                                    total_e += e_val
                            else:
                                if last_e is not None:
                                    delta = e_val - last_e
                                    if delta > 0:
                                        total_e += delta
                                last_e = e_val

                    if need_time_estimate:
                        x_match = re.search(r'X([-0-9.]+)', line)
                        y_match = re.search(r'Y([-0-9.]+)', line)
                        z_match = re.search(r'Z([-0-9.]+)', line)
                        f_match = re.search(r'F([-0-9.]+)', line)

                        x = float(x_match.group(1)) if x_match else prev_x
                        y = float(y_match.group(1)) if y_match else prev_y
                        z = float(z_match.group(1)) if z_match else prev_z
                        feedrate_val = float(f_match.group(1)) if f_match else prev_f

                        dist = ((x - prev_x)**2 + (y - prev_y)**2 + (z - prev_z)**2) ** 0.5
                        if dist > 0.01:
                            distance_sum += dist
                            feedrate_sum += feedrate_val
                            move_count += 1

                        prev_x, prev_y, prev_z = x, y, z
                        prev_f = feedrate_val

                chunk_start += chunk_size

        if found_moves:
            self.filament_length_mm = total_e

        if need_time_estimate and move_count > 0 and feedrate_sum > 0:
            avg_f = feedrate_sum / move_count
            if avg_f > 0:
                time_minutes = (distance_sum / avg_f) * 60
                self.print_time_seconds = int(time_minutes * 60)
                if self.print_time_seconds < 60:
                    self.print_time_seconds = int(self.filament_length_mm / 5)

    def _count_layers(self, header, tail):
        content = header
        if tail:
            content = header + '\n' + tail
        max_layer = 0
        for match in re.finditer(r';LAYER:(\d+)', content):
            try:
                layer_num = int(match.group(1))
                if layer_num > max_layer:
                    max_layer = layer_num
            except:
                pass
        if max_layer == 0:
            match = re.search(r';\s*total\s+layer\s+number:\s*(\d+)', content, re.IGNORECASE)
            if match:
                max_layer = int(match.group(1))
        self.layer_count = max_layer

    def _calculate_weight(self):
        if self.filament_weight_g > 0:
            return self.filament_weight_g
        length_mm = self.filament_length_mm
        if length_mm <= 0:
            return 0
        radius_mm = self.filament_diameter / 2.0
        cross_section = 3.14159 * (radius_mm ** 2)
        volume_mm3 = length_mm * cross_section
        density = self.filament_density if self.filament_density > 0 else 0.00124
        density_g_mm3 = density / 1000 if density > 1 else density
        weight_g = volume_mm3 * density_g_mm3
        return weight_g

    def get_print_time_string(self):
        seconds = self.print_time_seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f'{hours}h {minutes:02d}min'
        return f'{minutes} min'


def extract_thumbnail_from_gcode(filepath):
    parser = GCodeParser(filepath)
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read(512 * 1024)
        parser._extract_thumbnail(content)
        if parser.thumbnail_data and parser.thumbnail_size:
            from PIL import Image as PILImage
            import io
            return PILImage.open(io.BytesIO(parser.thumbnail_data))
    except Exception:
        pass
    return None
