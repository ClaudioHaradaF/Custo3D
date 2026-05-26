import zipfile
import xml.etree.ElementTree as ET
import os
import math
import time
import tempfile
import re

EPSILON = 0.001
SLICE_TIMEOUT = 600

# ─── VECTOR UTILITIES ────────────────────────────────────────────────────

class V2:
    __slots__ = ('x', 'y')
    def __init__(self, x, y): self.x, self.y = float(x), float(y)
    def __add__(s, o): return V2(s.x + o.x, s.y + o.y)
    def __sub__(s, o): return V2(s.x - o.x, s.y - o.y)
    def __mul__(s, a): return V2(s.x * a, s.y * a)
    def __repr__(s): return f'({s.x:.2f},{s.y:.2f})'
    def dot(s, o): return s.x * o.x + s.y * o.y
    def cross(s, o): return s.x * o.y - s.y * o.x
    def length(s): return math.hypot(s.x, s.y)
    def normalized(s):
        L = s.length()
        return V2(s.x / L, s.y / L) if L > 1e-9 else V2(0, 0)

class V3:
    __slots__ = ('x','y','z')
    def __init__(s, x, y, z): s.x, s.y, s.z = float(x), float(y), float(z)
    def __sub__(s, o): return V3(s.x - o.x, s.y - o.y, s.z - o.z)
    def dot(s, o): return s.x * o.x + s.y * o.y + s.z * o.z
    def cross(s, o): return V3(s.y*o.z - s.z*o.y, s.z*o.x - s.x*o.z, s.x*o.y - s.y*o.x)
    def length(s): return math.hypot(s.x, s.y, s.z)

def point_in_poly(p, poly):
    cnt, n = 0, len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if ((a.y <= p.y < b.y) or (b.y <= p.y < a.y)) and p.x < a.x + (p.y - a.y) / (b.y - a.y + 1e-9) * (b.x - a.x):
            cnt += 1
    return cnt & 1

def poly_area(poly):
    a, n = 0.0, len(poly)
    for i in range(n):
        j = (i + 1) % n
        a += poly[i].x * poly[j].y - poly[j].x * poly[i].y
    return a / 2.0

def poly_bbox(poly):
    xs = [p.x for p in poly]
    ys = [p.y for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))

def simplify_poly(poly, min_d=0.05):
    if len(poly) < 3: return poly
    res = [poly[0]]
    for p in poly[1:]:
        if (p - res[-1]).length() > min_d:
            res.append(p)
    if len(res) > 1 and (res[-1] - res[0]).length() < min_d:
        res.pop()
    return res

def _pd(p, a, b):
    """perpendicular distance from p to line ab"""
    dx = b.x - a.x
    dy = b.y - a.y
    den = dx*dx + dy*dy
    if den < 1e-12: return math.hypot(p.x - a.x, p.y - a.y)
    t = ((p.x - a.x)*dx + (p.y - a.y)*dy) / den
    t = max(0, min(1, t))
    return math.hypot(p.x - (a.x + t*dx), p.y - (a.y + t*dy))

def simplify_rdp(points, epsilon):
    """Ramer-Douglas-Peucker polyline simplification"""
    if len(points) <= 2:
        return points
    dmax = 0
    idx = 0
    end = len(points) - 1
    for i in range(1, end):
        d = _pd(points[i], points[0], points[end])
        if d > dmax:
            idx = i
            dmax = d
    if dmax > epsilon:
        left = simplify_rdp(points[:idx+1], epsilon)
        right = simplify_rdp(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]

def sort_ccw(pts):
    if not pts: return pts
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    return sorted(pts, key=lambda p: math.atan2(p.y - cy, p.x - cx))

def _mesh_volume(vertices, triangles):
    vol = 0.0
    for t in triangles:
        p1 = vertices[t[0]]
        p2 = vertices[t[1]]
        p3 = vertices[t[2]]
        vol += (
            p1.x * (p2.y * p3.z - p3.y * p2.z) +
            p2.x * (p3.y * p1.z - p1.y * p3.z) +
            p3.x * (p1.y * p2.z - p2.y * p1.z)
        )
    return abs(vol) / 6.0


# ─── 3MF PARSER ──────────────────────────────────────────────────────────

_3MF_NS = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'

def _tag(ns, name):
    return '{%s}%s' % (ns, name)

def _collect_mesh(obj_elem, transform=None):
    verts, tris = [], []
    for ns_uri in (_3MF_NS, ''):
        mesh = obj_elem.find(_tag(ns_uri, 'mesh')) if ns_uri else obj_elem.find('mesh')
        if mesh is None: continue
        ve = mesh.find(_tag(ns_uri, 'vertices')) if ns_uri else mesh.find('vertices')
        te = mesh.find(_tag(ns_uri, 'triangles')) if ns_uri else mesh.find('triangles')
        if ve is not None and te is not None:
            for v in (ve.findall(_tag(ns_uri, 'vertex')) if ns_uri else ve.findall('vertex')):
                verts.append((
                    float(v.get('x', '0')), float(v.get('y', '0')), float(v.get('z', '0'))
                ))
            for t in (te.findall(_tag(ns_uri, 'triangle')) if ns_uri else te.findall('triangle')):
                tris.append([
                    int(t.get('v1', '0')), int(t.get('v2', '0')), int(t.get('v3', '0'))
                ])
            break
    if transform and verts:
        verts = [_apply_transform(v, transform) for v in verts]
    return verts, tris

def _parse_transform(s):
    if not s: return None
    parts = s.split()
    nums = [float(p) for p in parts]
    # 3MF uses 4×3 row-major (12 vals, last row implicit 0,0,0,1) or full 4×4 (16 vals)
    if len(nums) == 12:
        # Pad to 16: add implicit '0 0 0 1' as last row
        nums = nums[:3] + [0] + nums[3:6] + [0] + nums[6:9] + [0] + nums[9:12] + [1]
    if len(nums) == 16:
        return nums
    return None

def _apply_transform(v, t):
    x, y, z = v
    return (
        x * t[0] + y * t[4] + z * t[8] + t[12],
        x * t[1] + y * t[5] + z * t[9] + t[13],
        x * t[2] + y * t[6] + z * t[10] + t[14],
    )

def _find_object_by_id(xml_root, obj_id):
    for ns_uri in (_3MF_NS, ''):
        if ns_uri:
            objs = xml_root.findall('.//' + _tag(ns_uri, 'object'))
        else:
            objs = xml_root.findall('.//object')
        for obj in objs:
            if obj.get('id') == obj_id:
                return obj
    return None

