import zipfile
import xml.etree.ElementTree as ET
import json
import math
import os

_3MF_NS = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
_PROD_NS = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
_BBL_NS = 'http://schemas.bambulab.com/package/2021'

VOLUMETRIC_SPEEDS = {
    'PLA': 12, 'PLA+': 12, 'PETG': 9, 'ABS': 9,
    'TPU': 5, 'NYLON': 7, 'PC': 7, 'ASA': 9,
    'PP': 8, 'PEEK': 3, 'PEKK': 3, 'HIPS': 10,
    'PVA': 8, 'PVB': 10,
}


def _parse_transform(transform_str):
    parts = list(map(float, transform_str.split()))
    return parts


def _apply_transform(v, m):
    x, y, z = v
    return (
        m[0]*x + m[4]*y + m[8]*z + (m[12] if len(m) > 12 else 0),
        m[1]*x + m[5]*y + m[9]*z + (m[13] if len(m) > 12 else 0),
        m[2]*x + m[6]*y + m[10]*z + (m[14] if len(m) > 12 else 0),
    )


def _mesh_volume(vertices, triangles):
    volume = 0.0
    for v0, v1, v2 in triangles:
        p1 = vertices[v0]
        p2 = vertices[v1]
        p3 = vertices[v2]
        volume += (
            p1[0] * (p2[1] * p3[2] - p3[1] * p2[2]) +
            p2[0] * (p3[1] * p1[2] - p1[1] * p3[2]) +
            p3[0] * (p1[1] * p2[2] - p2[1] * p1[2])
        )
    return abs(volume) / 6.0


def _read_metadata_configs(z):
    filament_configs = []
    machine_config = {}
    process_config = {}
    model_settings = None
    project_settings = {}
    filament_sequence = None

    for name in z.namelist():
        base = os.path.basename(name)
        try:
            content = z.read(name).decode('utf-8', errors='replace')
        except:
            continue

        if name.endswith('.config'):
            if base.startswith('filament_settings_'):
                try:
                    filament_configs.append(json.loads(content))
                except:
                    pass
            elif base.startswith('machine_settings_'):
                try:
                    machine_config = json.loads(content)
                except:
                    pass
            elif base.startswith('process_settings_'):
                try:
                    process_config = json.loads(content)
                except:
                    pass
            elif base == 'project_settings.config':
                try:
                    project_settings = json.loads(content)
                except:
                    pass
            elif base == 'model_settings.config':
                try:
                    model_settings = ET.fromstring(content)
                except:
                    pass
        elif name == 'Metadata/filament_sequence.json':
            try:
                filament_sequence = json.loads(content)
            except:
                pass

    return filament_configs, machine_config, process_config, model_settings, project_settings, filament_sequence


def _get_first_val(obj, keys, default=''):
    for k in keys:
        v = obj.get(k)
        if v is None:
            continue
        if isinstance(v, list) and len(v) > 0 and v[0]:
            return v[0]
        if isinstance(v, str) and v:
            return v
    return default


def _detect_used_colors(model_settings, project_settings, filament_configs):
    used_extruders = set()
    if model_settings is not None:
        for obj in model_settings.findall('object'):
            for meta in obj.findall('metadata'):
                if meta.get('key') == 'extruder':
                    val = meta.get('value', '1')
                    used_extruders.add(val)
            for part in obj.findall('part'):
                for meta in part.findall('metadata'):
                    if meta.get('key') == 'extruder':
                        val = meta.get('value', '1')
                        used_extruders.add(val)

    paint_info = project_settings.get('paint_info', [])
    if isinstance(paint_info, str):
        try:
            import json as _json
            paint_info = _json.loads(paint_info)
        except:
            paint_info = []

    if used_extruders == {'1'} or not used_extruders:
        if len(filament_configs) > 0:
            return set(str(i + 1) for i in range(len(filament_configs)))
        raw = project_settings.get('filament_colour', project_settings.get('filament_settings_id', []))
        if isinstance(raw, list) and len(raw) > 0:
            return set(str(i + 1) for i in range(len(raw)))
        return {'1'}
    else:
        return used_extruders


