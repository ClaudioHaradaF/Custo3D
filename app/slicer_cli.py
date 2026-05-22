import os, subprocess, tempfile, glob, json, re, shutil
from pathlib import Path
from .slicer_profiles import ProfileDB, _fuzzy_match, _first

def _has_nozzle(process_name, nozzle_diameter):
    """Check if process_name explicitly mentions a nozzle size matching nozzle_diameter.
    Avoids false matches like '0.4' matching inside '0.40' or '0.44'."""
    if not nozzle_diameter:
        return False
    noz = str(nozzle_diameter)
    # Pattern: nozzle_diameter followed by 'nozzle', 'n', or end of word
    return bool(re.search(rf'(?<!\d){re.escape(noz)}(?!\d)\s*(?:nozzle|n\b)', process_name, re.IGNORECASE))


def _needs_sanitizing(three_mf_path):
    """Check if .3mf contains version info incompatible with Orca CLI (>= 2.4.0).
    Orca's version check reads the Application metadata from .model files."""
    try:
        import zipfile
        with zipfile.ZipFile(three_mf_path, 'r') as z:
            for name in z.namelist():
                if name.endswith('.model') and not name.startswith('3D/_rels/'):
                    try:
                        txt = z.read(name).decode('utf-8', errors='replace')
                        if 'BambuStudio-02.07' in txt or 'BambuStudio-2.7' in txt:
                            return True
                    except: pass
    except: pass
    return False


def _sanitize_3mf_for_orca(three_mf_path):
    """Create a sanitized copy of a .3mf with version info stripped for Orca CLI compatibility."""
    import zipfile, tempfile, json, re
    tmpdir = tempfile.mkdtemp(prefix='custo3d_sanitize_')
    dest = os.path.join(tmpdir, os.path.basename(three_mf_path))
    app_pat = re.compile(r'<metadata\s+name="Application"[^>]*>.*?</metadata>', re.DOTALL)
    with zipfile.ZipFile(three_mf_path, 'r') as zin:
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                fname = item.filename
                # Remove slice_info.config entirely
                if fname == 'Metadata/slice_info.config':
                    continue
                # Strip Application metadata from .model files (this bypasses Orca's version check)
                if fname.endswith('.model'):
                    txt = data.decode('utf-8', errors='replace')
                    txt = app_pat.sub('', txt)
                    data = txt.encode('utf-8')
                # Preserve original zip entry metadata (mtime, compress_type)
                new_item = zipfile.ZipInfo(fname, date_time=item.date_time)
                new_item.compress_type = item.compress_type
                zout.writestr(new_item, data)
    return dest, tmpdir

SLICER_REGISTRY = {
    'AnycubicSlicerNext': {
        'exe': r'C:\Program Files\AnycubicSlicerNext\AnycubicSlicerNext.exe',
        'appdata': os.path.join(os.environ.get('APPDATA', ''), 'AnycubicSlicerNext'),
        'has_cli': True,
    },
    'AnycubicSlicer': {
        'exe': r'C:\Program Files\AnycubicSlicer\Anycubic-Slicer-console.exe',
        'appdata': os.path.join(os.environ.get('APPDATA', ''), 'AnycubicSlicer'),
        'has_cli': True,
    },
    'OrcaSlicer': {
        'exe': r'C:\Program Files\OrcaSlicer\orca-slicer.exe',
        'has_cli': True,
    },
    'BambuStudio': {
        'exe': r'C:\Program Files\Bambu Studio\bambu-studio.exe',
        'has_cli': False,
    },
}


def find_available_slicers():
    result = []
    for name, cfg in SLICER_REGISTRY.items():
        if os.path.isfile(cfg['exe']):
            result.append({
                'name': name,
                'exe': cfg['exe'],
                'has_cli': cfg.get('has_cli', False),
            })
    return result


_db = None
def get_db():
    global _db
    if _db is None:
        _db = ProfileDB()
    return _db