def _scan_3mf_volumes(filepath, plate_id=1):
    volumes = []
    materials = {}
    colors = {}
    slicer_config = {}
    has_support = False
    build_items = []

    with zipfile.ZipFile(filepath, 'r') as z:
        names = z.namelist()
        sub_models = {}

        for name in names:
            if name.endswith('.model'):
                try:
                    content = z.read(name).decode('utf-8', errors='replace')
                    xml_root = ET.fromstring(content)
                    if name == '3D/3dmodel.model' or name == '3D/3d.model':
                        pass  # main model
                    else:
                        sub_models[name] = xml_root
                except: pass
            elif name.lower().endswith('.config') or name.lower().endswith('.ini') or name.lower().endswith('.txt') or 'slic3r' in name.lower() or 'bambu' in name.lower():
                try:
                    txt = z.read(name).decode('utf-8', errors='replace')
                    # Try JSON first (BambuStudio/Anycubic use JSON .config files)
                    try:
                        import json
                        data = json.loads(txt)
                        if isinstance(data, dict):
                            for k, v in data.items():
                                if isinstance(v, list):
                                    v = v[0] if v else ''
                                slicer_config[str(k).strip()] = str(v).strip()
                            continue
                    except: pass
                    # Fall back to key=value format (Slic3r/Prusa legacy)
                    for line in txt.split('\n'):
                        line = line.strip()
                        if '=' in line and not line.startswith('#') and not line.startswith('<'):
                            k, v = line.split('=', 1)
                            k = k.strip().strip('"').strip("'")
                            v = v.strip().strip('"').strip("'").rstrip(',')
                            slicer_config[k] = v
                except: pass

        main_xml = None
        for n in names:
            if n == '3D/3dmodel.model' or n == '3D/3d.model' or n.endswith('.model'):
                try:
                    main_xml = ET.fromstring(z.read(n).decode('utf-8', errors='replace'))
                    break
                except: pass
        if main_xml is None:
            return {'error': 'No .model found in 3MF'}

        # Read plate assignments from model_settings.config (Anycubic format)
        plate_objects = {}
        try:
            settings_xml = ET.fromstring(z.read('Metadata/model_settings.config').decode('utf-8', errors='replace'))
            for plate_el in settings_xml.findall('plate'):
                pid = ''
                for meta in plate_el.findall("metadata"):
                    if meta.get('key') == 'plater_id':
                        pid = meta.get('value', '')
                for mi in plate_el.findall('model_instance'):
                    for meta in mi.findall('metadata'):
                        if meta.get('key') == 'object_id':
                            oid = meta.get('value', '')
                            if oid:
                                plate_objects.setdefault(pid, []).append(oid)
        except:
            pass

        # Fallback to BambuStudio plate_*.json format
        if not plate_objects:
            try:
                # Build name→id mapping from model_settings.config
                name_to_id = {}
                try:
                    settings_xml = ET.fromstring(z.read('Metadata/model_settings.config').decode('utf-8', errors='replace'))
                    for obj_el in settings_xml.findall('object'):
                        oid = obj_el.get('id', '')
                        for meta in obj_el.findall("metadata"):
                            if meta.get('key') == 'name':
                                name_to_id[meta.get('value', '')] = oid
                except:
                    pass
                # Parse each plate_*.json
                import json
                for plate_fname in sorted(names):
                    m = re.match(r'Metadata/plate_(\d+)\.json$', plate_fname)
                    if not m:
                        continue
                    pid = m.group(1)
                    try:
                        pdata = json.loads(z.read(plate_fname))
                        for bo in pdata.get('bbox_objects', []):
                            oname = bo.get('name', '')
                            if oname in name_to_id:
                                plate_objects.setdefault(pid, []).append(name_to_id[oname])
                    except:
                        pass
            except:
                pass

        for kw in ('support', 'support_enable', 'generate_support', 'supports', 'enable_support'):
            v = slicer_config.get(kw, '').lower()
            if v in ('1', 'true', 'yes'):
                has_support = True
                break

        for ns_uri in (_3MF_NS, ''):
            if ns_uri:
                objs = main_xml.findall('.//' + _tag(ns_uri, 'object'))
                res_el = main_xml.find(_tag(ns_uri, 'resources'))
                build_el = main_xml.find(_tag(ns_uri, 'build'))
            else:
                objs = main_xml.findall('.//object')
                res_el = main_xml.find('resources')
                build_el = main_xml.find('build')

            if objs:
                # Parse materials/colors from resources
                if res_el is not None:
                    for child in (res_el.findall(_tag(ns_uri, 'basematerials')) if ns_uri else res_el.findall('basematerials')):
                        for b in (child.findall(_tag(ns_uri, 'base')) if ns_uri else child.findall('base')):
                            mid = b.get('id', '')
                            cs = b.get('displaycolor', '').strip('#')
                            r, g, b_, a = 128, 128, 128, 255
                            if len(cs) >= 6:
                                try:
                                    r, g, b_ = int(cs[0:2],16), int(cs[2:4],16), int(cs[4:6],16)
                                    if len(cs) >= 8: a = int(cs[6:8],16)
                                except: pass
                            materials[mid] = (r, g, b_, a)
                    for child in (res_el.findall(_tag(ns_uri, 'colorgroup')) if ns_uri else res_el.findall('colorgroup')):
                        for c in (child.findall(_tag(ns_uri, 'color')) if ns_uri else child.findall('color')):
                            cid = c.get('id', '')
                            cs = c.get('color', '').strip('#')
                            r, g, b_, a = 128,128,128,255
                            if len(cs) >= 6:
                                try:
                                    r, g, b_ = int(cs[0:2],16), int(cs[2:4],16), int(cs[4:6],16)
                                    if len(cs) >= 8: a = int(cs[6:8],16)
                                except: pass
                            colors[cid] = (r, g, b_, a)

                object_meshes = {}
                component_refs = set()
                for obj in objs:
                    obj_id = obj.get('id')
                    comps_el = obj.find(_tag(ns_uri, 'components')) if ns_uri else obj.find('components')
                    if comps_el is not None:
                        comps = comps_el.findall(_tag(ns_uri, 'component')) if ns_uri else comps_el.findall('component')
                        for comp in comps:
                            cid = comp.get('objectid')
                            component_refs.add(cid)
                            trans = _parse_transform(comp.get('transform'))
                            found = False
                            # Try sub-models first
                            for sm_name, sm_root in sub_models.items():
                                sub_obj = _find_object_by_id(sm_root, cid)
                                if sub_obj is not None:
                                    _v, _t = _collect_mesh(sub_obj, trans)
                                    if _v:
                                        if obj_id not in object_meshes:
                                            object_meshes[obj_id] = ([], [])
                                        ov, ot = object_meshes[obj_id]
                                        off = len(ov)
                                        ov.extend(_v)
                                        ot.extend([(a+off, b+off, c+off) for (a,b,c) in _t])
                                        found = True
                                    break
                            if not found:
                                # Try main XML
                                sub_obj = _find_object_by_id(main_xml, cid)
                                if sub_obj is not None:
                                    _v, _t = _collect_mesh(sub_obj, trans)
                                    if _v:
                                        if obj_id not in object_meshes:
                                            object_meshes[obj_id] = ([], [])
                                        ov, ot = object_meshes[obj_id]
                                        off = len(ov)
                                        ov.extend(_v)
                                        ot.extend([(a+off, b+off, c+off) for (a,b,c) in _t])
                    else:
                        _v, _t = _collect_mesh(obj)
                        if _v:
                            object_meshes[obj_id] = (_v, _t)

                # Parse build items — only include the current plate
                target_oids = plate_objects.get(str(plate_id))
                if build_el is not None:
                    items = build_el.findall(_tag(ns_uri, 'item')) if ns_uri else build_el.findall('item')
                    for item in items:
                        oid = item.get('objectid')
                        if target_oids and oid not in target_oids:
                            continue  # skip objects from other plates
                        trans = _parse_transform(item.get('transform'))
                        if oid and oid in object_meshes:
                            _v, _t = object_meshes[oid]
                            if trans:
                                _v = [_apply_transform(p, trans) for p in _v]
                            volumes.append({
                                'id': oid,
                                'name': item.get('name', ''),
                                'vertices': [V3(x, y, z) for x, y, z in _v],
                                'triangles': _t,
                                'color': (128, 128, 128, 255),
                            })
                            build_items.append(oid)

                # Also add any objects not referenced in build or as component children
                for oid, (_v, _t) in object_meshes.items():
                    if oid not in build_items and oid not in component_refs and _v:
                        if target_oids:
                            continue  # skip orphans when filtering by plate
                        volumes.append({
                            'id': oid,
                            'name': '',
                            'vertices': [V3(x, y, z) for x, y, z in _v],
                            'triangles': _t,
                            'color': (128, 128, 128, 255),
                        })

                if volumes:
                    break  # Found volumes with this namespace, stop trying

    # If no volumes found with namespace, try completely without namespace
    if not volumes:
        for n in names:
            if not n.endswith('.model'): continue
            try:
                content2 = z.read(n).decode('utf-8', errors='replace')
                xml_root2 = ET.fromstring(content2)
                for obj in xml_root2.findall('.//object'):
                    oid = obj.get('id', '0')
                    _v, _t = _collect_mesh(obj)
                    if _v:
                        volumes.append({
                            'id': oid,
                            'name': obj.get('name', ''),
                            'vertices': [V3(x, y, z) for x, y, z in _v],
                            'triangles': _t,
                            'color': (128, 128, 128, 255),
                        })
            except: pass

    def cf(k, d):
        try:
            v = slicer_config.get(k)
            if v is not None:
                return float(str(v).replace('%', '').strip())
        except: pass
        return d

    def ci(k, d):
        try:
            v = slicer_config.get(k)
            if v is not None:
                return int(float(str(v).replace('%', '').strip()))
        except: pass
        return d

    nozzle = cf('nozzle_diameter', 0.4)
    lh = cf('layer_height', 0.20)
    ew = nozzle * 1.05
    support_enabled = has_support
    total_mesh_vol = sum(_mesh_volume(v['vertices'], v['triangles']) for v in volumes if v['vertices'])
    has_fill_config = ('fill_density' in slicer_config or 'sparse_infill_density' in slicer_config)
    return {
        'volumes': volumes,
        'total_mesh_vol': total_mesh_vol,
        'materials': materials,
        'colors': colors,
        'slicer_config': slicer_config,
        'layer_height': lh,
        'first_layer_height': cf('first_layer_height',
                               cf('initial_layer_print_height',
                                  cf('first_layer_height_overridden',
                                     min(lh * 1.2, 0.35)))),
        'nozzle_diameter': nozzle,
        'perimeter_extrusion_width': cf('perimeter_extrusion_width', ew),
        'infill_extrusion_width': cf('infill_extrusion_width', ew),
        'perimeter_count': ci('perimeters', ci('wall_loops', 2)),
        'fill_density': cf('fill_density', cf('sparse_infill_density', 20)) / 100,
        'has_fill_config': has_fill_config,
        'fill_pattern': slicer_config.get('fill_pattern', slicer_config.get('top_fill_pattern', 'grid')),
        'infill_pattern_type': (
            slicer_config.get('sparse_infill_pattern', '') or
            slicer_config.get('fill_pattern', '') or
            slicer_config.get('top_fill_pattern', 'grid')
        ),
        'support_enabled': support_enabled,
        'support_filament': ci('support_filament', ci('support_interface_filament', 0)),
        'top_solid_layers': ci('top_solid_layers', ci('solid_top_layers', 4)),
        'bottom_solid_layers': ci('bottom_solid_layers', ci('solid_bottom_layers', 4)),
        'bed_temp': cf('bed_temperature', cf('first_layer_bed_temperature', 60)),
        'nozzle_temp': cf('temperature', cf('nozzle_temperature', 200)),
        'filament_diameter': cf('filament_diameter', 1.75),
        'filament_density': cf('filament_density', 1.24),
        'filament_multiplier': cf('extrusion_multiplier', cf('filament_flow_ratio', 1.0)),
        'retract_length': cf('retract_length', cf('retraction_length', 5)),
        'retract_speed': cf('retract_speed', cf('retraction_speed', 30)) * 60,
        'travel_speed': cf('travel_speed', 130) * 60,
        'perimeter_speed': cf('perimeter_speed', cf('wall_speed', 60)) * 60,
        'infill_speed': cf('infill_speed', cf('sparse_infill_speed', 80)) * 60,
        'solid_infill_speed': cf('solid_infill_speed', cf('internal_solid_infill_speed', 60)) * 60,
        'first_layer_speed': cf('first_layer_speed', 30) * 60,
        'support_speed': cf('support_speed', cf('support_material_speed', 60)) * 60,
        'brim_width': cf('brim_width', cf('skirt_brim_width', 0)),
    }