def _extract_filament_info(project_settings, filament_configs, model_settings):
    used_set = _detect_used_colors(model_settings, project_settings, filament_configs)
    used_indices = set()
    for v in used_set:
        try:
            used_indices.add(int(v) - 1)
        except:
            pass
    used_count = len(used_indices)
    if used_count < 1:
        used_count = 1
        used_indices = {0}

    num_filament_configs = len(filament_configs)

    raw_names = project_settings.get('filament_settings_id', [])
    raw_colours = project_settings.get('filament_colour', [])
    raw_densities = project_settings.get('filament_density', [])
    raw_types = project_settings.get('filament_type', [])
    raw_vendors = project_settings.get('filament_vendor', [])
    raw_costs = project_settings.get('filament_cost', [])
    raw_diameters = project_settings.get('filament_diameter', [])
    raw_minimal_purge = project_settings.get('filament_minimal_purge_on_wipe_tower', [])
    raw_prime_volumes = project_settings.get('filament_prime_volume', [])
    raw_max_vol_speed = project_settings.get('filament_max_volumetric_speed', [])

    array_len = max(
        len(raw_names), len(raw_colours), len(raw_densities),
        len(raw_types), len(raw_vendors), len(raw_costs),
        len(raw_diameters), len(raw_minimal_purge),
        len(raw_prime_volumes), len(raw_max_vol_speed)
    )

    num = max(num_filament_configs, array_len, max(used_indices) + 1 if used_indices else 1)
    if num > 12:
        num = 12

    all_filaments = []
    for i in range(num):
        has_cfg = i < num_filament_configs
        cfg = filament_configs[i] if has_cfg else {}

        name = raw_names[i] if i < len(raw_names) else ''
        if not name and has_cfg:
            name = _get_first_val(cfg, ['filament_settings_id', 'name'], '')

        colour = raw_colours[i] if i < len(raw_colours) else ''
        if not colour and has_cfg:
            colour = _get_first_val(cfg, ['filament_colour', 'colour'], '')

        density = 1.24
        if has_cfg:
            try:
                density = float(_get_first_val(cfg, ['filament_density', 'density'], '1.24'))
            except:
                pass
        elif i < len(raw_densities):
            try:
                density = float(raw_densities[i])
            except:
                pass

        ftype = raw_types[i] if i < len(raw_types) else ''
        if not ftype and has_cfg:
            ftype = _get_first_val(cfg, ['filament_type', 'type'], '')

        vendor = raw_vendors[i] if i < len(raw_vendors) else ''
        if not vendor and has_cfg:
            vendor = _get_first_val(cfg, ['filament_vendor', 'vendor'], '')

        cost = 0.0
        if has_cfg:
            try:
                cost = float(_get_first_val(cfg, ['filament_cost', 'cost'], '0'))
            except:
                pass
        elif i < len(raw_costs):
            try:
                cost = float(raw_costs[i])
            except:
                pass

        diameter = 1.75
        if has_cfg:
            try:
                diameter = float(_get_first_val(cfg, ['filament_diameter', 'diameter'], '1.75'))
            except:
                pass
        elif i < len(raw_diameters):
            try:
                diameter = float(raw_diameters[i])
            except:
                pass

        purge = 15.0
        if i < len(raw_minimal_purge):
            try:
                purge = float(raw_minimal_purge[i])
            except:
                pass
        elif has_cfg:
            try:
                purge = float(_get_first_val(cfg, ['filament_minimal_purge_on_wipe_tower'], '15'))
            except:
                pass

        prime_vol = 45.0
        if i < len(raw_prime_volumes):
            try:
                prime_vol = float(raw_prime_volumes[i])
            except:
                pass
        elif has_cfg:
            try:
                prime_vol = float(_get_first_val(cfg, ['filament_prime_volume'], '45'))
            except:
                pass

        vol_speed = None
        if i < len(raw_max_vol_speed):
            try:
                vol_speed = float(raw_max_vol_speed[i])
            except:
                pass
        elif has_cfg:
            try:
                vol_speed = float(_get_first_val(cfg, ['filament_max_volumetric_speed'], '0'))
                if vol_speed == 0:
                    vol_speed = None
            except:
                pass

        all_filaments.append({
            'name': name,
            'colour': colour,
            'density': density,
            'type': ftype,
            'vendor': vendor,
            'cost_per_kg': cost,
            'diameter': diameter,
            'minimal_purge_mm3': purge,
            'prime_volume_mm3': prime_vol,
            'max_volumetric_speed': vol_speed,
        })

    filaments = [all_filaments[i] for i in range(len(all_filaments)) if i in used_indices]
    return filaments, used_count


