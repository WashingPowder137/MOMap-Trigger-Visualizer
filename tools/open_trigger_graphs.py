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

# --- simple ANSI colors (work in most modern terminals, including PowerShell) ---
RESET = "\033[0m"
FG_GREEN = "\033[92m"
FG_YELLOW = "\033[93m"
FG_RED = "\033[91m"
FG_CYAN = "\033[96m"
FG_MAGENTA = "\033[95m"

# --- colored print helpers ---
def cyan_msg(msg):      print(FG_CYAN   + msg + RESET)   # for info
def green_msg(msg):     print(FG_GREEN  + msg + RESET)   # for success
def yellow_msg(msg):    print(FG_YELLOW + msg + RESET)   # for warn
def red_msg(msg):       print(FG_RED    + msg + RESET)   # for error

ROOT = Path(__file__).resolve().parents[1]
MAPS_DIR = ROOT / 'data' / 'maps'
if not MAPS_DIR.exists():
    MAPS_DIR = ROOT / 'maps'

HTTP_PORT = 8999

TOOL_VERSION = '1.4.2'


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
    print(f"\nCurrent version of the tool: {FG_CYAN}v{TOOL_VERSION}{RESET}")
    if not maps:
        yellow_msg('No generated trigger_graph HTML found under ' + str(MAPS_DIR))
        return maps

    print('\nFound trigger graphs:')
    import json

    for i, e in enumerate(maps):
        has_html = bool(e['has_html'])
        has_node = bool(e['has_node'])
        has_debug = bool(e['has_debug'])

        # 1) 完整性
        if has_html and has_node and has_debug:
            complete_status = 'COMPLETE'
        elif has_html and not (has_node or has_debug):
            complete_status = 'MISSING_JSON'
        elif not has_html and (has_node or has_debug):
            complete_status = 'MISSING_HTML'
        else:
            complete_status = 'ALL_MISSING'

        # 2) 版本
        version_status = 'UNKNOWN'
        dbg_path = e.get('debug_json')
        if dbg_path and Path(dbg_path).exists():
            try:
                with open(dbg_path, 'r', encoding='utf-8') as f:
                    dbg = json.load(f)
                file_ver = dbg.get('tool_version')
                if isinstance(file_ver, str) and file_ver.strip():
                    cmp = compare_version(file_ver, TOOL_VERSION)
                    if cmp < 0:
                        version_status = f'OUTDATED v{file_ver}'
                    elif cmp > 0:
                        version_status = f'NEWER v{file_ver}'
                    else:
                        version_status = f'v{file_ver}'
            except Exception:
                version_status = 'UNKNOWN'

        # 3) 缓存状态
        mapname = e['map']
        map_dir = e['html'].parent if e.get('html') else (MAPS_DIR / mapname)
        cache_status = _cache_status(map_dir, mapname)

        name = e['html'].name if e['html'] else '<no html>'

        # 状态上个色，例子：
        if complete_status == 'COMPLETE':
            complete_str = FG_GREEN + complete_status + RESET
        else:
            complete_str = FG_YELLOW + complete_status + RESET

        if 'OUTDATED' in version_status:
            version_str = FG_RED + version_status + RESET
        elif 'NEWER' in version_status:
            version_str = FG_CYAN + version_status + RESET
        elif version_status.startswith('v'):
            version_str = FG_GREEN + version_status + RESET
        else:
            version_str = version_status

        if cache_status == 'CACHED':
            cache_str = FG_GREEN + cache_status + RESET
        elif cache_status == 'CACHE_OUTDATED':
            cache_str = FG_YELLOW + cache_status + RESET
        else:  # NOT_CACHED
            cache_str = FG_RED + cache_status + RESET

        print(f"[{FG_CYAN}{i}{RESET}] {FG_MAGENTA}{e['map']}{RESET}: {name} "
            f"({complete_str}) ({version_str}) ({cache_str})")


    return maps


