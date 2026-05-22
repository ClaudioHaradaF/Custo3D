import os
import json
import glob
import re

SLICERS = {
    'OrcaSlicer': {
        'appdata': os.path.join(os.environ.get('APPDATA', ''), 'OrcaSlicer'),
        'user_dirs': [
            ('user', ['filament', 'machine']),
        ],
        'system_vendor_dirs': ['system'],
        'has_numeric_user': False,
    },
    'BambuStudio': {
        'appdata': os.path.join(os.environ.get('APPDATA', ''), 'BambuStudio'),
        'user_dirs': [
            ('user', ['filament', 'machine']),
        ],
        'system_vendor_dirs': ['system'],
        'has_numeric_user': True,
    },
    'AnycubicSlicerNext': {
        'appdata': os.path.join(os.environ.get('APPDATA', ''), 'AnycubicSlicerNext'),
        'user_dirs': [
            ('user', ['filament', 'machine']),
        ],
        'system_vendor_dirs': ['system'],
        'has_numeric_user': True,
    },
}

def find_available_slicers():
    available = []
    for name, config in SLICERS.items():
        if os.path.isdir(os.path.join(config['appdata'], 'user')):
            available.append(name)
    return available

def _get_user_profile_dirs(config, category):
    dirs = []
    for base_rel, cats in config['user_dirs']:
        if category not in cats:
            continue
        base_path = os.path.join(config['appdata'], base_rel)
        if not os.path.isdir(base_path):
            continue

        default_path = os.path.join(base_path, 'default', category)
        if os.path.isdir(default_path):
            dirs.append(('user', default_path))

        user_path = os.path.join(base_path, category)
        if os.path.isdir(user_path):
            dirs.append(('user', user_path))

        if config.get('has_numeric_user'):
            for entry in os.listdir(base_path):
                if entry == 'default' or entry == category or entry == 'Temp' or entry.startswith('.'):
                    continue
                if entry == 'hints.cereal':
                    continue
                num_path = os.path.join(base_path, entry, category)
                if os.path.isdir(num_path):
                    dirs.append(('user', num_path))

    return dirs

def _get_system_profile_dirs(config, category):
    dirs = []
    for base_rel in config['system_vendor_dirs']:
        base_path = os.path.join(config['appdata'], base_rel)
        if not os.path.isdir(base_path):
            continue
        for vendor in os.listdir(base_path):
            vendor_path = os.path.join(base_path, vendor)
            if not os.path.isdir(vendor_path):
                continue
            cat_path = os.path.join(vendor_path, category)
            if os.path.isdir(cat_path):
                dirs.append(('system', cat_path, vendor))
    return dirs

def scan_filaments(slicer_names=None, user_only=False):
    filaments = []
    seen_names = set()
    slicers_to_scan = slicer_names if slicer_names else list(SLICERS.keys())

    for slicer_name in slicers_to_scan:
        config = SLICERS.get(slicer_name)
        if not config or not os.path.isdir(config['appdata']):
            continue

        user_dirs = _get_user_profile_dirs(config, 'filament')
        for source, directory in user_dirs:
            for fpath in glob.glob(os.path.join(directory, '*.json')):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    name = data.get('name', '') or ''
                    if isinstance(data.get('name'), list):
                        name = data['name'][0] if data['name'] else ''
                    if not name and isinstance(data.get('filament_settings_id'), list):
                        name = str(data['filament_settings_id'][0]) if data['filament_settings_id'] else ''
                    if not name:
                        name = os.path.splitext(os.path.basename(fpath))[0]
                    if not name or name in seen_names:
                        continue

                    material = _detect_material(name, data)
                    color = _detect_color(name)
                    brand = _detect_brand(name, slicer_name)

                    density = 1.24
                    if isinstance(data.get('filament_density'), list) and data['filament_density']:
                        try:
                            density = float(data['filament_density'][0])
                        except:
                            pass

                    filament = {
                        'name': name,
                        'brand': brand,
                        'material': material,
                        'color': color,
                        'diameter': 1.75,
                        'density': density,
                        'price_per_kg': 0,
                        'source': f'{slicer_name}',
                    }
                    filaments.append(filament)
                    seen_names.add(name)
                except (json.JSONDecodeError, KeyError, IOError):
                    continue

        if user_only:
            continue
        sys_dirs = _get_system_profile_dirs(config, 'filament')
        for source, directory, vendor in sys_dirs:
            for fpath in glob.glob(os.path.join(directory, '*.json')):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    if data.get('type') != 'filament' and data.get('type') is not None:
                        if data.get('type') == 'machine' or data.get('type') == 'process':
                            continue
                    name = data.get('name', '') or ''
                    if not name:
                        name = os.path.splitext(os.path.basename(fpath))[0]
                    if not name or name in seen_names:
                        continue

                    material = _detect_material(name, data)
                    color = _detect_color(name)
                    brand = vendor

                    filament = {
                        'name': name,
                        'brand': brand,
                        'material': material,
                        'color': color,
                        'diameter': 1.75,
                        'density': 1.24,
                        'price_per_kg': 0,
                        'source': f'{slicer_name} (sistema)',
                    }
                    filaments.append(filament)
                    seen_names.add(name)
                except (json.JSONDecodeError, KeyError, IOError):
                    continue

    return filaments