def _extract_printer_info(project_settings, machine_config):
    info = {}

    area = project_settings.get('printable_area', '')
    if isinstance(area, str):
        area = [area]
    height = project_settings.get('printable_height', '')
    nozzle_diam = project_settings.get('nozzle_diameter', ['0.4'])
    bed_type = project_settings.get('curr_bed_type', '')
    model = project_settings.get('printer_model', '')
    settings_id = project_settings.get('printer_settings_id', '')
    variant = project_settings.get('printer_variant', '')

    info['printable_area'] = area
    try:
        info['printable_height'] = float(height)
    except:
        info['printable_height'] = 250.0
    try:
        info['nozzle_diameter'] = float(nozzle_diam[0]) if isinstance(nozzle_diam, list) else float(nozzle_diam)
    except:
        info['nozzle_diameter'] = 0.4
    info['bed_type'] = bed_type
    info['model'] = model
    info['settings_id'] = settings_id
    info['variant'] = variant

    if machine_config:
        mid = machine_config.get('printer_settings_id', machine_config.get('name', ''))
        if isinstance(mid, list):
            mid = mid[0] if mid else ''
        if not info['settings_id']:
            info['settings_id'] = mid
        info['machine_name'] = mid

        if not info['model']:
            pm = machine_config.get('printer_model', '')
            if isinstance(pm, list):
                pm = pm[0] if pm else ''
            info['model'] = pm

        if not info['printable_area'] or info['printable_area'] == ['']:
            ma = machine_config.get('printable_area', info['printable_area'])
            if isinstance(ma, list):
                info['printable_area'] = ma

        if info['printable_height'] is None or info['printable_height'] == '':
            mh = machine_config.get('printable_height', info['printable_height'])
            if isinstance(mh, (str, int, float)):
                try:
                    info['printable_height'] = float(mh)
                except:
                    pass

        nd = machine_config.get('nozzle_diameter', ['0.4'])
        if isinstance(nd, list):
            try:
                info['nozzle_diameter'] = float(nd[0])
            except:
                pass
        elif isinstance(nd, str):
            try:
                info['nozzle_diameter'] = float(nd)
            except:
                pass

        if not info['variant']:
            pv = machine_config.get('printer_variant', '')
            if isinstance(pv, list):
                pv = pv[0] if pv else ''
            info['variant'] = pv

    return info


def _get_prime_tower_info(project_settings):
    enabled = project_settings.get('enable_prime_tower', '1')
    if isinstance(enabled, str):
        enabled = enabled == '1'
    width = 30.0
    try:
        width = float(project_settings.get('prime_tower_width', 30))
    except:
        pass
    x_pos = 0
    y_pos = 0
    x_raw = project_settings.get('wipe_tower_x', ['0'])
    y_raw = project_settings.get('wipe_tower_y', ['0'])
    if isinstance(x_raw, list) and len(x_raw) > 0:
        try:
            x_pos = float(x_raw[0])
        except:
            pass
    if isinstance(y_raw, list) and len(y_raw) > 0:
        try:
            y_pos = float(y_raw[0])
        except:
            pass
    return {
        'enabled': enabled,
        'width': width,
        'x': x_pos,
        'y': y_pos,
    }


