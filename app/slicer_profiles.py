"""Profile discovery for AnycubicSlicerNext, BambuStudio, OrcaSlicer."""
import os, json, glob, re
from pathlib import Path

APPDATA = os.environ.get('APPDATA', '')

SOURCES = [
    {
        'name': 'AnycubicSlicerNext',
        'index': os.path.join(APPDATA, 'AnycubicSlicerNext', 'system', 'Anycubic.json'),
        'base': os.path.join(APPDATA, 'AnycubicSlicerNext', 'system', 'Anycubic'),
        'user': os.path.join(APPDATA, 'AnycubicSlicerNext', 'user', 'Anycubic'),
    },
    {
        'name': 'BambuStudio',
        'index': r'C:\Program Files\Bambu Studio\resources\profiles\BBL.json',
        'base': r'C:\Program Files\Bambu Studio\resources\profiles\BBL',
        'user': os.path.join(APPDATA, 'BambuStudio', 'user'),
    },
    {
        'name': 'OrcaSlicer',
        'index': r'C:\Program Files\OrcaSlicer\resources\profiles\Anycubic.json',
        'base': r'C:\Program Files\OrcaSlicer\resources\profiles\Anycubic',
        'user': os.path.join(APPDATA, 'OrcaSlicer', 'user'),
        'extra_bases': [
            r'C:\Program Files\OrcaSlicer\resources\profiles\BBL',
            r'C:\Program Files\OrcaSlicer\resources\profiles\Creality',
            r'C:\Program Files\OrcaSlicer\resources\profiles\Prusa',
        ],
    },
]


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return None