def scan_printers(slicer_names=None, user_only=False):
    printers = []
    seen_names = set()
    slicers_to_scan = slicer_names if slicer_names else list(SLICERS.keys())

    for slicer_name in slicers_to_scan:
        config = SLICERS.get(slicer_name)
        if not config or not os.path.isdir(config['appdata']):
            continue

        user_dirs = _get_user_profile_dirs(config, 'machine')
        for source, directory in user_dirs:
            for fpath in glob.glob(os.path.join(directory, '*.json')):
                basename = os.path.basename(fpath).lower()
                if 'common' in basename or 'default' in basename:
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    name = data.get('name', '') or ''
                    if not name and isinstance(data.get('printer_settings_id'), list):
                        name = str(data['printer_settings_id'][0]) if data['printer_settings_id'] else ''
                    if not name:
                        name = os.path.splitext(os.path.basename(fpath))[0]
                    if not name or name in seen_names:
                        continue

                    printer = {
                        'name': name,
                        'model': data.get('model_id', ''),
                        'manufacturer': name.split(' ')[0] if ' ' in name else '',
                        'purchase_price': 0,
                        'power_watts': 350,
                        'lifespan_hours': 10000,
                        'maintenance_cost_per_hour': 0,
                        'source': f'{slicer_name}',
                    }
                    printers.append(printer)
                    seen_names.add(name)
                except (json.JSONDecodeError, KeyError, IOError):
                    continue

        if user_only:
            continue
        sys_dirs = _get_system_profile_dirs(config, 'machine')
        for source, directory, vendor in sys_dirs:
            for fpath in glob.glob(os.path.join(directory, '*.json')):
                basename = os.path.basename(fpath).lower()
                if 'common' in basename or 'default' in basename or 'template' in basename:
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    ptype = data.get('type', '')
                    if ptype not in ('machine', 'machine_model'):
                        continue
                    name = data.get('name', '') or ''
                    if not name:
                        name = os.path.splitext(os.path.basename(fpath))[0]
                    if not name or name in seen_names:
                        continue

                    printer = {
                        'name': name,
                        'model': data.get('model_id', ''),
                        'manufacturer': name.split(' ')[0] if ' ' in name else '',
                        'purchase_price': 0,
                        'power_watts': 350,
                        'lifespan_hours': 10000,
                        'maintenance_cost_per_hour': 0,
                        'source': f'{slicer_name} (sistema)',
                    }
                    printers.append(printer)
                    seen_names.add(name)
                except (json.JSONDecodeError, KeyError, IOError):
                    continue

    return printers

def _detect_material(name, data):
    material = ''
    if isinstance(data.get('filament_type'), list) and data['filament_type']:
        material = data['filament_type'][0]
    elif isinstance(data.get('filament_type'), str):
        material = data['filament_type']
    if material:
        return material.replace('FILAMENT_', '').upper()
    name_lower = name.lower()
    materials = ['PLA+', 'PETG', 'ABS', 'TPU', 'NYLON', 'PC', 'ASA', 'PP', 'PEEK', 'PEKK', 'HIPS', 'PVA', 'PVB', 'PLA']
    for m in sorted(materials, key=len, reverse=True):
        if m.lower() in name_lower:
            return m
    return 'PLA'

def _detect_color(name):
    name_lower = name.lower()
    colors = {
        'branco': 'Branco', 'white': 'Branco',
        'preto': 'Preto', 'black': 'Preto',
        'vermelho': 'Vermelho', 'red': 'Vermelho',
        'azul': 'Azul', 'blue': 'Azul', 'cyan': 'Azul',
        'verde': 'Verde', 'green': 'Verde',
        'amarelo': 'Amarelo', 'yellow': 'Amarelo',
        'cinza': 'Cinza', 'gray': 'Cinza', 'grey': 'Cinza',
        'transparent': 'Transparente', 'transparente': 'Transparente',
        'laranja': 'Laranja', 'orange': 'Laranja',
        'roxo': 'Roxo', 'purple': 'Roxo', 'violeta': 'Roxo',
        'rosa': 'Rosa', 'pink': 'Rosa',
        'marrom': 'Marrom', 'brown': 'Marrom',
        'dourado': 'Dourado', 'gold': 'Dourado',
        'prata': 'Prata', 'silver': 'Prata',
        'madeira': 'Madeira', 'wood': 'Madeira',
        'pele': 'Pele', 'skin': 'Pele',
        'metal': 'Metalizado', 'metallic': 'Metalizado',
        'silk': 'Seda',
        'marmore': 'Mármore', 'marble': 'Mármore',
        'carbon': 'Carbono', 'carbon fiber': 'Carbono',
    }
    for kw, cor in sorted(colors.items(), key=lambda x: len(x[0]), reverse=True):
        if kw in name_lower:
            return cor
    return ''

def _detect_brand(name, slicer_name):
    if slicer_name == 'BambuStudio' and name.lower().startswith('bambu'):
        return 'Bambu Lab'
    if slicer_name == 'AnycubicSlicerNext' and 'anycubic' in name.lower():
        return 'Anycubic'
    parts = name.split(' ')
    if len(parts) > 1 and parts[0].lower() in ('generic', 'bambu', 'creality', 'anycubic', 'prusa'):
        return parts[1] if len(parts) > 1 else parts[0]
    return parts[0] if parts else ''