# ─── TRIANGLE SLICING (OTIMIZADO: sweep Z) ──────────────────────────────

class MeshSlicer:
    def __init__(self, vertices, triangles):
        self.verts = vertices
        self.tris = triangles
        self.tri_z = []
        for t in triangles:
            v = [vertices[i] for i in t]
            zmin = min(p.z for p in v)
            zmax = max(p.z for p in v)
            self.tri_z.append((zmin, zmax, t))
        self.tri_z_sorted = sorted(self.tri_z, key=lambda x: x[0])

    def slice_layer(self, z):
        segs = []
        for zmin, zmax, t in self.tri_z_sorted:
            if z > zmax + EPSILON: continue
            if z < zmin - EPSILON: break
            v = [self.verts[i] for i in t]
            seg = self._tri_plane_inter(v[0], v[1], v[2], z)
            if seg:
                segs.append(seg)
        return segs

    def _tri_plane_inter(self, v0, v1, v2, z):
        d = [p.z - z for p in (v0, v1, v2)]
        above = [i for i, di in enumerate(d) if di > EPSILON]
        below = [i for i, di in enumerate(d) if di < -EPSILON]
        on = [i for i, di in enumerate(d) if abs(di) <= EPSILON]
        if not above or not below: return None
        verts = [v0, v1, v2]
        pts = [(verts[i].x, verts[i].y) for i in on]
        for i in range(3):
            j = (i + 1) % 3
            if d[i] * d[j] < 0:
                t = d[i] / (d[i] - d[j])
                pts.append((
                    verts[i].x + t * (verts[j].x - verts[i].x),
                    verts[i].y + t * (verts[j].y - verts[i].y),
                ))
        if len(pts) < 2: return None
        a, b = pts[0], pts[1]
        dx, dy = a[0] - b[0], a[1] - b[1]
        if dx*dx + dy*dy < 1e-8: return None
        return (a, b)