def compare_version(a: str, b: str) -> int:
    """
    Return:
      1  if a > b
      0  if a == b
     -1  if a < b
    """
    pa = [int(x) for x in a.split('.') if x.isdigit()]
    pb = [int(x) for x in b.split('.') if x.isdigit()]
    for i in range(max(len(pa), len(pb))):
        va = pa[i] if i < len(pa) else 0
        vb = pb[i] if i < len(pb) else 0
        if va > vb: return 1
        if va < vb: return -1
    return 0

def start_http_server(root, port=HTTP_PORT):
    # 使用自定义的 trigger_http_server.py，既能静态服务也能接收布局 POST
    server_script = Path(__file__).parent / 'trigger_http_server.py'
    if not server_script.exists():
        # 兜底：如果脚本不存在，仍然用简单 http.server
        cmd = [sys.executable, '-m', 'http.server', str(port)]
        print(
            f"{FG_CYAN}Starting simple HTTP server at {FG_MAGENTA}http://localhost:{port}/ "
            f"({FG_CYAN}serving {FG_MAGENTA}{root}{FG_CYAN}) [no layout autosave]{RESET}"
        )    
    else:
        cmd = [sys.executable, str(server_script), str(port)]
        print(
            f"{FG_CYAN}Starting Trigger HTTP server at {FG_MAGENTA}http://localhost:{port}/ "
            f"(serving {FG_MAGENTA}{root}{FG_CYAN}){RESET}"
        )

    return subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def _has_cache_files(map_dir: Path, mapname: str) -> bool:
    """
    简单判断该地图目录下是否存在布局缓存文件。
    你可以根据实际情况调整候选文件名。
    """
    candidates = [
        map_dir / f"{mapname}_layout_cache.json",
        map_dir / f"{mapname}_layout.json",
        map_dir / "layout_cache.json",
    ]
    return any(p.exists() for p in candidates)

def open_graph_entry(entry, skip_physics: bool = False):
    """Open the given graph entry in browser.
    If skip_physics=True, append ?skip_physics=1 so HTTP server can disable vis physics on load.
    """
    # ensure files are in place
    html = entry['html']
    rel = html.relative_to(ROOT)

    if skip_physics:
        url = f'http://localhost:{HTTP_PORT}/{rel.as_posix()}?skip_physics=1'
    else:
        url = f'http://localhost:{HTTP_PORT}/{rel.as_posix()}'
        
    print(f"{FG_CYAN}Opening {FG_MAGENTA}{url}{FG_CYAN}...{RESET}")
    webbrowser.open(url)

def _find_cache_files(map_dir: Path, mapname: str):
    """
    返回该地图目录下可能的布局缓存文件列表。
    目前已知命名：<mapname>_layout.json
    同时预留几种扩展写法：
      - <mapname>_layout.json
      - <mapname>_layout_*.json
      - <mapname>_layout_cache*.json
      - layout_cache*.json
    """
    patterns = [
        f"{mapname}_layout.json",
        f"{mapname}_layout_*.json",
        f"{mapname}_layout_cache*.json",
        "layout_cache*.json",
    ]
    files = []
    for pat in patterns:
        files.extend(map_dir.glob(pat))
    # 去重
    return list({p for p in files if p.exists()})


def _cache_status(map_dir: Path, mapname: str) -> str:
    """
    返回缓存状态：
      - 'CACHED'
      - 'CACHE_OUTDATED'
      - 'NOT_CACHED'
    """
    cache_files = _find_cache_files(map_dir, mapname)
    if not cache_files:
        return 'NOT_CACHED'

    mapfile = find_mapfile_for(mapname)
    if mapfile and mapfile.exists():
        latest_cache_mtime = max(p.stat().st_mtime for p in cache_files)
        if mapfile.stat().st_mtime > latest_cache_mtime:
            return 'CACHE_OUTDATED'
    return 'CACHED'