def _estimate_purge_waste(filaments, used_count, project_settings, printer_info, estimated_height, layer_height):
    if used_count <= 1:
        return {
            'waste_mm3': 0.0,
            'waste_grams': 0.0,
            'waste_grams_per_color': [0.0] * used_count if used_count >= 1 else [],
            'color_changes': 0,
            'prime_tower_volume_mm3': 0.0,
            'method': 'no_color_changes',
        }

    flush_matrix = project_settings.get('flush_volumes_matrix', [])
    flush_vector = project_settings.get('flush_volumes_vector', [])
    minimal_purges = [f['minimal_purge_mm3'] for f in filaments[:used_count]]
    prime_vols = [f['prime_volume_mm3'] for f in filaments[:used_count]]
    prime_volume_per_change = sum(prime_vols) / len(prime_vols) if prime_vols else 45.0
    minimal_purge_per_change = sum(minimal_purges) / len(minimal_purges) if minimal_purges else 15.0

    prime_tower_info = _get_prime_tower_info(project_settings)
    prime_tower_vol = 0.0
    if prime_tower_info['enabled']:
        pw = prime_tower_info['width']
        prime_tower_vol = pw * pw * estimated_height

    total_mm3 = 0.0

    color_changes_per_layer = used_count - 1
    total_layers = max(1, int(estimated_height / layer_height))

    if flush_matrix and len(flush_matrix) >= used_count * used_count:
        row_size = int(math.sqrt(len(flush_matrix)))
        transitions_per_layer = used_count - 1
        total_transitions = transitions_per_layer * total_layers
        avg_flush_per_transition = 0.0
        count_pairs = 0
        for from_i in range(used_count):
            for to_j in range(used_count):
                if from_i != to_j:
                    idx = from_i * row_size + to_j
                    if idx < len(flush_matrix):
                        try:
                            val = float(flush_matrix[idx])
                            if val > 0:
                                avg_flush_per_transition += val
                                count_pairs += 1
                        except:
                            pass
        if count_pairs > 0:
            avg_flush_per_transition /= count_pairs
            total_mm3 = avg_flush_per_transition * total_transitions + prime_tower_vol
            avg_density = sum(f['density'] for f in filaments[:used_count]) / used_count
            waste_per_color_g = [total_mm3 * avg_density / 1000.0 / used_count] * used_count
            return {
                'waste_mm3': total_mm3,
                'waste_grams': total_mm3 * avg_density / 1000.0,
                'waste_grams_per_color': waste_per_color_g,
                'color_changes': total_transitions,
                'prime_tower_volume_mm3': prime_tower_vol,
                'method': 'flush_volumes_matrix',
            }

    color_changes_per_layer = used_count - 1
    total_layers = max(1, int(estimated_height / layer_height))
    total_changes = color_changes_per_layer * total_layers
    waste_per_change = minimal_purge_per_change + prime_volume_per_change
    total_waste_mm3 = total_changes * waste_per_change + prime_tower_vol

    avg_density = sum(f['density'] for f in filaments[:used_count]) / used_count

    waste_per_color = []
    for i in range(used_count):
        changes_for_this = color_changes_per_layer * total_layers
        waste_per_color.append(changes_for_this * waste_per_change * avg_density / 1000.0 / used_count)

    return {
        'waste_mm3': total_waste_mm3,
        'waste_grams': total_waste_mm3 * avg_density / 1000.0,
        'waste_grams_per_color': [float(w) for w in waste_per_color],
        'color_changes': total_changes,
        'prime_tower_volume_mm3': prime_tower_vol,
        'method': 'estimated',
    }


def _find_object_by_id(sub_root, obj_id, ns):
    for obj in sub_root.findall('.//m:object', ns):
        if obj.get('id') == str(obj_id):
            return obj
    for obj in sub_root.findall('.//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}object'):
        if obj.get('id') == str(obj_id):
            return obj
    for obj in sub_root.findall('.//object'):
        if obj.get('id') == str(obj_id):
            return obj
    return None


def _collect_mesh(obj_elem, ns, transform=None):
    verts = []
    tris = []
    mesh = obj_elem.find('m:mesh', ns)
    if mesh is None:
        mesh = obj_elem.find('{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}mesh')
    if mesh is None:
        mesh = obj_elem.find('mesh')
    if mesh is not None:
        for verts_elem, tris_elem, vert_tag, tri_tag in [
            (mesh.find('m:vertices', ns), mesh.find('m:triangles', ns), 'm:vertex', 'm:triangle'),
            (mesh.find('{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}vertices'), mesh.find('{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}triangles'), '{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}vertex', '{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}triangle'),
            (mesh.find('vertices'), mesh.find('triangles'), 'vertex', 'triangle'),
        ]:
            if verts_elem is not None and tris_elem is not None:
                for v in verts_elem.findall(vert_tag):
                    verts.append((float(v.get('x', '0')), float(v.get('y', '0')), float(v.get('z', '0'))))
                for t in tris_elem.findall(tri_tag):
                    tris.append((int(t.get('v1')), int(t.get('v2')), int(t.get('v3'))))
                break
    if transform:
        verts = [_apply_transform(v, transform) for v in verts]
    return verts, tris