# ─── POLYGON ASSEMBLY (OTIMIZADO: dict lookup) ──────────────────────────

def assemble(segments):
    if not segments: return []
    segs = []
    for s in segments:
        a = (round(s[0][0], 4), round(s[0][1], 4))
        b = (round(s[1][0], 4), round(s[1][1], 4))
        if a == b: continue
        segs.append((a, b))
    if not segs: return []

    endpoint_map = {}
    for i, (a, b) in enumerate(segs):
        endpoint_map.setdefault(a, []).append(('end', i))
        endpoint_map.setdefault(b, []).append(('start', i))

    used = [False] * len(segs)
    chains = []

    for i in range(len(segs)):
        if used[i]: continue
        a, b = segs[i]
        used[i] = True
        chain = [a, b]
        changed = True
        while changed:
            changed = False
            head, tail = chain[0], chain[-1]
            for pos, key in [('prepend', head), ('append', tail)]:
                matches = endpoint_map.get(key, [])
                for typ, j in matches:
                    if used[j]: continue
                    ja, jb = segs[j]
                    if pos == 'prepend':
                        if (jb[0]-key[0])**2 + (jb[1]-key[1])**2 < 1e-8:
                            chain.insert(0, ja)
                            used[j] = True; changed = True; break
                        elif (ja[0]-key[0])**2 + (ja[1]-key[1])**2 < 1e-8:
                            chain.insert(0, jb)
                            used[j] = True; changed = True; break
                    else:
                        if (ja[0]-key[0])**2 + (ja[1]-key[1])**2 < 1e-8:
                            chain.append(jb)
                            used[j] = True; changed = True; break
                        elif (jb[0]-key[0])**2 + (jb[1]-key[1])**2 < 1e-8:
                            chain.append(ja)
                            used[j] = True; changed = True; break
                if changed: break
            if changed: continue
        hx, hy = chain[0]
        tx, ty = chain[-1]
        if (tx-hx)**2 + (ty-hy)**2 > 1e-8:
            chain.append(chain[0])
        chains.append([V2(x, y) for x, y in chain])
    return chains


# ─── POLYGON OFFSET (SIMPLIFICADO) ──────────────────────────────────────

def offset_poly(points, distance):
    if len(points) < 3: return None
    n = len(points)
    res = []
    for i in range(n):
        cur = points[i]
        prev = points[(i - 1) % n]
        nxt = points[(i + 1) % n]
        dx1, dy1 = cur.x - prev.x, cur.y - prev.y
        dx2, dy2 = nxt.x - cur.x, nxt.y - cur.y
        L1 = math.hypot(dx1, dy1)
        L2 = math.hypot(dx2, dy2)
        if L1 < 1e-6 or L2 < 1e-6: continue
        nx1, ny1 = -dy1 / L1, dx1 / L1
        nx2, ny2 = -dy2 / L2, dx2 / L2
        dot = nx1 * nx2 + ny1 * ny2
        dot = max(-1, min(1, dot))
        angle = math.acos(dot)
        if angle < 0.01 or angle > math.pi - 0.01:
            res.append(V2(cur.x + nx1 * distance, cur.y + ny1 * distance))
            continue
        sin_h = math.sin(angle / 2)
        d = distance / sin_h if sin_h > 0.01 else distance
        bx, by = (nx1 + nx2), (ny1 + ny2)
        bl = math.hypot(bx, by)
        if bl < 1e-9: continue
        res.append(V2(cur.x + bx / bl * d, cur.y + by / bl * d))
    if len(res) < 3: return None
    res = simplify_poly(res, 0.05)
    if len(res) < 3: return None
    if poly_area(res) * poly_area(points) < 0:
        res = list(reversed(res))
        if len(res) < 3: return None
    return res


# ─── INFILL ──────────────────────────────────────────────────────────────

def gen_infill(poly, spacing, angle=45, grid=None):
    lines = []
    minx, miny, maxx, maxy = poly_bbox(poly)
    diag = math.hypot(maxx - minx, maxy - miny) * 1.2
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    rad = math.radians(angle)
    sa, ca = math.sin(rad), math.cos(rad)
    n = max(1, int(diag / spacing) + 1)
    if grid is None:
        grid = _EdgeGrid(poly)
    for i in range(-n, n + 1):
        off = i * spacing
        x1 = cx + off * ca - diag * sa
        y1 = cy + off * sa + diag * ca
        x2 = cx + off * ca + diag * sa
        y2 = cy + off * sa - diag * ca
        clipped = grid.clip([(x1, y1), (x2, y2)], poly)
        if clipped:
            lines.append(clipped)
    return lines