def _collect_source_jsons(map_dir: Path, mapname: str):
    """
    收集 triggers/actions/events/locals 四个源 JSON（带 mapname 前缀或无前缀都尝试）。
    返回实际存在的文件列表。
    """
    out = []
    for key in ("triggers", "actions", "events", "locals"):
        p1 = map_dir / f"{mapname}_{key}.json"
        p2 = map_dir / f"{key}.json"
        if p1.exists():
            out.append(p1)
        elif p2.exists():
            out.append(p2)
    return out


def _source_outdated(map_dir: Path, mapname: str) -> bool:
    """
    若 .map 比任何一个源 JSON 更新，则认为源 JSON 过时。
    若 .map 或 4 个 JSON 不齐，则返回 False（交给其它逻辑处理）。
    """
    src_files = _collect_source_jsons(map_dir, mapname)
    if len(src_files) < 4:
        return False
    mapfile = find_mapfile_for(mapname)
    if not (mapfile and mapfile.exists()):
        return False
    latest_src_mtime = max(p.stat().st_mtime for p in src_files)
    return mapfile.stat().st_mtime > latest_src_mtime


def _spawn_background_map_parser(map_name: str, map_dir: Path):
    """在后台调用 map_parser.py 来生成缺失的 JSON。
    返回 (event, result) 其中 event 为 threading.Event，可被等待；result 为 dict，
    在进程结束后填充 'pid','log_path','written_files'。
    """
    mapfile = find_mapfile_for(map_name)
    if not mapfile:
        print(f"{FG_RED}No corresponding .map file found; cannot auto-repair JSON: "
              f"{FG_MAGENTA}{map_name}{RESET}")
        return None, None

    candidates = [Path(__file__).parent / 'map_parser.py', Path('map_parser.py'), Path('tools') / 'map_parser.py']
    parser_py = None
    for c in candidates:
        if c.exists():
            parser_py = c
            break
    if not parser_py:
        yellow_msg("Could not locate map_parser.py; cannot auto-repair JSON (tried: " + ', '.join(str(c) for c in candidates) + ")")
        return None, None

    import subprocess, sys as _sys
    repo_root = Path(__file__).resolve().parents[1]

    # ensure map_dir exists
    map_dir.mkdir(parents=True, exist_ok=True)

    try:
        # start subprocess but send its stdout/stderr to DEVNULL so we don't interleave logs
        proc = subprocess.Popen([_sys.executable, str(parser_py), str(mapfile)], cwd=str(repo_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        red_msg(f"Failed to start background repair: {e}")
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


def _run_map_parser_sync(map_path: Path, show_progress: bool = True) -> bool:
    """
    同步调用 map_parser.py 解析 .map，重写 triggers/actions/events/locals/report。
    返回 True 表示成功，False 表示失败或超时。
    """
    candidates = [
        Path(__file__).parent / 'map_parser.py',
        ROOT / 'map_parser.py',
        ROOT / 'tools' / 'map_parser.py',
    ]
    parser_py = None
    for c in candidates:
        if c.exists():
            parser_py = c
            break
    if not parser_py:
        if show_progress:
            yellow_msg("Could not locate map_parser.py; skip re-parsing.")
        return False

    if show_progress:
        print(f"{FG_CYAN}Re-parsing .map with {FG_MAGENTA}{parser_py}{FG_CYAN} ...{RESET}")
    cmd = [sys.executable, str(parser_py), str(map_path)]
    try:
        with subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT
        ) as p:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if show_progress:
                    yellow_msg("Map parsing still running after 30s, aborting. This is most likely bugged.")
                return False
            if p.returncode != 0:
                if show_progress:
                    red_msg(f"Map parsing failed (exit {p.returncode}).")
                return False
    except Exception as e:
        if show_progress:
            red_msg(f"Failed to run map_parser: {e}")
        return False

    if show_progress:
        cyan_msg('Map parsing finished.')
    return True


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


def generate_from_map(map_path, auto_open=False, show_progress: bool = True):
    """从 .map 重新解析 + 清理缓存 + 生成新的 trigger graph。
    返回 True 表示成功，False 表示失败。
    """
    map_path = Path(map_path)
    mapname = map_path.stem
    map_dir = MAPS_DIR / mapname

    # 1) 先解析 .map -> 源 JSON
    if not _run_map_parser_sync(map_path, show_progress=show_progress):
        if show_progress:
            yellow_msg('Skip graph generation due to map parsing failure/timeout.')
        return False

    # 2) 删除旧的布局缓存
    cache_files = _find_cache_files(map_dir, mapname)
    if cache_files and show_progress:
        files_str = ", ".join(f"{FG_MAGENTA}{p.name}{RESET}" for p in cache_files)
        print(f"{FG_CYAN}Clearing layout cache files: {files_str}{RESET}")
    for cf in cache_files:
        try:
            cf.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            if show_progress:
                red_msg(f"  Failed to remove {cf}: {e}")

    # 3) 调用 visualize_triggers 生成新图
    if show_progress:
        print(f"{FG_CYAN}Generating graph for {FG_MAGENTA}{map_path}{FG_CYAN}...{RESET}")

    vt = ROOT / 'tools' / 'visualize_triggers.py'
    if vt.exists():
        script_path = vt
    else:
        script_path = ROOT / 'visualize_triggers.py'

    cmd = [sys.executable, str(script_path), '--map', str(map_path), '--quiet']
    stop_event = threading.Event()
    spinner_thread = None
    if show_progress:
        spinner_thread = threading.Thread(
            target=_spinner,
            args=(f"{FG_CYAN}Generating {FG_MAGENTA}{map_path.name}{FG_CYAN}...{RESET}", stop_event),
            daemon=True
        )
        spinner_thread.start()

    repo_root = ROOT
    report_path = repo_root / 'data' / 'maps' / f"{mapname}" / f"{mapname}_report.json"

    try:
        with subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT
        ) as p:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                stop_event.set()
                if spinner_thread:
                    spinner_thread.join()
                if show_progress:
                    yellow_msg("Generation still running after 30s, aborting. This is most likely to have bugged.")
                    print(f"{FG_YELLOW}Please check the generation report (if any): {FG_MAGENTA}{report_path}{RESET}")
                return False

            if p.returncode != 0:
                raise subprocess.CalledProcessError(p.returncode, cmd)

    except subprocess.CalledProcessError as e:
        stop_event.set()
        if spinner_thread:
            spinner_thread.join()
        red_msg(f'Generation failed (exit {getattr(e, "returncode", "?")}).')
        print('You can:')
        print(f"  Enter {FG_CYAN}'a'{RESET} to open generation report")
        print(f"  Enter {FG_CYAN}'b'{RESET} to return to main menu")
        choice = input('Your choice: ').strip().lower()
        if choice == 'a':
            try:
                webbrowser.open(report_path.as_uri())
            except Exception:
                print(f'{FG_RED}Cannot open {FG_MAGENTA}{report_path}{FG_RED}; please inspect it manually.{RESET}')
        return False
    except Exception as e:
        stop_event.set()
        if spinner_thread:
            spinner_thread.join()
        red_msg(f'Generation failed: {e}')
        print('You can:')
        print(f"  Enter {FG_CYAN}'a'{RESET} to open generation report")
        print(f"  Enter {FG_CYAN}'b'{RESET} to return to main menu")
        choice = input('Your choice: ').strip().lower()
        if choice == 'a':
            try:
                webbrowser.open(report_path.as_uri())
            except Exception:
                print(f'{FG_RED}Cannot open {FG_MAGENTA}{report_path}{FG_RED}; please inspect it manually.{RESET}')
        return False

    stop_event.set()
    if spinner_thread:
        spinner_thread.join()
    if show_progress:
        print(f"{FG_CYAN}Generation finished for {FG_MAGENTA}{map_path.name}{FG_CYAN}.{RESET}")
    return True