class ProfileDB:
    def __init__(self):
        self.printers = []     # list of {name, model_id, nozzle, family, source}
        self.filaments = []    # list of {name, type, vendor, density, diameter, flow_ratio, cost, source}
        self.processes = []    # list of {name, layer_height, source, printer_compat}
        self._printer_map = {}  # name -> list of profile paths
        self._filament_map = {}
        self._process_map = {}
        self._printer_machine_paths = {}
        self._scan_all()

    def _scan_all(self):
        for source in SOURCES:
            index = _load_json(source['index'])
            if not index:
                continue
            base = source['base']
            source_name = source['name']

            machine_dir = os.path.join(base, 'machine')
            filament_dir = os.path.join(base, 'filament')
            process_dir = os.path.join(base, 'process')

            self._scan_machines(machine_dir, source_name)
            self._scan_filaments(filament_dir, source_name)
            self._scan_processes(process_dir, source_name)

            # Also scan user directory for custom profiles
            user_base = source.get('user', '')
            if user_base and os.path.isdir(user_base):
                for sub in ('machine', 'filament', 'process'):
                    d = os.path.join(user_base, sub)
                    if os.path.isdir(d):
                        if sub == 'machine':
                            self._scan_machines(d, source_name + '_user')
                        elif sub == 'filament':
                            self._scan_filaments(d, source_name + '_user')
                        elif sub == 'process':
                            self._scan_processes(d, source_name + '_user')

            # Scan extra bases (e.g., OrcaSlicer BBL, Creality, Prusa)
            for extra in source.get('extra_bases', []):
                if not os.path.isdir(extra):
                    continue
                for sub in ('machine', 'filament', 'process'):
                    d = os.path.join(extra, sub)
                    if os.path.isdir(d):
                        if sub == 'machine':
                            self._scan_machines(d, source_name)
                        elif sub == 'filament':
                            self._scan_filaments(d, source_name)
                        elif sub == 'process':
                            self._scan_processes(d, source_name)

        self.printers.sort(key=lambda p: p['name'].lower())
        self.filaments.sort(key=lambda f: f['name'].lower())
        self.processes.sort(key=lambda p: (p.get('layer_height', 99), p['name'].lower()))

    def _scan_machines(self, machine_dir, source):
        if not os.path.isdir(machine_dir):
            return
        for fp in sorted(glob.glob(os.path.join(machine_dir, '*.json'))):
            data = _load_json(fp)
            if not data:
                continue
            t = data.get('type', '')
            if t == 'machine_model':
                self.printers.append({
                    'name': data.get('name', ''),
                    'model_id': data.get('model_id', ''),
                    'nozzle': data.get('nozzle_diameter', ''),
                    'family': data.get('family', ''),
                    'source': source,
                })
            elif t == 'machine':
                pname = data.get('name', '')
                model = data.get('printer_model', '')
                variant = data.get('printer_variant', '')
                key = f'{model} {variant} nozzle' if variant else model
                self._printer_map.setdefault(key, []).append(fp)
                self._printer_machine_paths[key] = fp
                # Also index by just the profile name
                self._printer_machine_paths[pname] = fp

    def _scan_filaments(self, filament_dir, source):
        if not os.path.isdir(filament_dir):
            return
        for fp in sorted(glob.glob(os.path.join(filament_dir, '*.json'))):
            data = _load_json(fp)
            if not data or data.get('type') != 'filament':
                continue
            inst = data.get('instantiation', '')
            if inst == 'false':
                continue
            fname = data.get('name', '')
            self.filaments.append({
                'name': fname,
                'type': _first(data, 'filament_type', ''),
                'vendor': _first(data, 'filament_vendor', ''),
                'density': _first(data, 'filament_density', 1.24),
                'diameter': _first(data, 'filament_diameter', 1.75),
                'flow_ratio': _first(data, 'filament_flow_ratio', 1.0),
                'cost': _first(data, 'filament_cost', 0),
                'max_vol_speed': _first(data, 'filament_max_volumetric_speed', 0),
                'nozzle_temp': _first(data, 'nozzle_temperature', 0),
                'bed_temp': _first(data, 'hot_plate_temp', 0),
                'source': source,
            })
            compat = _list(data, 'compatible_printers', [])
            self._filament_map[fname] = {
                'path': fp,
                'compatible_printers': compat,
                'data': data,
            }

    def _scan_processes(self, process_dir, source):
        if not os.path.isdir(process_dir):
            return
        for fp in sorted(glob.glob(os.path.join(process_dir, '*.json'))):
            data = _load_json(fp)
            if not data or data.get('type') != 'process':
                continue
            inst = data.get('instantiation', '')
            if inst == 'false':
                continue
            pname = data.get('name', '')
            lh = _first(data, 'layer_height', None)
            if lh is None:
                # Extract from name like "0.08mm Extra Fine @BBL X1C"
                m = re.match(r'([\d.]+)\s*mm', pname)
                lh = float(m.group(1)) if m else 0.2
            else:
                lh = float(lh)
            self.processes.append({
                'name': pname,
                'layer_height': lh,
                'infill_density': _first(data, 'sparse_infill_density', '20%'),
                'wall_loops': int(_first(data, 'wall_loops', 2)),
                'source': source,
            })
            compat = _list(data, 'compatible_printers', [])
            self._process_map[pname] = {
                'path': fp,
                'compatible_printers': compat,
                'data': data,
                'layer_height': lh,
            }

    def get_printer_profile(self, printer_name, nozzle_diameter=None):
        if printer_name in self._printer_machine_paths:
            return self._printer_machine_paths[printer_name]
        candidates = list(self._printer_machine_paths.keys())
        # Prefer profile matching nozzle diameter
        if nozzle_diameter:
            noz_str = str(nozzle_diameter).replace('.', '_')
            exact = [c for c in candidates if noz_str in c and printer_name.lower() in c.lower()]
            if not exact:
                noz_str = str(nozzle_diameter)
                exact = [c for c in candidates if noz_str in c and printer_name.lower() in c.lower()]
            if exact:
                return self._printer_machine_paths[exact[0]]
        best = _fuzzy_match(printer_name, candidates)
        return self._printer_machine_paths.get(best)

    def get_filament_profiles_for_printer(self, printer_name):
        results = []
        for fname, finfo in self._filament_map.items():
            compat = finfo.get('compatible_printers', [])
            if not compat or any(printer_name in c for c in compat):
                results.append(fname)
        return results

    def get_processes_for_printer(self, printer_name):
        results = []
        for pname, pinfo in self._process_map.items():
            compat = pinfo.get('compatible_printers', [])
            if not compat or any(printer_name in c for c in compat):
                results.append((pname, pinfo['layer_height']))
        return sorted(results, key=lambda x: (x[1], x[0]))

    def get_filament_path(self, filament_name):
        info = self._filament_map.get(filament_name)
        return info['path'] if info else None

    def get_process_path(self, process_name):
        info = self._process_map.get(process_name)
        return info['path'] if info else None

    def list_printers(self):
        return [p['name'] for p in self.printers]

    def list_filaments(self):
        return [f['name'] for f in self.filaments]

    def list_layer_heights(self, printer_name=None):
        if printer_name:
            procs = self.get_processes_for_printer(printer_name)
            return sorted(set(h for _, h in procs))
        return sorted(set(p['layer_height'] for p in self.processes))

    def get_processes_for_layer_height(self, layer_height, printer_name=None):
        results = []
        for pname, pinfo in self._process_map.items():
            if abs(pinfo['layer_height'] - layer_height) < 0.001:
                compat = pinfo.get('compatible_printers', [])
                if not printer_name or not compat or any(printer_name in c for c in compat):
                    results.append(pname)
        return results

    def get_filament_density(self, filament_name):
        for f in self.filaments:
            if f['name'] == filament_name:
                return float(f['density'])
        return 1.24

    def get_filament_data(self, filament_name):
        for f in self.filaments:
            if f['name'] == filament_name:
                return f
        return None


def _first(data, key, default):
    v = data.get(key)
    if v is None:
        return default
    if isinstance(v, list):
        v = v[0] if v else default
    try:
        return float(v)
    except (ValueError, TypeError):
        if isinstance(v, str) and '%' in v:
            try: return float(v.replace('%', ''))
            except: pass
        return v


def _list(data, key, default):
    v = data.get(key, default)
    if isinstance(v, str) and v.startswith('['):
        try: return json.loads(v.replace("'", '"'))
        except: pass
    if not isinstance(v, list):
        return default
    return v


def _fuzzy_match(hint, candidates):
    if not hint or not candidates:
        return None if not candidates else candidates[0]
    hl = hint.lower()
    for c in candidates:
        if hl in c.lower():
            return c
    hw = set(re.split(r'[\s\-_@.]+', hl))
    best, best_score = None, 0
    for c in candidates:
        cl = c.lower()
        cw = set(re.split(r'[\s\-_@.]+', cl))
        score = len(hw & cw)
        if hl in cl:
            score += 10
        if score > best_score:
            best_score = score
            best = c
    return best or candidates[0]