class _EdgeGrid:
    """Spatial grid for fast line-polygon clipping"""
    def __init__(self, poly, cell_size=5.0):
        self.cell_size = cell_size
        xs = [p.x for p in poly]
        ys = [p.y for p in poly]
        self.x0, self.x1 = min(xs), max(xs)
        self.y0, self.y1 = min(ys), max(ys)
        self.nx = max(1, int((self.x1 - self.x0) / cell_size) + 1)
        self.ny = max(1, int((self.y1 - self.y0) / cell_size) + 1)
        self.cells = {}
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            self._add_edge(i, a, b)

    def _cell_key(self, x, y):
        ix = int((x - self.x0) / self.cell_size)
        iy = int((y - self.y0) / self.cell_size)
        return (max(0, min(self.nx - 1, ix)), max(0, min(self.ny - 1, iy)))

    def _add_edge(self, idx, a, b):
        dx, dy = b.x - a.x, b.y - a.y
        dist = math.hypot(dx, dy)
        if dist < 1e-9: return
        steps = max(1, int(dist / self.cell_size * 2))
        for s in range(steps + 1):
            t = s / steps
            x = a.x + t * dx
            y = a.y + t * dy
            key = self._cell_key(x, y)
            self.cells.setdefault(key, []).append(idx)

    def clip(self, seg, poly):
        seg_a = V2(seg[0][0], seg[0][1])
        seg_b = V2(seg[1][0], seg[1][1])
        dx, dy = seg_b.x - seg_a.x, seg_b.y - seg_a.y
        den_inv = 1.0 / (dx*dx + dy*dy) if (dx*dx + dy*dy) > 1e-12 else 0
        dist = math.hypot(dx, dy)
        if dist < 1e-9: return None
        steps = max(1, int(dist / self.cell_size * 2))
        seen = set()
        hits = []
        for s in range(steps + 1):
            t = s / steps
            x = seg_a.x + t * dx
            y = seg_a.y + t * dy
            key = self._cell_key(x, y)
            for eidx in self.cells.get(key, ()):
                if eidx in seen: continue
                seen.add(eidx)
                a, b = poly[eidx], poly[(eidx + 1) % len(poly)]
                ip = seg_inter(seg_a, seg_b, a, b)
                if ip is None: continue
                pt = ((ip.x - seg_a.x)*dx + (ip.y - seg_a.y)*dy) * den_inv
                ex, ey = b.x - a.x, b.y - a.y
                cross = dx * ey - dy * ex
                hits.append((pt, cross > 0))
        if len(hits) < 2: return None
        hits.sort(key=lambda h: h[0])
        first = last = None
        depth = 0
        for pt, ent in hits:
            if ent:
                if depth == 0: first = pt
                depth += 1
            else:
                depth -= 1
                if depth == 0: last = pt
        if first is None or last is None: return None
        p1 = V2(seg_a.x + first * dx, seg_a.y + first * dy)
        p2 = V2(seg_a.x + last * dx, seg_a.y + last * dy)
        return None if (p1 - p2).length() < 1e-8 else (p1, p2)

def seg_inter(a, b, c, d):
    den = (b.x - a.x) * (d.y - c.y) - (b.y - a.y) * (d.x - c.x)
    if abs(den) < 1e-12: return None
    t = ((c.x - a.x) * (d.y - c.y) - (c.y - a.y) * (d.x - c.x)) / den
    u = -((a.x - c.x) * (b.y - a.y) - (a.y - c.y) * (b.x - a.x)) / den
    if t < -1e-9 or t > 1+1e-9 or u < -1e-9 or u > 1+1e-9: return None
    return V2(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y))


# ─── SUPPORT DETECTION ──────────────────────────────────────────────────

def detect_overhangs(layers, lh, max_angle=50):
    if len(layers) < 2: return []
    max_dist = math.tan(math.radians(max_angle)) * lh
    out = []
    for i in range(1, len(layers)):
        cur = layers[i]
        prev = layers[i-1]
        if not cur or not prev: continue
        for cpoly in cur:
            for ppoly in prev:
                cx, cy = sum(p.x for p in cpoly) / len(cpoly), sum(p.y for p in cpoly) / len(cpoly)
                if not point_in_poly(V2(cx, cy), ppoly):
                    out.append(cpoly)
                    break
    return out


# ─── G-CODE WRITER ──────────────────────────────────────────────────────