def main():
    server = None
    try:
        while True:
            maps = list_maps()
            print('\nOptions:')
            print(f'  Enter {FG_CYAN}index number{RESET} to open that graph')
            print(f"  Enter {FG_CYAN}'g'{RESET} to list .map files and (re)generate graphs")
            print(f"  Enter {FG_CYAN}'q'{RESET} to quit")
            choice = input('\nYour choice: ').strip()
            if choice.lower() == 'q':
                break
            if choice.lower() == 'g':
                mfiles = find_map_files()
                if not mfiles:
                    yellow_msg('No .map files found in project root.')
                    continue
                print('\nFound .map files:')
                for i, m in enumerate(mfiles):
                    print(f'  [{FG_CYAN}{i}{RESET}] {FG_MAGENTA}{m.name}{RESET}')
                print(f"  Enter {FG_CYAN}index{RESET} to generate, {FG_CYAN}'a'{RESET} to generate ALL, or {FG_CYAN}leave blank{RESET} to cancel")
                idx = input('Your choice: ').strip()
                if idx == '':
                    continue
                if idx.lower() == 'a':
                    cyan_msg("Generating all .map files...")
                    for m in mfiles:
                        try:
                            generate_from_map(m)
                        except Exception as ex:
                            red_msg(f"Failed to generate for {m}: {ex}")
                    cyan_msg("All generation attempts finished. Rescanning...")
                    time.sleep(0.3)
                    continue
                try:
                    mi = int(idx)
                    if 0 <= mi < len(mfiles):
                        ok = generate_from_map(mfiles[mi])
                        cyan_msg("Generation done. Rescanning...")
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
                                open_graph_entry(found, skip_physics=False)
                                # after opening, continue outer loop (list will refresh on next iteration)
                                continue
                            else:
                                yellow_msg("Generated output incomplete or not found; not auto-opening.")
                                continue
                except Exception as e:
                    red_msg(f"Invalid selection: {e}")
                    continue
            # otherwise numeric index
            try:
                ix = int(choice)
            except Exception:
                red_msg("Invalid input")
                continue
            if ix < 0 or ix >= len(maps):
                red_msg("Index out of range")
                continue
            entry = maps[ix]
            # check completeness
            has_html = bool(entry.get('has_html'))
            has_node = bool(entry.get('has_node'))
            has_debug = bool(entry.get('has_debug'))
            if not (has_html and has_node and has_debug):
                yellow_msg(f"Selected entry '{entry['map']}' is incomplete.")
                # try to find a .map in project root to generate from
                mapfile = find_mapfile_for(entry['map'])
                if mapfile:
                    cyan_msg(f"Found source .map '{mapfile.name}' — attempting to generate trigger_graph...")
                    try:
                        generate_from_map(mapfile)
                    except subprocess.CalledProcessError as ex:
                        red_msg(f"Generation failed (subprocess error): {ex}")
                        red_msg("Will not open incomplete trigger_graph.")
                        continue
                    except Exception as ex:
                        red_msg(f"Generation failed: {ex}")
                        red_msg("Will not open incomplete trigger_graph.")
                        continue
                    # rescan entries to pick up new/generated files
                    cyan_msg("Generation finished — rescanning...")
                    time.sleep(0.25)
                    maps = find_graphs()
                    # find matching entry again
                    found = None
                    for e in maps:
                        if e['map'] == entry['map']:
                            found = e
                            break
                    if not found:
                        yellow_msg("Generation seemed to run but no output found. Not opening.")
                        continue
                    entry = found
                    has_html = bool(entry.get('has_html'))
                    has_node = bool(entry.get('has_node'))
                    has_debug = bool(entry.get('has_debug'))
                    if not (has_html and has_node and has_debug):
                        yellow_msg("After generation the entry is still incomplete. Not opening.")
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
                    red_msg("Missing files for this entry: " + ', '.join(miss))
                    red_msg("No corresponding .map source found in repository root — cannot auto-generate.")
                    print("Suggestions:")
                    print(
                        f"  - Place the {FG_CYAN}missing sidecar JSON files{RESET} next to the HTML (names above).\n"  
                        f"  - Or run this script and press {FG_CYAN}'g'{RESET} to generate from a .map if you have one.\n"
                        f"  - Or run: {FG_CYAN}python visualize_triggers.py --map <mapname>{RESET} to generate manually.")
                    continue

            mapname = entry['map']
            map_dir = entry.get('html').parent if entry.get('html') else (MAPS_DIR / mapname)

            # ensure HTTP server
            if server is None:
                server = start_http_server(ROOT)
                # give server a moment
                time.sleep(0.3)

            # 如果源 JSON 早于 .map，给出提醒
            try:
                if _source_outdated(map_dir, mapname):
                    print(f"{FG_YELLOW}Note: source JSONs for "
                          f"{FG_MAGENTA}'{mapname}' "
                          f"{FG_YELLOW}seem older than the .map file.{RESET}")
                    print(f"{FG_YELLOW}The graph may not reflect latest changes. Consider regenerating via "
                          f"{FG_CYAN}'g'{FG_YELLOW}.{RESET}\n")
            except Exception:
                pass

            # Before opening: check whether the original source JSONs (triggers/actions/events/locals)
            # are present in the map directory. If missing, we will still open the HTML but spawn a
            # background map_parser to auto-fill them and inform the user.
            def _has_source_jsons(d: Path, name: str) -> bool:
                # require ALL four source JSONs to be present; otherwise consider incomplete
                return all((d / f"{name}_{k}.json").exists() or (d / f"{k}.json").exists()
                           for k in ("triggers", "actions", "events", "locals"))

            if not _has_source_jsons(map_dir, mapname):
                print(f"{FG_YELLOW}Note: source JSONs (triggers/actions/events/locals) are missing. Repair started in background for: "
                      f"{FG_MAGENTA}{mapname}{FG_YELLOW} (generation info will be written to {FG_MAGENTA}{mapname}_report.json{FG_YELLOW}){RESET}")
                done_event, result = _spawn_background_map_parser(mapname, map_dir)
            else:
                done_event = None
                result = None

            # Version check before opening
            try:
                import json
                with open(entry['debug_json'], 'r', encoding='utf-8') as f:
                    dbg = json.load(f)
                file_ver = dbg.get('tool_version')
                if isinstance(file_ver, str) and compare_version(file_ver, TOOL_VERSION) < 0:
                    print(f"{FG_YELLOW}Note: this trigger graph was generated using an older version {FG_CYAN}({file_ver}).{RESET}")
                    print(f"{FG_YELLOW}It is recommended to re-generate using the latest visualization tool.{RESET}\n")
            except Exception:
                pass

            # finally open the graph
            try:
                cache_status = _cache_status(map_dir, mapname)
            except Exception:
                cache_status = 'NOT_CACHED'
            
            # 只在缓存状态为 CACHED 时跳过物理引擎，以提升加载速度
            skip_physics = (cache_status == 'CACHED')

            if skip_physics:
                cyan_msg(
                    f"Layout cache for {FG_MAGENTA}{mapname}{FG_CYAN} is up-to-date; "
                    f"will skip physics simulation on load for faster rendering.{RESET}"
                )

            open_graph_entry(entry, skip_physics=skip_physics)

            # If we spawned a background repair, wait for it to finish now and report a concise summary.
            if done_event is not None:
                # wait with a small dot-progress to avoid blocking print interleaving
                print(f' {FG_CYAN}Waiting for background repair to complete...{RESET}', end='', flush=True)
                while not done_event.wait(timeout=0.5):
                    print(f'{FG_CYAN}.{RESET}', end='', flush=True)
                print(f'\n{FG_GREEN}Repair complete.{RESET}', end=' ')
                try:
                    # 简洁单行提示，用户可自行查看目录或 report
                    print(f'{FG_GREEN}Repair complete - please check: {FG_MAGENTA}{map_dir}{RESET}')
                except Exception:
                    green_msg("Repair complete - please check the corresponding directory for details.")
    finally:
        if server:
            cyan_msg('Stopping HTTP server...')
            server.terminate()


if __name__ == '__main__':
    main()