def _run_slicer_anycubic_next(name, input_3mf, output_dir, printer='', filament='', process=''):
    cfg = SLICER_REGISTRY.get(name)
    if not cfg:
        return {'error': f'Slicer "{name}" não encontrado'}
    exe = cfg['exe']
    db = get_db()

    # Find machine profile
    machine_fp = db.get_printer_profile(printer) if printer else None
    nozzle_diameter = 0
    layer_height = 0
    default_process = ''
    printer_model = ''
    if not machine_fp:
        # Auto-detect from 3MF or use first available
        from .slicer_engine import _scan_3mf_volumes
        parsed = _scan_3mf_volumes(input_3mf)
        pcfg = parsed.get('slicer_config', {})
        printer_model = pcfg.get('printer_model', '') or pcfg.get('printer_settings_id', '')
        nozzle_diameter = parsed.get('nozzle_diameter', 0)
        layer_height = parsed.get('layer_height', 0)
        default_process = pcfg.get('default_print_profile', '')
        if printer_model:
            machine_fp = db.get_printer_profile(printer_model, nozzle_diameter=nozzle_diameter)
    
    # Find process profile
    process_fp = None
    if process:
        process_fp = db.get_process_path(process)
    if not process_fp:
        # Auto-detect process from 3MF default_print_profile
        if default_process and not process_fp:
            process_fp = db.get_process_path(default_process)
        # Fallback: find process by printer + nozzle + layer height
        if not process_fp and layer_height > 0:
            mp_name = os.path.splitext(os.path.basename(machine_fp))[0] if machine_fp else printer_model
            procs = db.get_processes_for_printer(mp_name)
            # Only consider processes that are explicitly compatible (not empty compat list)
            pinfo_list = [(p, plh, db._process_map[p]) for p, plh in procs]
            compatible = [(p, plh) for p, plh, pi in pinfo_list
                          if pi.get('compatible_printers') and
                          any(c for c in pi['compatible_printers'] if 'P1S' in c or 'X1' in c or printer_model.replace(' ','') in c.replace(' ',''))]
            if compatible:
                best_proc, best_score = None, -1
                for pname, plh in compatible:
                    score = 0
                    if abs(plh - layer_height) < 0.001:
                        score += 10
                    elif abs(plh - layer_height) < 0.02:
                        score += 5
                    if nozzle_diameter and str(nozzle_diameter) in pname:
                        score += 3
                    if score > best_score:
                        best_score = score
                        best_proc = pname
                if best_proc:
                    process_fp = db.get_process_path(best_proc)
    
    # Find filament profile
    filament_fp = None
    if filament:
        filament_fp = db.get_filament_path(filament)
    
    args = [exe]
    settings = []
    if machine_fp:
        settings.append(machine_fp)
    if process_fp:
        settings.append(process_fp)
    if settings:
        args.append('--load-settings')
        args.append(';'.join(settings))
    if filament_fp:
        args.append('--load-filaments')
        args.append(filament_fp)
    args.append('--slice')
    args.append('0')
    args.append('--outputdir')
    args.append(output_dir)
    args.append(input_3mf)
    
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600,
                              creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
    except subprocess.TimeoutExpired:
        return {'error': 'Fatiamento excedeu o tempo limite de 10 minutos'}
    except Exception as e:
        return {'error': str(e)}
    
    if proc.returncode != 0:
        err_msg = proc.stderr[:2000] if proc.stderr else proc.stdout[:2000] if proc.stdout else 'Erro desconhecido'
        return {'error': f'Fatiador retornou código {proc.returncode}: {err_msg}'}
    
    gcode_files = sorted(glob.glob(os.path.join(output_dir, '*.gcode')))
    if not gcode_files:
        return {'error': 'Nenhum arquivo G-code gerado', 'stdout': proc.stdout[:1000], 'stderr': proc.stderr[:1000]}
    
    return {
        'success': True,
        'gcode_files': gcode_files,
        'output_dir': output_dir,
        'slicer': name,
        'profiles': {
            'machine': os.path.basename(machine_fp) if machine_fp else '',
            'process': os.path.basename(process_fp) if process_fp else '',
            'filament': os.path.basename(filament_fp) if filament_fp else '',
        }
    }


def _run_slicer_anycubic(name, input_3mf, output_dir):
    cfg = SLICER_REGISTRY.get(name)
    if not cfg:
        return {'error': f'Slicer "{name}" não encontrado'}
    exe = cfg['exe']
    args = [exe, '--export-gcode', '--output-dir', output_dir, input_3mf]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600,
                              creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
    except subprocess.TimeoutExpired:
        return {'error': 'Fatiamento excedeu o tempo limite de 10 minutos'}
    except Exception as e:
        return {'error': str(e)}
    if proc.returncode != 0:
        return {'error': f'Fatiador retornou código {proc.returncode}', 'stderr': proc.stderr[:2000]}
    gcode_files = sorted(glob.glob(os.path.join(output_dir, '*.gcode')))
    if not gcode_files:
        return {'error': 'Nenhum arquivo G-code gerado', 'stdout': proc.stdout[:1000], 'stderr': proc.stderr[:1000]}
    return {'success': True, 'gcode_files': gcode_files, 'output_dir': output_dir, 'slicer': name}


