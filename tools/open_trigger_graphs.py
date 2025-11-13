#!/usr/bin/env python3
"""
Interactive helper to list generated trigger graphs under ./maps, check completeness (node_details+debug JSONs),
let user pick one to open via a local HTTP server, or regenerate from a .map file.

Usage:
  python tools/open_trigger_graphs.py

The script:
 - scans ./data/maps or ./maps for folders containing *_trigger_graph.html
 - checks for *_node_details.json and *_debug.json alongside the HTML
 - lists entries with indices; user inputs index to open
 - 'g' or 'G' triggers "generate mode": it scans project root for .map files and allows generating graph via visualize_triggers.py
 - starts a local python HTTP server in background and opens the URL in the default browser

This is a simple, cross-platform helper intended for local use.
"""
import os, sys, subprocess, webbrowser, time, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / 'data' / 'maps'
if not MAPS_DIR.exists():
    MAPS_DIR = ROOT / 'maps'

HTTP_PORT = 8000


def find_graphs():
    out = []
    if not MAPS_DIR.exists():
        return out
    for sub in sorted(MAPS_DIR.iterdir()):
        if not sub.is_dir():
            continue
        # determine presence of html / node_details / debug
        htmls = list(sub.glob('*_trigger_graph.html'))
        nodes = list(sub.glob('*_node_details.json'))
        debugs = list(sub.glob('*_debug.json'))
        if htmls:
            for p in htmls:
                nd = p.with_name(p.stem + '_node_details.json')
                dbg = p.with_name(p.stem + '_debug.json')
                out.append({
                    'map': sub.name,
                    'html': p,
                    'node_json': nd,
                    'debug_json': dbg,
                    'has_html': True,
                    'has_node': nd.exists(),
                    'has_debug': dbg.exists(),
                })
        else:
            # no html present: but maybe json exist
            nd = nodes[0] if nodes else None
            dbg = debugs[0] if debugs else None
            out.append({
                'map': sub.name,
                'html': None,
                'node_json': nd,
                'debug_json': dbg,
                'has_html': False,
                'has_node': bool(nd),
                'has_debug': bool(dbg),
            })
    return out


def list_maps():
    maps = find_graphs()
    if not maps:
        print('No generated trigger_graph HTML found under', MAPS_DIR)
        return maps
    print('\nFound trigger graphs:')
    for i, e in enumerate(maps):
        # compute status
        if e['has_html'] and e['has_node'] and e['has_debug']:
            status = 'COMPLETE'
        elif e['has_html'] and not (e['has_node'] or e['has_debug']):
            status = 'MISSING_JSON'
        elif not e['has_html'] and (e['has_node'] or e['has_debug']):
            status = 'MISSING_HTML'
        else:
            status = 'ALL_MISSING'
        name = e['html'].name if e['html'] else '<no html>'
        print(f"[{i}] {e['map']}: {name} ({status})")
    return maps


def start_http_server(root, port=HTTP_PORT):
    # spawn a simple http.server in the background
    # Use sys.executable to ensure same Python
    cmd = [sys.executable, '-m', 'http.server', str(port)]
    print(f"Starting HTTP server at http://localhost:{port}/ (serving {root})")
    # start in the directory
    return subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_graph_entry(entry):
    # ensure files are in place
    html = entry['html']
    rel = html.relative_to(ROOT)
    url = f'http://localhost:{HTTP_PORT}/{rel.as_posix()}'
    print('Opening', url)
    webbrowser.open(url)