class GCodeWriter:
    def __init__(self, cfg):
        self.c = cfg
        fd = cfg['filament_diameter']
        self.e_per_mm3 = 1.0 / (math.pi * (fd / 2) ** 2) if fd > 0 else 1.0
        self.flow_mult = cfg.get('filament_multiplier', 1.0)

    def write(self, layers, path):
        lines = []
        c = self.c
        fh = c.get('first_layer_height', min(c['layer_height'] * 1.2, 0.35))

        lines.append('; Generated by Cost3D Built-in Slicer')
        lines.append(f'; Layer height: {c["layer_height"]}')
        lines.append(f'; Nozzle: {c["nozzle_diameter"]}mm')
        lines.append(f'; filament_diameter: {c["filament_diameter"]}')
        lines.append(f'; filament_density: {c["filament_density"]}')
        lines.append(f'; Bed: {c["bed_temp"]}C, Nozzle: {c["nozzle_temp"]}C')
        lines.append(f'; Perimeters: {c["perimeter_count"]}, Fill: {c["fill_density"]*100:.0f}%')
        lines.append(f'; Support: {"enabled" if c["support_enabled"] else "disabled"}')
        lines.append('M73 P0')
        lines.append('G21 ; mm')
        lines.append('G90 ; absolute')
        lines.append('M83 ; relative E')
        lines.append(f'M104 S{c["nozzle_temp"]} ; start nozzle heating')
        lines.append(f'M140 S{c["bed_temp"]} ; start bed heating')
        lines.append('G28 ; home all')
        lines.append(f'M190 S{c["bed_temp"]} ; wait for bed temp')
        lines.append('G1 Z5 F3000')
        lines.append(f'M109 S{c["nozzle_temp"]} ; wait for nozzle temp')
        lines.append('G92 E0 ; reset extrusion')

        total_e = 0.0
        support_e = 0.0
        total_time = 0.0
        has_printed = False
        last_path_type = None
        retract_len = c.get('retract_length', 5)
        retract_speed = c.get('retract_speed', 1800)  # mm/min

        for layer_idx, (layer_z, layer_paths) in enumerate(layers):
            if not layer_paths: continue
            is_first = not has_printed
            zh = fh if is_first else c['layer_height']

            lines.append(f'; LAYER:{layer_idx}')
            lines.append(f';Z:{layer_z:.3f}')

            if is_first:
                lines.append(f'G1 Z{layer_z:.3f} F3000')

            for path_type, pts in layer_paths:
                if not pts or len(pts) < 2: continue

                if path_type == 'travel':
                    # Retract only when leaving a print move
                    if last_path_type and last_path_type != 'travel' and retract_len > 0:
                        lines.append(f'G1 E-{retract_len:.3f} F{retract_speed:.0f}')
                        total_time += retract_len / (retract_speed / 60)
                    last_path_type = 'travel'
                    spd = c['travel_speed']
                    for i, p in enumerate(pts):
                        if i > 0:
                            seg_len = math.hypot(p.x - pts[i-1].x, p.y - pts[i-1].y)
                            total_time += seg_len / (spd / 60)
                            lines.append(f'G0 X{p.x:.3f} Y{p.y:.3f}')
                        else:
                            lines.append(f'G0 X{p.x:.3f} Y{p.y:.3f} F{spd:.0f}')
                    continue

                # Unretract when starting a print move after travel
                if last_path_type == 'travel' and retract_len > 0:
                    lines.append(f'G1 E{retract_len:.3f} F{retract_speed:.0f}')
                    total_time += retract_len / (retract_speed / 60)

                ew = c['perimeter_extrusion_width'] if path_type in ('perimeter','solid_infill','skirt') else c['infill_extrusion_width']
                spd = c.get(path_type + '_speed', c['perimeter_speed'])
                if is_first:
                    spd = c['first_layer_speed']

                for i, p in enumerate(pts):
                    if i == 0: continue
                    dx = p.x - pts[i-1].x
                    dy = p.y - pts[i-1].y
                    seg_len = math.hypot(dx, dy)
                    e_move = seg_len * self.e_per_mm3 * ew * zh * self.flow_mult
                    total_e += e_move
                    if path_type == 'support':
                        support_e += e_move
                    total_time += seg_len / (spd / 60)  # spd in mm/min → seconds
                    lines.append(f'G1 X{p.x:.3f} Y{p.y:.3f} E{e_move:.5f} F{spd:.0f}')

                last_path_type = path_type

            has_printed = True
            lines.append(f'G1 Z{layer_z + c["layer_height"]:.3f}')

        cs_area = math.pi * (c['filament_diameter'] / 2) ** 2
        total_filament_g = total_e * cs_area * c['filament_density'] / 1000

        lines.append(f'; total filament used [mm] = {total_e:.2f}')
        lines.append(f'; total filament used [g] = {total_filament_g:.2f}')
        lines.append(f'; total layer number: {len(layers)}')
        lines.append(f'; TIME:{int(total_time)}')
        lines.append('M104 S0 ; turn off nozzle')
        lines.append('M140 S0 ; turn off bed')
        lines.append('G91 ; relative positioning')
        lines.append('G1 E-2 F300 ; retract filament')
        lines.append('G1 Z+10 F3000 ; raise Z')
        lines.append('G90 ; absolute positioning')
        lines.append('G28 X0 Y0 ; home X and Y')
        lines.append('M84 ; disable motors')
        lines.append('M73 P100')
        lines.append('; Done')

        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return len(lines), total_e, total_time, support_e


# ─── MAIN SLICER ─────────────────────────────────────────────────────────