def _run_slicer_orca(name, input_3mf, output_dir, printer='', filament='', process=''):
    cfg = SLICER_REGISTRY.get(name)
    if not cfg:
        return {'error': f'Slicer "{name}" não encontrado'}
    exe = cfg['exe']
    db = get_db()
    
    # Sanitize 3mf if version info would cause Orca CLI rejection
    _temp_dirs = []
    slice_input = input_3mf
    if _needs_sanitizing(input_3mf):
        sanitized_path, sanitized_tmpdir = _sanitize_3mf_for_orca(input_3mf)
        slice_input = sanitized_path
        _temp_dirs.append(sanitized_tmpdir)
    
    machine_fp = db.get_printer_profile(printer) if printer else None
    process_fp = db.get_process_path(process) if process else None
    filament_fp = db.get_filament_path(filament) if filament else None
    
    # Auto-detect from 3MF if explicit lookup failed (use original for scanning)
    from .slicer_engine import _scan_3mf_volumes
    parsed = _scan_3mf_volumes(input_3mf)
    pcfg = parsed.get('slicer_config', {})
    if not machine_fp:
        printer_model = pcfg.get('printer_model', '') or pcfg.get('printer_settings_id', '')
        nozzle_diameter = parsed.get('nozzle_diameter', 0)
        if printer_model:
            machine_fp = db.get_printer_profile(printer_model, nozzle_diameter=nozzle_diameter)
            if not machine_fp:
                for p in db.printers:
                    if printer_model.lower() in p['name'].lower():
                        found = db.get_printer_profile(p['name'])
                        if found:
                            machine_fp = found
                            break
    
    if not process_fp:
        default_proc = pcfg.get('default_print_profile', '')
        if default_proc:
            process_fp = db.get_process_path(default_proc)
    
    # Printer-based fallback for process when default_print_profile is not found
    if not process_fp and machine_fp:
        nozzle_diameter = parsed.get('nozzle_diameter', 0)
        layer_height = parsed.get('layer_height', 0)
        printer_model = pcfg.get('printer_model', '') or pcfg.get('printer_settings_id', '')
        mp_name = os.path.splitext(os.path.basename(machine_fp))[0]
        # Filter processes with explicit printer compatibility
        compat_procs = []
        for pname, pinfo in db._process_map.items():
            compat = pinfo.get('compatible_printers', [])
            if not compat:
                continue
            if not any(mp_name.lower() in c.lower() for c in compat):
                if not any(printer_model.lower().replace(' ','') in c.lower().replace(' ','') for c in compat):
                    continue
            compat_procs.append((pname, pinfo.get('layer_height', 0)))
        if compat_procs and layer_height > 0:
            candidates = [(p, lh) for p, lh in compat_procs if abs(lh - layer_height) < 0.02]
            if not candidates:
                candidates = compat_procs[:20]
            best_proc, best_lh_diff = None, float('inf')
            for pname, plh in candidates:
                lh_diff = abs(plh - layer_height)
                if best_proc is None:
                    best_proc, best_lh_diff = pname, lh_diff
                elif abs(lh_diff - best_lh_diff) < 0.001 or (lh_diff < best_lh_diff):
                    if abs(lh_diff - best_lh_diff) < 0.001:
                        best_has_noz = nozzle_diameter and _has_nozzle(best_proc, nozzle_diameter)
                        cur_has_noz = nozzle_diameter and _has_nozzle(pname, nozzle_diameter)
                        if cur_has_noz and not best_has_noz:
                            best_proc, best_lh_diff = pname, lh_diff
                        elif not best_has_noz and not cur_has_noz:
                            if pname < best_proc:
                                best_proc, best_lh_diff = pname, lh_diff
                    else:
                        best_proc, best_lh_diff = pname, lh_diff
            if best_proc:
                process_fp = db._process_map[best_proc]['path']
    
    # Orca internally resolves default_print_profile from the machine profile,
    # but looks in process_full/ (which doesn't exist). So if we have a machine
    # profile but no process, skip --load-settings to avoid that internal error.
    args = [exe]
    if machine_fp and process_fp:
        args.append('--load-settings')
        args.append(f'{machine_fp};{process_fp}')
    elif machine_fp and not process_fp:
        pass
    if filament_fp:
        args.append('--load-filaments')
        args.append(filament_fp)
    args.append('--slice')
    args.append('0')
    args.append('--outputdir')
    args.append(output_dir)
    args.append(slice_input)  # use sanitized copy if applicable
    
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600,
                              creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
    except subprocess.TimeoutExpired:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)
        return {'error': 'Fatiamento excedeu o tempo limite'}
    except Exception as e:
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)
        return {'error': str(e)}
    
    stdout = proc.stdout or ''
    stderr = proc.stderr or ''

    if proc.returncode == 0:
        gcode_files = sorted(glob.glob(os.path.join(output_dir, '*.gcode')))
        if gcode_files:
            for td in _temp_dirs:
                shutil.rmtree(td, ignore_errors=True)
            return {'success': True, 'gcode_files': gcode_files, 'output_dir': output_dir, 'slicer': name}
        result = {'error': f'Slicer executou mas não produziu arquivo .gcode em:\n{output_dir}', 'stdout': stdout[:1000], 'stderr': stderr[:1000]}
    elif _temp_dirs:
        # Sanitization was applied but Orca CLI still failed (param values or model incompatibility).
        # Fall back to opening the original file in Orca GUI for manual slicing.
        for td in _temp_dirs:
            shutil.rmtree(td, ignore_errors=True)
        return _run_orca_gui(name, input_3mf)
    else:
        err_msg = stderr[:2000] if stderr else stdout[:2000] if stdout else 'Erro desconhecido'
        result = {'error': f'OrcaSlicer retornou código {proc.returncode}: {err_msg}'}
    
    for td in _temp_dirs:
        shutil.rmtree(td, ignore_errors=True)
    return result