def _scan_all_xml_for_mesh(z):
    object_meshes = {}
    for name in z.namelist():
        if not name.endswith('.model'):
            continue
        try:
            content = z.read(name).decode('utf-8')
            xml_root = ET.fromstring(content)
            ns = {'m': _3MF_NS}
            raw_objs = xml_root.findall('.//m:object', ns)
            for obj in raw_objs:
                oid = obj.get('id', '0')
                mesh = obj.find('m:mesh', ns)
                if mesh is None:
                    continue
                verts_elem = mesh.find('m:vertices', ns)
                tris_elem = mesh.find('m:triangles', ns)
                if verts_elem is None or tris_elem is None:
                    continue
                verts = []
                tris = []
                for v in verts_elem.findall('m:vertex', ns):
                    verts.append((float(v.get('x', '0')), float(v.get('y', '0')), float(v.get('z', '0'))))
                for t in tris_elem.findall('m:triangle', ns):
                    tris.append((int(t.get('v1')), int(t.get('v2')), int(t.get('v3'))))
                if verts and tris:
                    if oid not in object_meshes:
                        object_meshes[oid] = (verts, tris)
        except:
            pass
    return object_meshes if object_meshes else None


def _is_scaled_component(trans):
    if trans is None:
        return False
    if len(trans) >= 12:
        return abs(trans[0]) < 0.5 or abs(trans[4]) < 0.5 or abs(trans[8]) < 0.5
    return False


def _collect_plates(root, ns, sub_models, plate_object_ids=None):
    object_meshes = {}
    model_meshes = {}
    objs = root.findall('.//m:object', ns)
    if not objs:
        objs = root.findall('.//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}object')
    if not objs:
        objs = root.findall('.//object')

    for obj in objs:
        obj_id = obj.get('id')
        find_comp = obj.find('m:components', ns)
        if find_comp is None:
            find_comp = obj.find('{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}components')
        if find_comp is None:
            find_comp = obj.find('components')
        if find_comp is not None:
            comps = find_comp.findall('m:component', ns)
            if not comps:
                comps = find_comp.findall('{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component')
            if not comps:
                comps = find_comp.findall('component')
            for comp in comps:
                c_obj_id = comp.get('objectid')
                trans_str = comp.get('transform')
                trans = _parse_transform(trans_str) if trans_str else None
                is_scaled = _is_scaled_component(trans)
                path = comp.get('{%s}path' % _PROD_NS, '')
                path = path.lstrip('/') if path else ''
                found = False
                if path and path in sub_models:
                    sub_obj = _find_object_by_id(sub_models[path], c_obj_id, ns)
                    if sub_obj is not None:
                        _v, _t = _collect_mesh(sub_obj, ns, trans)
                        if _v:
                            if obj_id not in object_meshes:
                                object_meshes[obj_id] = ([], [])
                            ov, ot = object_meshes[obj_id]
                            off = len(ov)
                            ov.extend(_v)
                            ot.extend([(a+off, b+off, c+off) for (a,b,c) in _t])
                            if not is_scaled:
                                if obj_id not in model_meshes:
                                    model_meshes[obj_id] = ([], [])
                                mv, mt = model_meshes[obj_id]
                                moff = len(mv)
                                mv.extend(_v)
                                mt.extend([(a+moff, b+moff, c+moff) for (a,b,c) in _t])
                            found = True
                if not found:
                    for sm_name, sm_root in sub_models.items():
                        sub_obj = _find_object_by_id(sm_root, c_obj_id, ns)
                        if sub_obj is not None:
                            _v, _t = _collect_mesh(sub_obj, ns, trans)
                            if _v:
                                if obj_id not in object_meshes:
                                    object_meshes[obj_id] = ([], [])
                                ov, ot = object_meshes[obj_id]
                                off = len(ov)
                                ov.extend(_v)
                                ot.extend([(a+off, b+off, c+off) for (a,b,c) in _t])
                                if not is_scaled:
                                    if obj_id not in model_meshes:
                                        model_meshes[obj_id] = ([], [])
                                    mv, mt = model_meshes[obj_id]
                                    moff = len(mv)
                                    mv.extend(_v)
                                    mt.extend([(a+moff, b+moff, c+moff) for (a,b,c) in _t])
                            break
        else:
            _v, _t = _collect_mesh(obj, ns)
            if _v:
                object_meshes[obj_id] = (_v, _t)
                model_meshes[obj_id] = (_v, _t)

    vector_count = sum(len(v) for v, t in object_meshes.values())
    triangle_count = sum(len(list(t)) for v, t in object_meshes.values())
    if vector_count == 0 or triangle_count == 0:
        return None

    all_verts = []
    all_tris = []
    model_verts = []
    model_tris = []
    vert_offset = 0
    model_offset = 0
    build_items = root.findall('.//m:item', ns)
    if not build_items:
        build_items = root.findall('.//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}item')
    if not build_items:
        build_items = root.findall('.//item')
    for build_item in build_items:
        obj_id = build_item.get('objectid')
        # Skip if this build item is not on the requested plate
        if plate_object_ids is not None and obj_id not in plate_object_ids:
            continue
        trans_str = build_item.get('transform')
        trans = _parse_transform(trans_str) if trans_str else [1,0,0,0, 0,1,0,0, 0,0,1,0]
        if obj_id in object_meshes:
            _v, _t = object_meshes[obj_id]
            _v = [_apply_transform(p, trans) for p in _v]
            all_verts.extend(_v)
            all_tris.extend([(a+vert_offset, b+vert_offset, c+vert_offset) for (a,b,c) in _t])
            vert_offset += len(_v)
        if obj_id in model_meshes:
            _mv, _mt = model_meshes[obj_id]
            _mv = [_apply_transform(p, trans) for p in _mv]
            model_verts.extend(_mv)
            model_tris.extend([(a+model_offset, b+model_offset, c+model_offset) for (a,b,c) in _mt])
            model_offset += len(_mv)

    if not all_verts or not all_tris:
        return None

    return {
        'total': (all_verts, all_tris),
        'model': (model_verts, model_tris) if model_verts else (all_verts, all_tris),
    }