class BuiltinSlicer:
    def __init__(self):
        self._start_time = 0

    def slice(self, input_path, output_path=None, plate_id=1, **kwargs):
        self._start_time = time.time()

        parsed = _scan_3mf_volumes(input_path, plate_id=plate_id)
        if 'error' in parsed:
            return parsed

        volumes = parsed.get('volumes', [])
        if not volumes:
            return {'error': 'Nenhum volume 3D encontrado'}

        cfg = {k: parsed.get(k, v) for k, v in parsed.items()
               if k not in ('volumes', 'materials', 'colors', 'slicer_config')}
        
        # Apply profile overrides from kwargs
        printer_name = kwargs.get('printer_name', '')
        filament_name = kwargs.get('filament_name', '')
        lh_override = kwargs.get('layer_height_override', 0)
        fd_override = kwargs.get('filament_density_override', 0)
        fm_override = kwargs.get('filament_multiplier_override', 0)
        
        if printer_name:
            cfg['printer_name'] = printer_name
        if filament_name:
            cfg['filament_name'] = filament_name
        if lh_override > 0:
            cfg['layer_height'] = lh_override
        if fd_override > 0:
            cfg['filament_density_override'] = fd_override
        if fm_override > 0:
            cfg['filament_multiplier_override'] = fm_override
        
        self.cfg = cfg

        # Deduplicate identical volumes (same shape, ignoring X/Y/Z translation)
        # Compare: vertex count, tri count, and Z range + shape via triangle area signature
        # Compute total mesh volume from ALL build items (before dedup)
        # so weight reflects everything on the build plate
        total_mesh_vol = sum(_mesh_volume(vol['vertices'], vol['triangles']) for vol in volumes if vol['vertices'])

        dedup_volumes = []
        seen_signatures = set()
        for vol in volumes:
            verts = vol['vertices']
            if not verts:
                continue
            zs = [v.z for v in verts]
            zmin, zmax = min(zs), max(zs)
            nv = len(verts)
            nt = len(vol['triangles'])
            cx = sum(v.x for v in verts) / nv
            cy = sum(v.y for v in verts) / nv
            cz = (zmin + zmax) / 2
            bounds_sig = (round(min(v.x - cx for v in verts), 1),
                          round(max(v.x - cx for v in verts), 1),
                          round(min(v.y - cy for v in verts), 1),
                          round(max(v.y - cy for v in verts), 1),
                          round(zmin - cz, 4), round(zmax - cz, 4))
            sig = (nv, nt, bounds_sig)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                dedup_volumes.append(vol)

        volumes = dedup_volumes
        all_verts, all_tris = [], []
        for vol in volumes:
            base = len(all_verts)
            for v in vol['vertices']:
                all_verts.append(v)
            for t in vol['triangles']:
                all_tris.append([t[0] + base, t[1] + base, t[2] + base])

        if not all_tris:
            return {'error': 'Malha sem triângulos'}

        lh = cfg['layer_height']
        min_z = min(v.z for v in all_verts)
        max_z = max(v.z for v in all_verts)
        layer_count = max(1, int(math.ceil((max_z - min_z) / lh)))
        slicer_mesh = MeshSlicer(all_verts, all_tris)

        total_weight = 0.0
        gcode_layers = []
        ew = cfg['perimeter_extrusion_width']
        nc = cfg['perimeter_count']
        fd = cfg['fill_density']
        ts = cfg['top_solid_layers']
        bs = cfg['bottom_solid_layers']
        infill_pattern = cfg.get('infill_pattern_type', 'grid').lower()
        is_zigzag = 'zigzag' in infill_pattern or 'rectilinear' in infill_pattern
        solid_sp = ew * 1.1
        if is_zigzag:
            infill_sp = ew / max(fd, 0.01)  # single angle → no doubling
        else:
            infill_sp = 2 * ew / max(fd, 0.01)  # *2 for grid (both angles)
        last_layer_polys = None
        actual_layers_processed = 0

        for li in range(layer_count + 1):
            now = time.time()
            if now - self._start_time > SLICE_TIMEOUT:
                break
            z = min_z + li * lh
            segs = slicer_mesh.slice_layer(z)
            chains = assemble(segs)
            polys = []
            poly_originals = []  # True = original ccw (island), False = original cw (hole)
            for c in chains:
                if len(c) < 3: continue
                a = poly_area(c)
                if abs(a) < 0.01: continue
                original_ccw = a > 0
                if original_ccw:
                    c = list(reversed(c))
                polys.append(c)
                poly_originals.append(original_ccw)

            if not polys and not last_layer_polys:
                gcode_layers.append((z, []))
                actual_layers_processed += 1
                continue

            polys.sort(key=lambda p: -abs(poly_area(p)))
            # Reorder originals to match sorted polys
            sorted_indices = sorted(range(len(polys)), key=lambda i: -abs(poly_area(polys[i])))
            poly_originals = [poly_originals[i] for i in sorted_indices]
            outer_idx = 0
            outer_areas = [abs(poly_area(p)) for p in polys]
            if outer_areas:
                max_area = max(outer_areas)
                outer_idx = outer_areas.index(max_area) if max_area > 0 else 0

            layer_paths = []

            solid_bottom = li < bs
            solid_top = li >= len(range(layer_count + 1)) - ts
            is_solid = solid_bottom or solid_top or fd >= 0.95

            if polys:
                for pi, poly in enumerate(polys):
                    pts = simplify_poly(poly, 0.1)
                    if len(pts) < 3: continue

                    # RDP simplify before perimeters to smooth mesh surface detail
                    # (prevents 100K+ segment count on dense bottom-layer meshes)
                    core = pts[:-1] if len(pts) > 2 and (pts[-1] - pts[0]).length() < 1e-9 else pts[:]
                    rdp_tol = 0.3 if is_solid else 0.15
                    rdp_simple = simplify_rdp(core, rdp_tol)
                    if len(rdp_simple) > 2:
                        if (rdp_simple[-1] - rdp_simple[0]).length() > 1e-9:
                            rdp_simple.append(rdp_simple[0])
                        pts = rdp_simple

                    if pi == outer_idx:
                        poly_area_val = poly_area(pts)
                        if poly_area_val > 0:
                            pts = list(reversed(pts))

                    for wi in range(nc):
                        wall_d = -ew * (wi + 0.5)
                        off = offset_poly(pts, wall_d)
                        if off and len(off) > 2:
                            layer_paths.append(('perimeter', off))
                            pts = off
                        else:
                            break

                    if is_solid:
                        sp = solid_sp
                        ptype = 'solid_infill'
                    else:
                        sp = infill_sp
                        ptype = 'infill'

                    # Skip infill for holes (originally cw polygons)
                    is_hole = pi != outer_idx and not poly_originals[pi]
                    if pi == outer_idx or not is_hole:
                        inner = offset_poly(pts, -ew * 0.3)
                    else:
                        inner = None

                    if inner and len(inner) > 2:
                        # Skip infill for tiny polygons (<10 pts)
                        if len(inner) < 10 and abs(poly_area(inner)) < 1.0:
                            continue
                        # RDP simplify
                        core = inner[:-1] if len(inner) > 2 and (inner[-1] - inner[0]).length() < 1e-9 else inner[:]
                        inner_rdp = simplify_rdp(core, 0.8)
                        inner_simple = inner_rdp[:]
                        if len(inner_simple) > 2 and (inner_simple[-1] - inner_simple[0]).length() > 1e-9:
                            inner_simple.append(inner_simple[0])
                        if len(inner_simple) < 3:
                            inner_simple = inner
                        # Build spatial grid once for both angles
                        infill_grid = _EdgeGrid(inner_simple)
                        angle_a = 45 if (li % 2 == 0) else -45
                        angle_b = -45 if (li % 2 == 0) else 45
                        lines_a = gen_infill(inner_simple, sp, angle_a, grid=infill_grid)
                        for la in lines_a:
                            layer_paths.append((ptype, [la[0], la[1]]))
                        if not is_solid and not is_zigzag:
                            lines_b = gen_infill(inner_simple, sp, angle_b, grid=infill_grid)
                            for lb in lines_b:
                                layer_paths.append((ptype, [lb[0], lb[1]]))

                last_layer_polys = [p[:] for p in polys]

            gcode_layers.append((z, layer_paths))
            actual_layers_processed += 1

        # ─── SUPPORT GENERATION ─────────────────────────────────────────────
        if cfg.get('support_enabled', False):
            support_spacing = ew * 3
            support_angle_a = 0
            support_angle_b = 90
            model_polys_per_layer = []
            for z, paths in gcode_layers:
                polys = [pts for path_type, pts in paths
                         if path_type in ('perimeter', 'solid_infill')]
                model_polys_per_layer.append(polys)
            overhang_map = {}
            for li in range(1, len(model_polys_per_layer)):
                cur = model_polys_per_layer[li]
                prev = model_polys_per_layer[li - 1]
                if not cur:
                    continue
                for cpoly in cur:
                    if not prev:
                        overhang_map.setdefault(li, []).append(cpoly)
                        continue
                    cx = sum(p.x for p in cpoly) / len(cpoly)
                    cy = sum(p.y for p in cpoly) / len(cpoly)
                    supported = any(point_in_poly(V2(cx, cy), ppoly) for ppoly in prev)
                    if not supported:
                        overhang_map.setdefault(li, []).append(cpoly)
            if overhang_map:
                support_polys_at_layer = [[] for _ in range(len(gcode_layers))]
                for li, overhangs in overhang_map.items():
                    for oh_poly in overhangs:
                        for support_li in range(li):
                            cx = sum(p.x for p in oh_poly) / len(oh_poly)
                            cy = sum(p.y for p in oh_poly) / len(oh_poly)
                            model_at = model_polys_per_layer[support_li]
                            inside_model = any(point_in_poly(V2(cx, cy), mp) for mp in model_at)
                            if not inside_model:
                                support_polys_at_layer[support_li].append(oh_poly)
                for li, (z, paths) in enumerate(gcode_layers):
                    support_polys = support_polys_at_layer[li]
                    if not support_polys:
                        continue
                    seen = set()
                    for spoly in support_polys:
                        key = (round(spoly[0].x, 1), round(spoly[0].y, 1),
                               round(len(spoly), 0))
                        if key in seen:
                            continue
                        seen.add(key)
                        inner = offset_poly(spoly, -ew * 0.5)
                        if not inner or len(inner) < 3:
                            continue
                        infill_grid = _EdgeGrid(inner)
                        angle = support_angle_a if (li % 2 == 0) else support_angle_b
                        lines_a = gen_infill(inner, support_spacing, angle, grid=infill_grid)
                        for la in lines_a:
                            paths.append(('support', [la[0], la[1]]))
                        lines_b = gen_infill(inner, support_spacing, angle + 90, grid=infill_grid)
                        for lb in lines_b:
                            paths.append(('support', [lb[0], lb[1]]))
                support_els = sum(1 for sl in support_polys_at_layer if sl)
                if support_els:
                    cfg['support_enabled'] = True

        if not output_path:
            output_dir = tempfile.mkdtemp(prefix='custo3d_')
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(output_dir, base_name + '.gcode')

        # Apply density override before GCodeWriter (so metadata in G-code is correct)
        filament_density_override = float(cfg.get('filament_density_override', 0))
        if filament_density_override > 0:
            cfg['filament_density'] = filament_density_override
        filament_multiplier_override = float(cfg.get('filament_multiplier_override', 0))
        if filament_multiplier_override > 0:
            cfg['filament_multiplier'] = filament_multiplier_override

        writer = GCodeWriter(cfg)
        total_lines, total_e, total_time, support_e = writer.write(gcode_layers, output_path)

        cs_area = math.pi * (cfg['filament_diameter'] / 2) ** 2
        filament_density = cfg['filament_density']
        support_density = cfg.get('support_material_density', filament_density)

        # Weight from actual extruded path volume (accounts for infill, perimeters, etc.)
        weight = total_mesh_vol * filament_density / 1000  # solid weight (upper bound)
        if total_e > 0:
            model_e = max(0, total_e - support_e)
            model_weight = model_e * cs_area * filament_density / 1000
            support_weight = support_e * cs_area * support_density / 1000
            path_weight = model_weight + support_weight
            weight = min(path_weight, weight)  # path volume should never exceed mesh volume

        elapsed = time.time() - self._start_time

        # Z move between layers (distance = layer_height, ~300 mm/min Z speed)
        total_time += actual_layers_processed * (lh / 300 * 60)
        total_time *= 1.10  # 10% margin for acceleration/deceleration

        return {
            'success': True,
            'gcode_path': output_path,
            'total_layers': layer_count,
            'actual_layers': actual_layers_processed,
            'estimated_weight_grams': weight,
            'filament_length_mm': total_e,
            'model_filament_length_mm': max(0, total_e - support_e),
            'support_filament_length_mm': support_e,
            'print_time_seconds': int(total_time),
            'layer_height': lh,
            'nozzle_diameter': cfg['nozzle_diameter'],
            'elapsed_seconds': round(elapsed, 1),
            'printer': cfg.get('printer_name', cfg.get('printer_model', '')),
            'filament': cfg.get('filament_name', ''),
            'filament_density': filament_density,
        }