def _run_orca_gui(name, input_3mf):
    cfg = SLICER_REGISTRY.get(name)
    if not cfg:
        return {'error': 'OrcaSlicer não encontrado'}
    try:
        subprocess.Popen([cfg['exe'], input_3mf])
        return {'success': True, 'message': 'Arquivo aberto no OrcaSlicer. Fatia manualmente.'}
    except Exception as e:
        return {'error': str(e)}


def _run_slicer_bambustudio(name, input_3mf):
    cfg = SLICER_REGISTRY.get(name)
    if not cfg:
        return {'error': 'Bambu Studio não encontrado'}
    # BambuStudio has no CLI -- open in GUI
    try:
        subprocess.Popen([cfg['exe'], input_3mf])
        return {
            'success': True,
            'message': 'Arquivo aberto no Bambu Studio. Fatia manualmente.',
            'manual_path': input_3mf,
        }
    except Exception as e:
        return {'error': str(e)}


def slice_3mf(slicer_name, input_3mf, output_dir=None,
              printer='', filament='', process=''):
    if not os.path.isfile(input_3mf):
        return {'error': f'Arquivo .3mf não encontrado: {input_3mf}'}
    if not output_dir:
        output_dir = tempfile.mkdtemp(prefix='custo3d_slice_')
    else:
        os.makedirs(output_dir, exist_ok=True)
    
    if slicer_name == 'AnycubicSlicerNext':
        return _run_slicer_anycubic_next(slicer_name, input_3mf, output_dir, printer, filament, process)
    elif slicer_name == 'AnycubicSlicer':
        return _run_slicer_anycubic(slicer_name, input_3mf, output_dir)
    elif slicer_name == 'OrcaSlicer':
        return _run_slicer_orca(slicer_name, input_3mf, output_dir, printer, filament, process)
    elif slicer_name == 'BambuStudio':
        return _run_slicer_bambustudio(slicer_name, input_3mf)
    else:
        return {'error': f'Fatiador desconhecido: {slicer_name}'}


def open_in_bambu_studio(input_3mf):
    exe = SLICER_REGISTRY['BambuStudio']['exe']
    if not os.path.isfile(exe):
        return {'error': 'Bambu Studio não encontrado'}
    try:
        subprocess.Popen([exe, input_3mf])
        return {'success': True, 'message': 'Arquivo aberto no Bambu Studio. Fatia manualmente.'}
    except Exception as e:
        return {'error': str(e)}