def estimate_from_3mf(filepath, filament_density=1.24, material='PLA',
                      layer_height=0.2, nozzle_diameter=0.4, plate_id=None):
    try:
        with zipfile.ZipFile(filepath) as z:
            zip_names = z.namelist()

            model_xml = None
            model_name = None
            for name in zip_names:
                if name.endswith('.model'):
                    try:
                        model_xml = z.read(name).decode('utf-8')
                        model_name = name
                        break
                    except:
                        pass
            if model_xml is None:
                return {'error': 'No .model found in 3MF'}

            sub_models = {}
            for name in zip_names:
                if name != model_name and name.endswith('.model'):
                    try:
                        sub_models[name] = ET.fromstring(z.read(name).decode('utf-8'))
                    except:
                        pass

            filament_configs, machine_cfg, process_cfg, model_settings, project_settings, filament_seq = \
                _read_metadata_configs(z)
    except Exception as e:
        return {'error': f'Cannot read 3MF: {e}'}

    ns = {'m': _3MF_NS, 'b': _BBL_NS, 'p': _PROD_NS}
    root = ET.fromstring(model_xml)

    filaments, used_count = _extract_filament_info(project_settings, filament_configs, model_settings)
    printer_info = _extract_printer_info(project_settings, machine_cfg)

    # Resolve plate_id → object_id mapping from model_settings.config
    plate_object_ids = None
    if plate_id is not None and model_settings is not None:
        plate_object_ids = set()
        for obj in model_settings.findall('object'):
            for meta in obj.findall('metadata'):
                if meta.get('key') == 'plate_id' and meta.get('value') == str(plate_id):
                    plate_object_ids.add(obj.get('id'))
        for obj in model_settings.findall('object'):
            for meta in obj.findall('metadata'):
                if meta.get('key') == 'extruder':
                    pass  # also match extruder assignments
        # Fallback: use nested object structure
        if not plate_object_ids:
            for obj in model_settings.findall('object'):
                for part in obj.findall('part'):
                    for meta in part.findall('metadata'):
                        if meta.get('key') == 'plate_id' and meta.get('value') == str(plate_id):
                            plate_object_ids.add(obj.get('id'))
        # Last resort: use plate_id from Metadata/plate_id.json or similar
        if not plate_object_ids:
            # Try reading build_plate mapping from project_settings
            plate_map = project_settings.get('plate_object_id_map', {})
            if plate_map:
                for k, v in plate_map.items():
                    if str(v) == str(plate_id):
                        plate_object_ids.add(k)

    mesh_result = _collect_plates(root, ns, sub_models, plate_object_ids)

    if mesh_result is None:
        try:
            with zipfile.ZipFile(filepath) as z:
                fallback = _scan_all_xml_for_mesh(z)
                if fallback:
                    all_verts = []
                    all_tris = []
                    vo = 0
                    for oid, (v, t) in fallback.items():
                        all_verts.extend(v)
                        all_tris.extend([(a+vo, b+vo, c+vo) for (a,b,c) in t])
                        vo += len(v)
                    mesh_result = {'total': (all_verts, all_tris), 'model': (all_verts, all_tris)}
        except:
            pass

    if mesh_result is None:
        return {'error': 'No mesh data found in 3MF', 'filaments': filaments, 'color_count': used_count}

    all_verts, all_tris = mesh_result['total']
    model_verts, model_tris = mesh_result['model']

    volume_mm3 = _mesh_volume(all_verts, all_tris)
    if volume_mm3 <= 0:
        return {'error': 'Invalid mesh volume', 'filaments': filaments, 'color_count': used_count}

    avg_density = sum(f['density'] for f in filaments[:used_count]) / used_count if used_count > 0 else filament_density
    # Prefer process_settings density when available (overrides filament average)
    try:
        d = float(_get_first_val(process_config, ['filament_density', 'density'], '0'))
        if d > 0:
            avg_density = d
    except:
        pass

    z_vals = [v[2] for v in model_verts]
    height = max(z_vals) - min(z_vals) if z_vals else 10
    lh = layer_height
    lh_from_config = project_settings.get('layer_height', layer_height)
    if isinstance(lh_from_config, (int, float)):
        lh = float(lh_from_config)
    elif isinstance(lh_from_config, str):
        try:
            lh = float(lh_from_config)
        except:
            pass
    layer_height = lh

    purge_waste = _estimate_purge_waste(filaments, used_count, project_settings, printer_info, height, layer_height)

    total_volume_waste_mm3 = volume_mm3 + purge_waste['waste_mm3']

    material_for_weight = material
    if used_count > 0 and filaments[0]['type']:
        material_for_weight = filaments[0]['type']

    total_weight_with_waste = total_volume_waste_mm3 * avg_density / 1000.0 if avg_density else total_volume_waste_mm3 * filament_density / 1000.0
    model_weight = volume_mm3 * avg_density / 1000.0

    material_upper = (filaments[0]['type'] if used_count > 0 and filaments[0]['type'] else material).upper()
    vol_speed = VOLUMETRIC_SPEEDS.get(material_upper, 10)
    if used_count > 0 and filaments[0]['max_volumetric_speed']:
        vol_speed = filaments[0]['max_volumetric_speed']
    time_by_volume = int(volume_mm3 / vol_speed)

    extrusion_width = printer_info['nozzle_diameter'] * 1.1
    cross_section = layer_height * extrusion_width
    extrusion_length = volume_mm3 / cross_section if cross_section > 0 else 0
    time_by_extrusion = extrusion_length / 60
    travel_factor = 1.2

    time_seconds = max(time_by_volume, int(time_by_extrusion * travel_factor))
    layer_count = max(1, int(height / layer_height))

    # Extract fill density from process config
    fill_density_pct = 20.0
    fill_raw = _get_first_val(process_config, ['sparse_infill_density', 'fill_density'], '20%')
    fill_pct = fill_raw
    if isinstance(fill_raw, str) and '%' in fill_raw:
        try:
            fill_pct = float(fill_raw.replace('%', ''))
        except:
            pass
    elif isinstance(fill_raw, (int, float)):
        fill_pct = float(fill_raw)
    fill_density = fill_pct / 100.0 if fill_pct > 1 else fill_pct

    result = {
        'volume_mm3': volume_mm3,
        'estimated_weight_grams': round(model_weight, 2),
        'weight_with_waste_grams': round(total_weight_with_waste, 2),
        'print_time_seconds': time_seconds,
        'layer_count': layer_count,
        'height_mm': round(height, 2),
        'filament_length_mm': round(extrusion_length, 2) if extrusion_length > 0 else 0,
        'color_count': used_count,
        'filaments': filaments[:used_count],
        'printer': printer_info,
        'purge_waste': purge_waste,
        'layer_height': layer_height,
        'fill_density': fill_density,
        'fill_density_pct': f'{fill_pct:.0f}%',
    }

    return result


def list_namelist_from_zip(filepath):
    try:
        with zipfile.ZipFile(filepath) as z:
            return z.namelist()
    except:
        return []


def extract_thumbnail_from_3mf(filepath):
    try:
        with zipfile.ZipFile(filepath) as z:
            names = z.namelist()
            png_candidates = [n for n in names if n.endswith('.png') and 'Metadata/' in n]
            png_candidates.sort()
            if png_candidates:
                from PIL import Image
                import io
                return Image.open(io.BytesIO(z.read(png_candidates[0])))
    except:
        pass
    return None