def builtin_slice_3mf(input_path, output_path=None, printer_name='', filament_name='', layer_height=0,
                      filament_density=0, plate_id=1, **kwargs):
    """Slice a 3MF file using the built-in slicer.
    
    Args:
        input_path: Path to .3mf file
        output_path: Optional output path for G-code
        printer_name: Override printer profile name (e.g. 'Anycubic Kobra 3 0.4 nozzle')
        filament_name: Override filament profile name (e.g. 'Anycubic PLA @Anycubic Kobra 3 0.4 nozzle')
        layer_height: Override layer height in mm (0 = use value from 3MF config)
    """
    from .slicer_profiles import ProfileDB
    db = ProfileDB()
    
    # Apply overrides from profile database
    kwargs['printer_name'] = printer_name
    kwargs['filament_name'] = filament_name
    
    if layer_height > 0:
        kwargs['layer_height_override'] = layer_height
    
    # Use filament density: priority = explicit param > profile lookup > .3mf config
    if filament_density > 0:
        kwargs['filament_density_override'] = filament_density
    if filament_name:
        fdata = db.get_filament_data(filament_name)
        if fdata:
            if 'filament_density_override' not in kwargs:
                kwargs['filament_density_override'] = float(fdata['density'])
            # Also apply flow ratio from profile
            flow = float(fdata.get('flow_ratio', 0))
            if flow > 0:
                kwargs['filament_multiplier_override'] = flow
    # Also try to match by printer + filament type if name didn't resolve
    if 'filament_density_override' not in kwargs and printer_name and filament_name:
        compat = db.get_filament_profiles_for_printer(printer_name)
        for fname in compat:
            fdata = db.get_filament_data(fname)
            if fdata:
                kwargs['filament_density_override'] = float(fdata['density'])
                flow = float(fdata.get('flow_ratio', 0))
                if flow > 0:
                    kwargs['filament_multiplier_override'] = flow
                break
    
    slicer = BuiltinSlicer()
    return slicer.slice(input_path, output_path, plate_id=plate_id, **kwargs)