def _spawn_background_map_parser(map_name: str, map_dir: Path):
    """在后台调用 map_parser.py 来生成缺失的 JSON。
    返回 (event, result) 其中 event 为 threading.Event，可被等待；result 为 dict，
    在进程结束后填充 'pid','log_path','written_files'。
    """
    mapfile = find_mapfile_for(map_name)
    if not mapfile:
        print(f"⚠️ No corresponding .map file found; cannot auto-repair JSON: {map_name}")
        return None, None

    candidates = [Path(__file__).parent / 'map_parser.py', Path('map_parser.py'), Path('tools') / 'map_parser.py']
    parser_py = None
    for c in candidates:
        if c.exists():
            parser_py = c
            break
    if not parser_py:
        print('⚠️ Could not locate map_parser.py; cannot auto-repair JSON (tried: ' + ', '.join(str(c) for c in candidates) + ')')
        return None, None

    import subprocess, sys as _sys
    repo_root = Path(__file__).resolve().parents[1]

    # ensure map_dir exists
    map_dir.mkdir(parents=True, exist_ok=True)

    try:
        # start subprocess but send its stdout/stderr to DEVNULL so we don't interleave logs
        proc = subprocess.Popen([_sys.executable, str(parser_py), str(mapfile)], cwd=str(repo_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print('❌ Failed to start background repair:', e)
        return None, None

    done = threading.Event()
    result = {'pid': proc.pid, 'report_path': str(map_dir / f"{map_name}_report.json"), 'written_files': []}

    def _wait_and_parse():
        try:
            proc.wait()
        finally:
            pass
        # determine which expected files now exist
        try:
            expected = [
                map_dir / f"{map_name}_triggers.json",
                map_dir / f"{map_name}_events.json",
                map_dir / f"{map_name}_actions.json",
                map_dir / f"{map_name}_locals.json",
                map_dir / f"{map_name}_report.json",
            ]
            written = [str(p) for p in expected if p.exists()]
            result['written_files'] = written
        except Exception:
            result['written_files'] = []
        done.set()

    t = threading.Thread(target=_wait_and_parse, daemon=True)
    t.start()
    # return event and result holder to caller who can wait on event
    return done, result


def find_map_files():
    # scan project root for .map files
    out = []
    for p in sorted(ROOT.glob('*.map')):
        out.append(p)
    return out


def find_mapfile_for(mapname: str):
    """Look for a .map file in project root that matches the given map name.
    Returns Path or None."""
    # exact match first
    cand = ROOT / f"{mapname}.map"
    if cand.exists():
        return cand
    # fallback: any file that starts with mapname
    for p in ROOT.glob(f'{mapname}*.map'):
        return p
    return None


def _spinner(msg, stop_event):
    chars = ['|','/','-','\\']
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r{msg} {chars[i%4]}")
        sys.stdout.flush()
        time.sleep(0.12)
        i += 1
    sys.stdout.write('\r' + ' ' * (len(msg) + 3) + '\r')
    sys.stdout.flush()


def generate_from_map(map_path, auto_open=False):
    """Call visualize_triggers.py --map <map> and optionally auto-open when done.
    Returns True on success, False on failure."""
    print('Generating graph for', map_path)
    # If the user moved visualize_triggers.py into tools/, prefer that path (keeps local tools together)
    vt = ROOT / 'tools' / 'visualize_triggers.py'
    if vt.exists():
        script_path = vt
    else:
        script_path = ROOT / 'visualize_triggers.py'
    # Call visualize_triggers.py in quiet mode by default; caller can edit this file to remove --quiet
    cmd = [sys.executable, str(script_path), '--map', str(map_path), '--quiet']
    stop_event = threading.Event()
    spinner_thread = threading.Thread(target=_spinner, args=(f"Generating {map_path.name}...", stop_event), daemon=True)
    spinner_thread.start()
    try:
        # Run the visualizer from the repository root so relative paths resolve correctly.
        repo_root = ROOT
        # ensure logs directory
        logs_dir = repo_root / 'data' / 'logs'
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f'visualize_{map_path.stem}.log'
        print(f'Running: {cmd}'); print(f'  cwd={repo_root}'); print(f'  log -> {log_path}')
        # open log file for child stdout/stderr
        logf = open(str(log_path), 'w', encoding='utf-8')
        with subprocess.Popen(cmd, cwd=str(repo_root), stdout=logf, stderr=subprocess.STDOUT) as p:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # child still running; user can inspect the log
                print(f'Generation still running after 30s. Check log: {log_path}')
                # do not kill the process; leave it running for manual inspection
                return False
            if p.returncode != 0:
                # read last portion of log for diagnostic
                try:
                    logf.flush()
                    logf.close()
                    out = log_path.read_text(encoding='utf-8', errors='replace')
                    snippet = out[-4000:]
                except Exception:
                    snippet = '<could not read child output>'
                raise subprocess.CalledProcessError(p.returncode, cmd, output=snippet)
        # ensure log file closed
        try:
            logf.close()
        except Exception:
            pass
    except subprocess.CalledProcessError as e:
        stop_event.set()
        spinner_thread.join()
        print(f'Generation failed with exit code {e.returncode}')
        if getattr(e, 'output', None):
            print('--- child output (truncated) ---')
            print(e.output)
            print('--- end child output ---')
        return False
    except Exception as e:
        stop_event.set()
        spinner_thread.join()
        print('Generation failed:', e)
        return False
    stop_event.set()
    spinner_thread.join()
    print(f'Generation finished for {map_path.name}')
    return True


def main():
    server = None
    try:
        while True:
            maps = list_maps()
            print('\nOptions:')
            print('  Enter index number to open that graph')
            print("  Enter 'g' to list .map files and (re)generate a graph from a .map")
            print("  Enter 'q' to quit")
            choice = input('\nYour choice: ').strip()
            if choice.lower() == 'q':
                break
            if choice.lower() == 'g':
                mfiles = find_map_files()
                if not mfiles:
                    print('No .map files found in project root.')
                    continue
                print('\nFound .map files:')
                for i, m in enumerate(mfiles):
                    print(f'  [{i}] {m.name}')
                print("  Enter index to generate, 'a' to generate ALL, or blank to cancel")
                idx = input('Your choice: ').strip()
                if idx == '':
                    continue
                if idx.lower() == 'a':
                    print('Generating all .map files...')
                    for m in mfiles:
                        try:
                            generate_from_map(m)
                        except Exception as ex:
                            print('Failed to generate for', m, ex)
                    print('All generation attempts finished. Rescanning...')
                    time.sleep(0.3)
                    continue
                try:
                    mi = int(idx)
                    if 0 <= mi < len(mfiles):
                        ok = generate_from_map(mfiles[mi])
                        print('Generation done. Rescanning...')
                        time.sleep(0.3)
                        if ok:
                            # rescan and attempt to open the newly generated graph (single-file generation only)
                            maps = find_graphs()
                            target_name = mfiles[mi].stem
                            found = None
                            for e in maps:
                                if e['map'] == target_name:
                                    found = e
                                    break
                            if found and found.get('has_html') and found.get('has_node') and found.get('has_debug'):
                                if server is None:
                                    server = start_http_server(ROOT)
                                    time.sleep(0.3)
                                open_graph_entry(found)
                                # after opening, continue outer loop (list will refresh on next iteration)
                                continue
                            else:
                                print('Generated output incomplete or not found; not auto-opening.')
                                continue
                except Exception as e:
                    print('Invalid selection', e)
                    continue
            # otherwise numeric index
            try:
                ix = int(choice)
            except Exception:
                print('Invalid input')
                continue
            if ix < 0 or ix >= len(maps):
                print('Index out of range')
                continue
            entry = maps[ix]
            # check completeness
            has_html = bool(entry.get('has_html'))
            has_node = bool(entry.get('has_node'))
            has_debug = bool(entry.get('has_debug'))
            if not (has_html and has_node and has_debug):
                print(f"Selected entry '{entry['map']}' is incomplete.")
                # try to find a .map in project root to generate from
                mapfile = find_mapfile_for(entry['map'])
                if mapfile:
                    print(f"Found source .map '{mapfile.name}' — attempting to generate trigger_graph...")
                    try:
                        generate_from_map(mapfile)
                    except subprocess.CalledProcessError as ex:
                        print('Generation failed (subprocess error):', ex)
                        print('Will not open incomplete trigger_graph.')
                        continue
                    except Exception as ex:
                        print('Generation failed:', ex)
                        print('Will not open incomplete trigger_graph.')
                        continue
                    # rescan entries to pick up new/generated files
                    print('Generation finished — rescanning...')
                    time.sleep(0.25)
                    maps = find_graphs()
                    # find matching entry again
                    found = None
                    for e in maps:
                        if e['map'] == entry['map']:
                            found = e
                            break
                    if not found:
                        print('Generation seemed to run but no output found. Not opening.')
                        continue
                    entry = found
                    has_html = bool(entry.get('has_html'))
                    has_node = bool(entry.get('has_node'))
                    has_debug = bool(entry.get('has_debug'))
                    if not (has_html and has_node and has_debug):
                        print('After generation the entry is still incomplete. Not opening.')
                        continue
                else:
                    # print explicit missing files and helpful suggestions
                    miss = []
                    if not has_html:
                        miss.append(f"HTML ({entry.get('html') or '<no html>'})")
                    if not has_node:
                        expected_node = f"{entry['map']}_node_details.json"
                        miss.append(f"node JSON ({expected_node})")
                    if not has_debug:
                        expected_dbg = f"{entry['map']}_debug.json"
                        miss.append(f"debug JSON ({expected_dbg})")
                    print('Missing files for this entry: ' + ', '.join(miss))
                    print("No corresponding .map source found in repository root — cannot auto-generate.")
                    print("Suggestions:")
                    print("  - Place the missing sidecar JSON files next to the HTML (names above).\n  - Or run this script and press 'g' to generate from a .map if you have one.\n  - Or run: python visualize_triggers.py --map <mapname> to generate manually.")
                    continue
            # ensure HTTP server
            if server is None:
                server = start_http_server(ROOT)
                # give server a moment
                time.sleep(0.3)

            # Before opening: check whether the original source JSONs (triggers/actions/events/locals)
            # are present in the map directory. If missing, we will still open the HTML but spawn a
            # background map_parser to auto-fill them and inform the user.
            mapname = entry['map']
            map_dir = entry.get('html').parent if entry.get('html') else (MAPS_DIR / mapname)
            def _has_source_jsons(d: Path, name: str) -> bool:
                # require ALL four source JSONs to be present; otherwise consider incomplete
                return all((d / f"{name}_{k}.json").exists() or (d / f"{k}.json").exists()
                           for k in ("triggers", "actions", "events", "locals"))

            if not _has_source_jsons(map_dir, mapname):
                print(f"⚠️ Note: source JSONs (triggers/actions/events/locals) are missing. Repair started in background for: {mapname} (generation info will be written to {mapname}_report.json)")
                done_event, result = _spawn_background_map_parser(mapname, map_dir)
            else:
                done_event = None
                result = None

            open_graph_entry(entry)

            # If we spawned a background repair, wait for it to finish now and report a concise summary.
            if done_event is not None:
                # wait with a small dot-progress to avoid blocking print interleaving
                print('ℹ️ Waiting for background repair to complete...', end='', flush=True)
                while not done_event.wait(timeout=0.5):
                    print('.', end='', flush=True)
                print('\nRepair complete.', end=' ')
                try:
                    # 简洁单行提示，用户可自行查看目录或 report
                    print(f'Repair complete - please check: {map_dir}')
                except Exception:
                    print('Repair complete - please check the corresponding directory for details.')
    finally:
        if server:
            print('Stopping HTTP server...')
            server.terminate()


if __name__ == '__main__':
    main()
