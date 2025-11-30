#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
visualize_triggers_full.py — Build an interactive trigger graph for YR/Mental Omega maps.

Features
- Load map JSONs from ./data/maps/<mapname>/<mapname>_{triggers,events,actions,locals}.json
- Load merged YAML dictionaries (actions_all.yml / conditions_all.yml) from ./data/dicts/merged
- Optional overrides from ./data/dicts/overrides (won't override explicit merged fields)
- Graph:
  * Node = Trigger (one per ID). Tooltip aggregates Triggers / Events / Actions.
  * Edges from Actions (via actions dict `produces_edges`).
  * Edges from Events (via conditions dict `references` role=depends_on for local_id/local_var).
- QoL visualization:
  * No hover tooltips; click shows info box near cursor.
  * Highlight selected node & neighbors; dim others (nodes+edges).
  * Edge labels removed; arrows & lines semi-transparent by default.
  * Node size scales with degree; physics repulsion tuned to “spread out”.

CLI
  python visualize_triggers.py --map yours
  python visualize_triggers.py --map-dir data/maps/yours
  python visualize_triggers.py --out mygraph.html
"""

from __future__ import annotations
import sys, json, argparse
from pathlib import Path

# Repository root (two levels up from tools/ file)
REPO_ROOT = Path(__file__).resolve().parents[1]

TOOL_VERSION = "1.4.2"

# In-memory generation log collector. Messages appended here will be
# written into the map's *_report.json under `generation_log` when done.
_GEN_LOG: list[str] = []

def _log(msg: str, level: str = 'INFO', print_always: bool = False, *, quiet: bool = False):
    import time
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    entry = f"[{ts}] {level}: {msg}"
    try:
        _GEN_LOG.append(entry)
    except Exception:
        pass
    if not quiet or print_always:
        try:
            print(entry)
        except Exception:
            try:
                print(entry.encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                pass

# ---------- dependency checks ----------
missing = []
try:
    import yaml
except Exception:
    missing.append('pyyaml')
try:
    import networkx as nx
except Exception:
    missing.append('networkx')
try:
    from pyvis.network import Network
except Exception:
    missing.append('pyvis')

if missing:
    print('⚠️ Missing dependencies: ' + ', '.join(missing))
    print('   pip install ' + ' '.join(missing))
    sys.exit(1)

import yaml, networkx as nx
from pyvis.network import Network

# ---- user config loader ----
from pathlib import Path
import json as _json
try:
    import tomllib as _toml  # py3.11+
except Exception:
    _toml = None

def _deep_merge(a: dict, b: dict) -> dict:
    """shallow+dict递归：b覆盖a，仅dict做下钻合并"""
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_user_config(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    p = path
    try:
        if p.suffix.lower() in {".yml", ".yaml"}:
            import yaml  # 已有依赖
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if p.suffix.lower() == ".json":
            return json.loads(p.read_text(encoding="utf-8"))
        if p.suffix.lower() == ".toml" and _toml:
            return _toml.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

# ---- visual config defaults ----
DEFAULT_CFG = {
    "ui": {                                  # 可调：主题 'dark'/'light'
        "theme": "dark",
        "bg_color_dark":   "#0f172a",        # 背景与文字基色
        "bg_color_light":  "#eeeeee",
        "font_color_dark": "#e5e7eb",
        "font_color_light":"#111111",
    },
    "edges": {                               # 可调：连线的颜色代码
        "dark": {
            "enable":           "#43ff5c",
            "disable":          "#ff4444",
            "destroy":          "#a0aec0",
            "force":            "#64a0ff",
            "enable_local":     "#43ff5c",
            "disable_local":    "#ff4444",
            "depends_on_true":  "#b3b9c5",
            "depends_on_false": "#b3b9c5",
            "depends_on":       "#b3b9c5",
            "linked":           "#ff7fc3",
        },
        "light": {
            "enable":           "#2cb600",
            "disable":          "#ff4444",
            "destroy":          "#111827",
            "force":            "#328af0",
            "enable_local":     "#2cb600",
            "disable_local":    "#ff4444",
            "depends_on_true":  "#6b7280",
            "depends_on_false": "#6b7280",
            "depends_on":       "#6b7280",
            "linked":           "#8b5cf6",
        }
    },
    "nodes": {                               # 结点样式：触发/本地变量/未知
        "dark": {                            # 暗色主题下提高“亮度对比”，避免远景发灰
            "trigger":   {"shape":"dot",     "color":"#7aa2f7"},
            "local_var": {"shape":"diamond", "color":"#f6c177"},
            "unknown":   {"shape":"ellipse", "color":"#a8b1c7"},
        },
        "light": {
            "trigger":   {"shape":"dot",     "color":"#60a5fa"},
            "local_var": {"shape":"diamond", "color":"#fbbf24"},
            "unknown":   {"shape":"ellipse", "color":"#9ca3af"},
        }
    },
    "interact": {                            # 注入到前端JS的可调参数
        "Z_MIN": 0.3,
        "Z_MAX": 0.6,
        "OP_AT_FAR": 1.0,
        "OP_AT_NEAR": 0.45,
        "INCLUDE_TWO_HOPS": True,
        "EDGE_HILITE_MODE": "outgoing",      # outgoing|incoming|both
        "TOOLTIP_TRACKING": "zoom_only",     # zoom_only|zoom_and_drag|always|none
        # 当缩放倍率低于此值时自动隐藏节点 labels；参考 high/low highlight 的阈值
        # 废弃功能，现在“技术上”会始终显示标签，不过它们实际上会在过小的缩放倍率下不可见
        # 保留的唯一原因是尽量减少代码的修改以免不可预见的问题
        "LABEL_HIDE_BELOW": 0.0,
        "HUD_FADE_DELAY_MS": 2000,
        "HUD_FADE_DURATION_MS": 1000,
        "DIM_UPDATE_INTERVAL_MS": 120,       # 结点/边透明度渐变更新间隔（毫秒）
        # 限制透明度更新的频率，以减少前端性能开销，提升帧数
    }
}

# layout defaults for adaptive physics iterations
DEFAULT_LAYOUT = {
    # thresholds by node count; counts > large_threshold use iterations_large, > medium_threshold use iterations_medium
    "medium_threshold": 1500,
    "large_threshold": 3000,
    "iterations_default": 120,
    "iterations_medium": 60,
    "iterations_large": 30,
}

# debug defaults
DEFAULT_DEBUG = {
    "enable": False,
}

# ---- try load external config (优先级更高) ----
# 你可以把路径定成 data/config/trigger_viz.yml 或同目录 config.yml
CFG_PATHS = [
    Path(REPO_ROOT / "config.yml"),
    Path("data/config/trigger_viz.yml"),
    Path("data/config/trigger_viz.json"),
    Path("data/config/trigger_viz.toml"),
    Path(__file__).with_name("config.yml"),
]
_user_cfg = {}
for _p in CFG_PATHS:
    if _p.exists():
        _user_cfg = load_user_config(_p)
        break

CFG = _deep_merge(DEFAULT_CFG, _user_cfg)
# merge debug defaults (allow debug: true/false or debug: { ... })
_user_dbg = CFG.get('debug', {})
if not isinstance(_user_dbg, dict):
    _user_dbg = {'enable': bool(_user_dbg)}
CFG['debug'] = _deep_merge(DEFAULT_DEBUG, _user_dbg)
# Normalize debug config to only expose the 'enable' flag to generated assets
CFG['debug'] = {'enable': bool(CFG.get('debug', {}).get('enable', False))}

# ---- derive runtime constants from CFG ----
THEME = (CFG["ui"]["theme"] or "dark").lower()
EDGE_COLOR = CFG["edges"]["dark" if THEME=="dark" else "light"]
NODE_STYLE = CFG["nodes"]["dark" if THEME=="dark" else "light"]
BG_COLOR   = CFG["ui"]["bg_color_dark" if THEME=="dark" else "bg_color_light"]
FONT_COLOR = CFG["ui"]["font_color_dark" if THEME=="dark" else "font_color_light"]

# ---- label normalizer: keep one canonical name ----
LABEL_ALIASES = {
    "set_local": "enable_local",   # 统一：set_local -> enable_local
    "enable_local": "enable_local",
    "clear_local": "disable_local",# 如后续有 clear_local 也统一
}

def canon_label(lbl: str) -> str:
    if not lbl:
        return lbl
    return LABEL_ALIASES.get(lbl, lbl)

# ---------- IO helpers ----------
def load_json(path: Path):
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)

def _to_int(x):
    try:
        return int(str(x).strip())
    except Exception:
        return None

def _split_csv(s):
    return [t.strip() for t in str(s).split(',')]

def load_actions_dict(yml_path: Path) -> dict[int, dict]:
    doc = yaml.safe_load(yml_path.read_text(encoding='utf-8')) or {}
    actions = doc.get('actions', doc)
    out = {}
    for k, v in (actions or {}).items():
        try:
            out[int(k)] = v or {}
        except Exception:
            pass
    return out

def load_conditions_dict(yml_path: Path) -> dict[int, dict]:
    doc = yaml.safe_load(yml_path.read_text(encoding='utf-8')) or {}
    conds = doc.get('conditions', doc)
    out = {}
    for k, v in (conds or {}).items():
        try:
            out[int(k)] = v or {}
        except Exception:
            pass
    return out

def merge_overrides(base: dict[int, dict], override_path: Path, top_key: str, *, quiet: bool = False):
    """Shallow merge: only add/override specific subkeys like produces_edges / references."""
    if not override_path.exists():
        return
    try:
        doc = yaml.safe_load(override_path.read_text(encoding='utf-8')) or {}
        block = doc.get(top_key, doc) or {}
        for k, v in block.items():
            try:
                code = int(k)
            except Exception:
                code = k
            cur = base.setdefault(code, {})
            for kk, vv in (v or {}).items():
                cur[kk] = vv
        _log(f"Applied overrides from {override_path}", level='INFO', quiet=quiet)
    except Exception as e:
        _log(f"Failed to apply overrides from {override_path}: {e}", level='WARNING', quiet=quiet)

# ---------- normalization ----------
def _iter_actions_normalized(acts):
    """Yield {'code': int, 'params': [p1..p7]} from JSON forms."""
    if acts is None:
        return
    # container
    if isinstance(acts, dict) and "actions" in acts:
        acts = acts.get("actions", [])
    for a in acts:
        # dict act_id / p1..p7
        if isinstance(a, dict) and ("act_id" in a or "p1" in a):
            code = _to_int(a.get("act_id"))
            if code is None:
                continue
            ps = [a.get(f"p{i}") for i in range(1, 8)]
            if ps and isinstance(ps[-1], str) and ps[-1].upper() == 'A':
                ps = ps[:-1]
            out = []
            for t in ps[:7]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 7:
                out.append(0)
            yield {"code": code, "params": out[:7]}
            continue
        # loose dict
        if isinstance(a, dict):
            code = a.get('code') or a.get('action') or a.get('A1')
            code = _to_int(code)
            if code is None:
                continue
            params = a.get('params')
            if params is None:
                params = [a.get('A1P1'), a.get('A1P2'), a.get('A1P3'),
                          a.get('A1P4'), a.get('A1P5'), a.get('A1P6'), a.get('A1P7')]
            ps = list(params or [])
            if ps and isinstance(ps[-1], str) and ps[-1].upper() == 'A':
                ps = ps[:-1]
            out = []
            for t in ps[:7]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 7:
                out.append(0)
            yield {"code": code, "params": out[:7]}
            continue
        # list/tuple
        if isinstance(a, (list, tuple)):
            if not a:
                continue
            code = _to_int(a[0])
            if code is None:
                continue
            ps = list(a[1:])
            if ps and isinstance(ps[-1], str) and ps[-1].upper() == 'A':
                ps = ps[:-1]
            out = []
            for t in ps[:7]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 7:
                out.append(0)
            yield {"code": code, "params": out[:7]}
            continue
        # csv
        if isinstance(a, str):
            toks = _split_csv(a)
            if not toks:
                continue
            code = _to_int(toks[0])
            if code is None:
                continue
            ps = toks[1:]
            if ps and ps[-1].upper() == 'A':
                ps = ps[:-1]
            out = []
            for t in ps[:7]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 7:
                out.append(0)
            yield {"code": code, "params": out[:7]}
            continue

def _iter_events_normalized(conds):
    """Yield {'code': int, 'params': [p1..p3]} from JSON forms."""
    if conds is None:
        return
    if isinstance(conds, dict) and "conditions" in conds:
        conds = conds.get("conditions", [])
    for e in conds:
        if isinstance(e, dict) and ("cond_id" in e or "p1" in e):
            code = _to_int(e.get("cond_id"))
            if code is None:
                continue
            ps = [e.get("p1"), e.get("p2"), e.get("p3")]
            out = []
            for t in ps[:3]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 3: out.append(0)
            yield {"code": code, "params": out[:3]}
            continue
        if isinstance(e, dict):
            code = e.get('code') or e.get('event') or e.get('E1')
            code = _to_int(code)
            if code is None:
                continue
            params = e.get('params')
            if params is None:
                params = [e.get('E1P1'), e.get('E1P2'), e.get('E1P3')]
            ps = list(params or [])
            out = []
            for t in ps[:3]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 3: out.append(0)
            yield {"code": code, "params": out[:3]}
            continue
        if isinstance(e, (list, tuple)):
            if not e: continue
            code = _to_int(e[0])
            if code is None: continue
            ps = list(e[1:])
            out = []
            for t in ps[:3]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 3: out.append(0)
            yield {"code": code, "params": out[:3]}
            continue
        if isinstance(e, str):
            toks = _split_csv(e)
            if not toks: continue
            code = _to_int(toks[0])
            if code is None: continue
            ps = toks[1:]
            out = []
            for t in ps[:3]:
                ti = _to_int(t)
                out.append(ti if ti is not None else t)
            while len(out) < 3: out.append(0)
            yield {"code": code, "params": out[:3]}
            continue

def _short(s: str|None, n=22) -> str:
    if not s: return ""
    s = str(s)
    return s if len(s) <= n else (s[:n] + "…")

PAD_KEYS = {
    'trigger_id','trigger',              # 触发
    'team_id','team',                    # 队伍
    'taskforce_id','taskforce',          # 兵力编成
    'script_id','script',                # 脚本
}

def _should_pad(key: str|None) -> bool:
    if not key: return False
    k = str(key).lower()
    return (k in PAD_KEYS) or (k.endswith('_trigger')) or (k.endswith('_id') and any(t in k for t in ['trigger','team','taskforce','script']))

def _fmt_val(v, key: str|None=None):
    if v is None or v == "": return "0"
    # 只在“应当零填充”的键上做 zfill(8)；其余保持原样
    if isinstance(v, int) and _should_pad(key) and 0 <= v <= 99_999_999:
        return str(v).zfill(8)
    return str(v)

# ---------- helpers: id formatting & waypoint letters ----------
def _pad8(n: int) -> str:
    try:
        return f"{int(n):08d}"
    except Exception:
        return str(n)

def _letters_to_waypoint(s: str | int) -> int | str:
    """
    将路径点字母标签转换为整数索引：
      A..Z   -> 0..25
      AA..AZ -> 26..51
      BA..BZ -> 52..77
      ...
    规则：两位字母的序号 = 26 + 26*(首字母序) + (次字母序)，其中 A=0, B=1, ...
    纯数字或可转数字的字符串则直接转 int；无法解析则原样返回。
    """
    # 先处理数字/数字字符串
    try:
        # 若本身就是 int 或者是像 "12" 这样的数字字符串，直接返回数值
        return int(s)
    except Exception:
        pass

    if not isinstance(s, str):
        return s

    su = s.strip().upper()
    if not su.isalpha():
        return s  # 混合或其他格式，保持原样

    if len(su) == 1:
        # 单字母：A..Z => 0..25
        return ord(su[0]) - ord('A')

    if len(su) == 2:
        # 两字母：从 26 起算
        a = ord(su[0]) - ord('A')
        b = ord(su[1]) - ord('A')
        return 26 + 26 * a + b

    # 三位及以上（通常不会出现），保守返回原值
    return s

def _fmt_val_with_type(val, type_hint: str | None, key_name: str = "") -> str:
    """
    按“类型提示”渲染值；若没有类型提示，使用字段名进行启发式判断。
    """
    t = (type_hint or "").lower()
    k = (key_name or "").lower()

    # ---- 强类型分支 ----
    if t in {"trigger_id", "teamtype_id", "taskforce_id", "script_id"}:
        return _pad8(val)
    if t == "waypoint_id":
        wp = _letters_to_waypoint(val)
        return str(wp) if isinstance(wp, int) else str(val)
    if t in {"techno_id", "building_type", "csf_label", "sound_id", "speech_id", "eva_label"}:
        return str(val)
    if t in {"house_id"}:
        # house 在你的数据里通常是名字或索引，这里原样展示（需要零填充时可按需扩展）
        return str(val)
    if t in {"local_id"}:
        return str(int(val))  # 局部变量索引不做零填充

    # ---- 无类型提示时的启发式兜底 ----
    if "waypoint" in k or k.endswith("_wp") or k.endswith("_waypoint"):
        wp = _letters_to_waypoint(val)
        return str(wp) if isinstance(wp, int) else str(val)

    # 常见“数值含义”的名字：秒数/帧数/数量
    if any(x in k for x in ["second", "frame", "count", "credits", "amount", "radius", "range", "delay"]):
        try:
            return str(int(val))
        except Exception:
            return str(val)

    # 其他：保持字符串化即可
    return str(val)

# 当没有任何可展示参数时，用这句做兜底占位
FALLBACK_ON_EMPTY = "params=null"

def format_action_entry(code: int, params: list, actions_dict: dict) -> str:
    meta = (actions_dict.get(code) or {})
    name = meta.get("name") or f"Action {code}"
    pieces: list[str] = []
    used_params: set[int] = set()

    # 1) value_fields 优先
    for vf in (meta.get("value_fields") or []):
        p = vf.get("param")
        label = vf.get("name") or f"P{p}"
        if isinstance(p, int) and 1 <= p <= 7:
            pieces.append(f"{label}={_fmt_val_with_type(params[p-1], None, label)}")
            used_params.add(p)

    # 2) references 作为补充（未出现的才加）
    for ref in (meta.get("references") or []):
        p = ref.get("param")
        if isinstance(p, int) and 1 <= p <= 7 and p not in used_params:
            type_hint = ref.get("type")
            label = ref.get("type") or f"P{p}"
            pieces.append(f"{label}={_fmt_val_with_type(params[p-1], type_hint, label)}")
            used_params.add(p)

    # 3) 兜底：只有在 1) 和 2) 都没有任何输出时，才给出占位提示
    if not pieces:
        return f"{name} ({FALLBACK_ON_EMPTY})"
    else:
        return f"{name} (" + ", ".join(pieces) + ")"

def format_event_entry(code: int, params: list, conditions_dict: dict) -> str:
    meta = (conditions_dict.get(code) or {})
    name = meta.get("name") or f"Event {code}"
    pieces: list[str] = []
    used_params = set()  # 记录已输出过的参数下标（1-based）

    # 1) 先按 value_fields 输出（带字段名，优先级高）
    for vf in (meta.get("value_fields") or []):
        p = vf.get("param")
        key = vf.get("name") or f"P{p}"
        if isinstance(p, int) and 1 <= p <= len(params):
            pieces.append(f"{key}={_fmt_val(params[p-1], key)}")
            used_params.add(p)

    # 2) 再把 references 中需要展示的参数补上（未出现过的才加，避免重复）
    for ref in (meta.get("references") or []):
        p = ref.get("param")
        if isinstance(p, int) and 1 <= p <= len(params) and p not in used_params:
            key = ref.get("type") or f"P{p}"
            pieces.append(f"{key}={_fmt_val(params[p-1], key)}")
            used_params.add(p)

    # 3) 兜底：只有在 1) 和 2) 都没有任何输出时，才给出占位提示
    if not pieces:
        return f"{name} ({FALLBACK_ON_EMPTY})"
    else:
        return f"{name} (" + ", ".join(pieces) + ")"

# ---------- graph builder ----------
def build_graph(triggers_json, actions_json, events_json, actions_dict, conditions_dict, locals_dict=None):
    if locals_dict is None: locals_dict = {}
    G = nx.DiGraph()

    # Prepare trigger nodes
    for tid, t in triggers_json.items():
        name  = t.get('name') or t.get('Name') or ''
        house = t.get('house') or t.get('HOUSE') or t.get('House') or ''
        label = f"{tid}\n{_short(name)}" if name else str(tid)
        title_lines = []
        if name: title_lines.append(f"<b>{name}</b>")
        if house: title_lines.append(f"House: {house}")
        title_lines.append(f"ID: {tid}")
        G.add_node(tid, type='trigger', label=label, name=name, house=house,
                   _sum_actions=[], _sum_events=[], title="\n".join(title_lines))
        
    # After adding trigger nodes:
    for tid, t in triggers_json.items():
        linked = t.get('linked') or t.get('LINKED_TRIGGER') or t.get('linked_trigger') or ''
        linked = str(linked).strip()
        if not linked or linked.lower() in ('<none>', 'none', 'null', '0'):
            continue
        # 统一 8 位
        linked_id = linked.zfill(8) if linked.isdigit() and len(linked) <= 8 else linked
        if linked_id not in G:
            G.add_node(linked_id, type='trigger', label=linked_id, _sum_actions=[], _sum_events=[], title=f"ID: {linked_id}")
        # 画一条“关联”边
        G.add_edge(linked_id, tid, 
                   label=canon_label('linked'), 
                   style='dot')  

    # Actions => edges & action summary
    for tid, acts in actions_json.items():
        if tid not in G:
            G.add_node(tid, type='trigger', label=str(tid), _sum_actions=[], _sum_events=[], title=f"ID: {tid}")
        for a in _iter_actions_normalized(acts):
            code = a["code"]; params = a["params"]
            # action name for summary
            astr = format_action_entry(code, params, actions_dict)
            G.nodes[tid]["_sum_actions"].append(astr)

            # produce edges from dict
            for em in (actions_dict.get(code, {}) or {}).get("produces_edges", []) or []:
                to_type    = em.get("to")
                from_param = em.get("from_param")
                elabel     = em.get("label", "")
                style      = em.get("style", "solid")
                if not isinstance(from_param, int) or not (1 <= from_param <= 7):
                    continue
                target_raw = params[from_param - 1] if from_param - 1 < len(params) else None
                if target_raw is None:
                    continue
                if to_type == "trigger_id":
                    sraw = str(target_raw)
                    target_id = sraw.zfill(8) if sraw.isdigit() and len(sraw) <= 8 else sraw
                    if target_id not in G:
                        G.add_node(target_id, type='trigger', label=str(target_id), _sum_actions=[], _sum_events=[], title=f"ID: {target_id}")
                elif to_type in ("local_id", "local_var"):
                    target_id = f"local:{target_raw}"
                    linfo = locals_dict.get(str(target_raw)) or {}
                    lname = linfo.get("name") or linfo.get("Name")
                    linitial = linfo.get("initial")
                    llabel = f"Local {target_raw}" + (f"\n{lname}" if lname else "")
                    ltitle = f"<b>Local {target_raw}</b>"
                    if lname: ltitle += f"<br>Name: {lname}"
                    if linitial is not None: ltitle += f"<br>Initial: {linitial}"
                    if target_id not in G:
                        G.add_node(target_id, type='local_var', label=llabel, title=ltitle, initial=linitial)
                else:
                    target_id = f"{to_type}:{target_raw}"
                    if target_id not in G:
                        G.add_node(target_id, type=to_type or 'unknown', label=str(target_id), title=str(target_id))
                G.add_edge(tid, target_id, 
                           label=canon_label(elabel), 
                           style=style)

    # Events => local depends_on edges & event summary
    for tid, conds in events_json.items():
        if tid not in G:
            G.add_node(tid, type='trigger', label=str(tid), _sum_actions=[], _sum_events=[], title=f"ID: {tid}")
        for e in _iter_events_normalized(conds):
            code = e["code"]; params = e["params"]
            estr = format_event_entry(code, params, conditions_dict)
            G.nodes[tid]["_sum_events"].append(estr)

            refs = (conditions_dict.get(code, {}) or {}).get("references", []) or []
            local_pidxes = [r.get("param") for r in refs
                            if r.get("type") in ("local_id","local_var") and r.get("role")=="depends_on"]
            for pidx in local_pidxes:
                if not isinstance(pidx, int) or not (1 <= pidx <= 3): continue
                target_raw = params[pidx-1] if pidx-1 < len(params) else None
                if target_raw is None: continue
                local_id = f"local:{target_raw}"
                linfo = locals_dict.get(str(target_raw)) or {}
                lname = linfo.get("name") or linfo.get("Name")
                linitial = linfo.get("initial")
                llabel = f"Local {target_raw}" + (f"\n{lname}" if lname else "")
                ltitle = f"<b>Local {target_raw}</b>"
                if lname: ltitle += f"<br>Name: {lname}"
                if linitial is not None: ltitle += f"<br>Initial: {linitial}"
                if local_id not in G:
                    G.add_node(local_id, type='local_var', label=llabel, title=ltitle, initial=linitial)
                lb1 = (
                    "depends_on_true"  if code == 36 else
                    "depends_on_false" if code == 37 else
                    "depends_on"
                )
                G.add_edge(local_id, tid, 
                           label=canon_label(lb1), 
                           style="dashed")

    # finalize titles (append summaries)
    for nid, attrs in G.nodes(data=True):
        if attrs.get("type") != "trigger":
            continue
        lines = [attrs.get("title","")]
        evs = attrs.get("_sum_events", [])
        acts = attrs.get("_sum_actions", [])
        if evs:
            lines.append("<hr><b>Events</b>")
            for s in evs[:10]: lines.append(f"• {s}")
            if len(evs)>10: lines.append(f"…(+{len(evs)-10} more)")
        if acts:
            lines.append("<hr><b>Actions</b>")
            for s in acts[:10]: lines.append(f"• {s}")
            if len(acts)>10: lines.append(f"…(+{len(acts)-10} more)")
        attrs["title"] = "<br>".join(lines)
        if "_sum_events" in attrs: del attrs["_sum_events"]
        if "_sum_actions" in attrs: del attrs["_sum_actions"]

    return G

def _append_custom_js(html_path: Path, node_details: dict | None = None, debug_info: dict | None = None):
    """
    Append our interaction script (zoom-aware opacity + label fading + zoom HUD)
    to the generated HTML. 主题通过 {THEME} 占位符注入 ('dark' or 'light')。
    """
    # prepare node details JSON (may be None)
    ND_JSON = _json.dumps(node_details or {})
    DBG_JSON = _json.dumps(debug_info or {})
    # base64-encode to avoid embedding raw </script> or other problematic sequences
    try:
        import base64
        ND_B64 = base64.b64encode(ND_JSON.encode('utf-8')).decode('ascii')
        DBG_B64 = base64.b64encode(DBG_JSON.encode('utf-8')).decode('ascii')
    except Exception:
        ND_B64 = ''
        DBG_B64 = ''

    # derive filenames next to the HTML
    base_name = html_path.stem
    node_json_name = base_name + "_node_details.json"
    debug_json_name = base_name + "_debug.json"

    js = r"""
<script type="text/javascript">
/* ===== Mental Omega Trigger Graph – injected interaction script ===== */
window.__THEME = "{THEME}";                    // 注入主题色 'dark' | 'light'，若未替换则自动按 <body> 背景判断
window.__CFG_INTERACT = {CFG_INTERACT_JSON};   // 注入用户设置
window.__TRIGGER_VIZ_VERSION = "{TOOL_VERSION}"; // 当前可视化脚本版本（用于客户端版本检查
// Load external node details and debug info (fetch adjacent JSON files)
window.__NODE_DETAILS = {};
// small fallback: expose the debug config immediately so HUD can appear even if fetch() is blocked (file://)
window.__DEBUG = {"debug_cfg": %s};
// fetch of sidecar JSONs is done later inside the IIFE after interaction helpers are defined

(function(){
    // ---------- 私有 DOM 就绪工具（避免与其它脚本同名冲突） ----------
    const __moOnReady = (fn) => {
        if (document.readyState !== 'loading') { try { fn(); } catch(e){} }
        else { document.addEventListener('DOMContentLoaded', () => { try { fn(); } catch(e){} }, { once:true }); }
    };

    // 是否已经应用过布局缓存（避免重复）
    window.__LAYOUT_APPLIED = window.__LAYOUT_APPLIED || false;


    // Accurate scale helper: prefer the last observed scale after fit/stabilize to avoid
    // the initial 1x placeholder returned before vis-network finishes fitting the graph.
    window.__VIS_LAST_SCALE = null;
    function __updateLastScale(){ try { window.__VIS_LAST_SCALE = network.getScale(); } catch(e){} }
    function __getAccurateScale(){ try { return (window.__VIS_LAST_SCALE || network.getScale() || 1.0); } catch(e){ return 1.0; } }

    // 布局来源标记（'physics' / 'cache' / null）
    window.__LAYOUT_SOURCE = window.__LAYOUT_SOURCE || null;

    // ====== 布局缓存：兼容判断 + 应用 ======
    let __LAYOUT_APPLIED = false;

        function __inferMapNameFromLocation(){
        try {
            const path = window.location.pathname || ''; // 如 /data/maps/aanes/aanes_trigger_graph.html
            const parts = path.split('/').filter(Boolean);
            if (!parts.length) return null;

            const htmlName = parts[parts.length - 1];   // aanes_trigger_graph.html
            const m = htmlName.match(/^(.+)_trigger_graph\.html$/);
            if (m) return m[1];

            // 回退：用上一级目录名
            return parts.length >= 2 ? parts[parts.length - 2] : null;
        } catch(e){
            return null;
        }
    }

    const __MAP_NAME_HINT = __inferMapNameFromLocation();

    function __isLayoutCompatible(layoutObj, dbg) {
        if (!layoutObj) return false;

        // 如果还没拿到 debug，就先相信同一目录的 layout
        if (!dbg) return true;

        // 1) tool_version 不一致，直接当不兼容
        if (layoutObj.tool_version && dbg.tool_version &&
            layoutObj.tool_version !== dbg.tool_version) {
        return false;
        }

        // 2) 如果以后你在 layout.json 里也加了 node_count / edge_count / map_name，
        //    这里会自动生效；现在这些字段缺失也不会影响兼容性判断。
        if (typeof layoutObj.node_count === 'number' &&
            typeof dbg.node_count === 'number' &&
            layoutObj.node_count !== dbg.node_count) {
        return false;
        }
        if (typeof layoutObj.edge_count === 'number' &&
            typeof dbg.edge_count === 'number' &&
            layoutObj.edge_count !== dbg.edge_count) {
        return false;
        }
        if (layoutObj.map_name && dbg.map_name &&
            layoutObj.map_name !== dbg.map_name) {
        return false;
        }

        // 其他情况就认为“基本兼容”
        return true;
    }

    function __maybeApplyCachedLayout() {
        if (window.__LAYOUT_APPLIED) return;

        try {
        if (typeof network === 'undefined' || !network || !network.body) {
            return; // network 还没就绪，稍后再试
        }

        const dbg = window.__DEBUG;
        // 优先用 debug.map_name，拿不到时用 URL 推断
        const mapName = (dbg && dbg.map_name) || __MAP_NAME_HINT;
        if (!mapName) {
            // 实在推不出 map 名，只能等下一次
            return;
        }

        // HTML 跟 layout.json 在同一目录，文件名是 "<map_name>_layout.json"
        const layoutUrl = `${mapName}_layout.json`;

        fetch(layoutUrl).then(r => {
            if (!r.ok) throw new Error('status ' + r.status + ' @' + layoutUrl);
            return r.json();
        }).then(data => {
            if (!data) return;

            // layout.json 现在的格式：
            // {
            //   "tool_version": "...",
            //   "generated_at": "...",
            //   "node_positions": { "01000000": {x,y}, ... }
            // }
            const layoutMeta = data;
            const positions = data.node_positions || data.positions || data;

            if (!__isLayoutCompatible(layoutMeta, dbg)) {
            console.log('[TriggerGraph] layout cache incompatible, ignored');
            return;
            }

            const nodesData = network.body.data.nodes;
            const allNodes  = nodesData.get();

            allNodes.forEach(n => {
            const idStr = String(n.id);
            let pos = positions[idStr];

            // 兼容一点：如果 ID 有前导 0 / 去掉前导 0
            if (!pos && /^0\d+$/.test(idStr)) {
                const stripped = idStr.replace(/^0+/, '');
                pos = positions[stripped] || positions[parseInt(stripped || '0', 10)];
            }
            if (!pos && positions[n.id]) {
                pos = positions[n.id];
            }

            if (pos && typeof pos.x === 'number' && typeof pos.y === 'number') {
                n.x = pos.x;
                n.y = pos.y;
                n.physics = false;

                if(n.fixed) {
                    n.fixed = false;
                }
            }
            });

            nodesData.update(allNodes);

            // 新增：缓存布局时，修改边的形状（两套方案）
            try {
                const edgesData = network.body.data.edges;
                const allEdges  = edgesData.get();
                allEdges.forEach(e => {
                    // 把旧的平滑参数和 via 控制点统统清掉
                    if (e.hasOwnProperty('via'))    delete e.via;
                    if (e.hasOwnProperty('smooth')) delete e.smooth;

                    // 方案A：完全直线
                    e.smooth = { enabled: false };

                    // 方案B：轻微圆角
                    // e.smooth = {
                    //    enabled: true,
                    //    type: 'cubicBezier',
                    //    roundness: 0.10   // 越小越接近直线
                    // };
                });
                edgesData.update(allEdges);
            } catch(e){}

            try {
                network.setOptions({
                    edges: { 
                        // smooth: {
                        //    enabled: true,
                        //    type: 'cubicBezier',
                        //    roundness: 0.10   // 越小越接近直线
                        // } 
                        smooth: { enabled: false }
                    }
                });
            } catch(e){}

            // 关闭物理，避免再次迭代
            try {
                network.setOptions({
                    physics: {
                        enabled: false,
                        stabilization: { enabled: false }
                    }
                });
                if (typeof network.stopSimulation === 'function') {
                network.stopSimulation();
                }
            } catch(e){}

            try {
                network.redraw();
            } catch(e){}

            // ⭐ 标记布局来源为“缓存”
            try {
                window.__LAYOUT_SOURCE = 'cache';
            } catch(e){}

            // layout 应用完毕后，强制按照“当前缩放”刷新一遍基线样式
            try {
                if (typeof __updateLastScale === 'function') {
                    __updateLastScale();                           // 把当前 scale 记入 __VIS_LAST_SCALE
                }
                if (typeof __resetDimThrottled === 'function') {
                    __resetDimThrottled(true);                     // 强制刷新一次节点/边透明度 + label 显隐
                }
                if (typeof __showZoomHUD === 'function') {
                    __showZoomHUD(__getAccurateScale());           // HUD 也顺便同步一下
                }
                if (typeof __alignTooltipByPolicy === 'function') {
                    __alignTooltipByPolicy('zoom');                // 若已有选中对象，让 tooltip 顺带对齐一次（可选）
                }
            } catch (e) {
                // 安全兜底，不让这里的报错影响加载
                console.warn('[TriggerGraph] post-layout dim refresh failed:', e);
            }

            window.__LAYOUT_APPLIED = true;
            console.log('[TriggerGraph] layout cache applied from', layoutUrl);
        }).catch(err => {
            // 没文件 / 404 / 解析失败，都当无缓存，不报错
            // console.log('[TriggerGraph] no layout cache:', err);
        });
        } catch (e) {
        // 安全兜底
        // console.warn('[TriggerGraph] apply layout cache failed', e);
        }
    }
    
    // fetch sidecar JSONs and wire up debug HUD update when data arrives
    (function fetchSidecars(){
        try {
            fetch('{NODE_JSON}').then(r=>r.json()).then(j=>{ window.__NODE_DETAILS = j; window.__NODE_DETAILS_LOADED = true; try { /* if a node is currently selected, refresh its tooltip */ if (typeof __LAST_SELECTED_ID !== 'undefined' && __LAST_SELECTED_ID != null){ __alignTooltipByPolicy('other'); } } catch(e){} }).catch(()=>{ window.__NODE_DETAILS_LOADED = false; });
            const __debugUrlPrimary = '{DEBUG_JSON}';
            const __baseName = '{THEME}' ? (function(){ const bn = (typeof __debugUrlPrimary === 'string' ? __debugUrlPrimary : ''); return bn.replace(/_debug\.json$/,''); })() : '';
            const __altUrl = (function(){
                try {
                    // 推导 map 名称：移除 "_trigger_graph" 后缀
                    const m = __baseName.replace(/_trigger_graph$/,'');
                    if (!m) return null;
                    return 'data/maps/' + m + '/' + __baseName + '_debug.json';
                } catch(e){ return null; }
            })();
            function __coerceDebug(j){
                if (!j) return j;
                if (typeof j.size_score === 'string'){ const n = parseFloat(j.size_score); if(!Number.isNaN(n)) j.size_score = n; }
                if (typeof j.node_weight === 'string'){ const n = parseFloat(j.node_weight); if(!Number.isNaN(n)) j.node_weight = n; }
                if (typeof j.edge_weight === 'string'){ const n = parseFloat(j.edge_weight); if(!Number.isNaN(n)) j.edge_weight = n; }
                return j;
            }

            function __applyDebug(j){
                try {
                    j = __coerceDebug(j);

                    // 若缺少 size_score/权重字段（旧版 debug JSON），尝试重建
                    if (j && (j.size_score === undefined || j.node_weight === undefined || j.edge_weight === undefined)) {
                        try {
                            const layout = (window.__CFG_INTERACT && window.__CFG_INTERACT) ? window.__CFG_INTERACT : {};
                            // 回退权重：1.0 / 0.5
                            const nw = (typeof j.node_weight === 'number') ? j.node_weight : 1.0;
                            const ew = (typeof j.edge_weight === 'number') ? j.edge_weight : 0.5;
                            if (typeof j.node_count === 'number' && typeof j.edge_count === 'number') {
                                const sc = nw * j.node_count + ew * j.edge_count;
                                if (j.size_score === undefined) j.size_score = sc;
                                if (j.node_weight === undefined) j.node_weight = nw;
                                if (j.edge_weight === undefined) j.edge_weight = ew;
                            }
                        } catch(e){}
                    }

                    if (typeof window.__DEBUG === 'object' && window.__DEBUG){
                        Object.assign(window.__DEBUG, j);
                    } else {
                        window.__DEBUG = j || {};
                    }
                    console.log('[TriggerGraph] debug sidecar applied:', window.__DEBUG);

                    // =======================================================
                    // 布局缓存：尝试从 <map_name>_layout.json 读取并应用
                    // 统一走 __maybeApplyCachedLayout，这里只负责“在 debug.json 到手后再试一次”
                    // =======================================================
                    try {
                        __maybeApplyCachedLayout();
                    } catch(e){}
                    // =======================================================

                    __DBG_STAB_SET = false;
                    try {
                        const _d = (window.__DEBUG && window.__DEBUG.debug_cfg)
                             ? window.__DEBUG.debug_cfg
                             : null;
                        if (_d && _d.enable) __updateDebugHUD(window.__DEBUG);
                    } catch(e){}

                } catch(e){
                    console.warn('[TriggerGraph] apply debug failed', e);
                }
            }

            function __fetchDebug(url, isFallback){
                if (!url) return Promise.reject('no-url');
                return fetch(url).then(r=>{ if(!r.ok) throw new Error('status '+r.status+' @'+url); return r.json(); })
                    .then(j=>{ __applyDebug(j); return j; })
                    .catch(err => {
                        console.warn('[TriggerGraph] debug sidecar fetch failed', url, err);
                        if (!isFallback && __altUrl && url === __debugUrlPrimary){
                            console.log('[TriggerGraph] trying fallback debug url:', __altUrl);
                            return __fetchDebug(__altUrl, true);
                        }
                        throw err;
                    });
            }
            __fetchDebug(__debugUrlPrimary).catch(()=>{});
        } catch(e){}
    })();

  // ---------- 可调参数 ----------
  const Z_MIN  = (window.__CFG_INTERACT?.Z_MIN  ?? 0.3);              // ≤Z_MIN 视为“很远”
  const Z_MAX  = (window.__CFG_INTERACT?.Z_MAX  ?? 0.6);              // ≥Z_MAX 视为“很近”
  const OP_AT_FAR  = (window.__CFG_INTERACT?.OP_AT_FAR  ?? 1.0);      // 远时边更实（避免看不清）
  const OP_AT_NEAR = (window.__CFG_INTERACT?.OP_AT_NEAR ?? 0.45);     // 近时边更透明（避免遮挡标签）

  const INCLUDE_TWO_HOPS = (window.__CFG_INTERACT?.INCLUDE_TWO_HOPS ?? true);       // 选中结点时，是否高亮两跳邻居
  const EDGE_HILITE_MODE = (window.__CFG_INTERACT?.EDGE_HILITE_MODE ?? 'outgoing'); // 选中结点时，只高亮从结点向外连出的箭头
  
  const HILITE_BACKWARD_ONEHOP_NODE_ONLY = true; // 在 'outgoing' 模式下：高亮向后一跳的结点，但是不高亮相连边

  // 悬浮窗跟随策略：
  // 'zoom_only'     仅在缩放时跟随到选中节点
  // 'zoom_and_drag' 缩放 + 拖动画布时都跟随到选中节点
  // 'always'        选中期间每帧都跟随（物理抖动/布局变化也流畅跟随）
  // 'none'          从不自动跟随（只在点击高亮时定位一次）
  const TOOLTIP_TRACKING = (window.__CFG_INTERACT?.TOOLTIP_TRACKING ?? 'zoom_only');
    const LABEL_HIDE_BELOW = (window.__CFG_INTERACT?.LABEL_HIDE_BELOW ?? 0.35);

  // Zoom HUD
  const HUD_FADE_DELAY_MS    = (window.__CFG_INTERACT?.HUD_FADE_DELAY_MS    ?? 2000);  // 没有操作多久时间后开始淡出
  const HUD_FADE_DURATION_MS = (window.__CFG_INTERACT?.HUD_FADE_DURATION_MS ?? 1000);  // 淡出动画时长
  const HUD_BG               = "rgba(0,0,0,0.55)";
  const HUD_TEXT_COLOR       = "#ffffff";
  const HUD_BORDER_RADIUS    = "8px";
  const HUD_FONT             = "12px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";
  let   __hudFadeTimer = null;

  // 透明度重算的节流间隔（毫秒）
  const DIM_UPDATE_INTERVAL = (window.__CFG_INTERACT?.DIM_UPDATE_INTERVAL_MS ?? 120);
  let __dimLastUpdate = 0;
  
  // ---- 每帧跟随（仅在选中状态下运行） ----
  let __followRAF = null;
  let __followActive = false;

  function __startFollow(){
    if (__followActive) return;
    __followActive = true;
    const step = () => {
      if (!__followActive || (__LAST_SELECTED_ID == null && __LAST_SELECTED_EDGE == null)) return;
      __placeTooltipAtSelection();
      __followRAF = requestAnimationFrame(step);
    };
    __followRAF = requestAnimationFrame(step);
  }

  function __stopFollow(){
    __followActive = false;
    if (__followRAF) { cancelAnimationFrame(__followRAF); __followRAF = null; }
  }

  // 新增：按策略触发一次“对齐”
  function __alignTooltipByPolicy(trigger){
    // trigger: 'zoom' | 'drag' | 'select' | 'other'

    if (__LAST_SELECTED_ID == null && __LAST_SELECTED_EDGE == null) return;

    if (TOOLTIP_TRACKING === 'always') {
      // 连续跟随由 rAF 负责，这里只确保开始
      __startFollow();
      return;
    }
    if (TOOLTIP_TRACKING === 'zoom_and_drag') {
      if (trigger === 'zoom' || trigger === 'drag' || trigger === 'select') {
        __placeTooltipAtSelection();
      }
      return;
    }
    if (TOOLTIP_TRACKING === 'zoom_only') {
      if (trigger === 'zoom' || trigger === 'select') {
        __placeTooltipAtSelection();
      }
      return;
    }
    // 'none'：仅在 select 时定位一次，其它时机不跟随
    if (TOOLTIP_TRACKING === 'none') {
      if (trigger === 'select') {
        __placeTooltipAtSelection();
      }
      return;
    }
  }

  function __ensureZoomHUD(){
    let hud = document.getElementById("zoom_hud");
    if (!hud){
      hud = document.createElement("div");
      hud.id = "zoom_hud";
      hud.style.position      = "fixed";
      hud.style.left          = "12px";
      hud.style.bottom        = "12px";
      hud.style.padding       = "6px 10px";
      hud.style.background    = HUD_BG;
      hud.style.color         = HUD_TEXT_COLOR;
      hud.style.borderRadius  = HUD_BORDER_RADIUS;
      hud.style.font          = HUD_FONT;
      hud.style.letterSpacing = "0.3px";
      hud.style.zIndex        = 10001;
      hud.style.pointerEvents = "none";
      hud.style.opacity       = "0";
      hud.style.transition    = `opacity ${HUD_FADE_DURATION_MS}ms ease`;
      document.body.appendChild(hud);
    }
    return hud;
  }
  function __formatScale(z){ return `Zoom: ${Math.max(0, z).toFixed(2)}×`; }
  function __showZoomHUD(z){
    const hud = __ensureZoomHUD();
    hud.textContent = __formatScale(z);
    hud.style.opacity = "1";
    if (__hudFadeTimer) { clearTimeout(__hudFadeTimer); __hudFadeTimer = null; }
    __hudFadeTimer = setTimeout(() => { hud.style.opacity = "0"; }, HUD_FADE_DELAY_MS);
  }

    // ---------- Debug HUD (optional) ----------
    function __ensureDebugHUD(){
        let d = document.getElementById('debug_hud');
        if (!d){
            d = document.createElement('div');
            d.id = 'debug_hud';
            Object.assign(d.style, {
                position:'fixed', right:'12px', top:'12px', padding:'8px 10px',
                background:'rgba(0,0,0,0.6)', color:'#fff', fontSize:'12px', zIndex:10002,
                borderRadius:'6px', fontFamily:'ui-monospace, monospace', maxWidth:'360px'
            });
            document.body.appendChild(d);
        }
        return d;
    }

    function __updateDebugHUD(info){
        if (!info) return;
        const d = __ensureDebugHUD();

        // 版本号
        const ver = (typeof info.tool_version === 'string' && info.tool_version.trim() !== '')
            ? info.tool_version
            : '(未知)';

        // 缩放信息
        let cached = (window.__VIS_LAST_SCALE == null
            ? '?'
            : (Number.isFinite(window.__VIS_LAST_SCALE)
                ? window.__VIS_LAST_SCALE.toFixed(3)
                : String(window.__VIS_LAST_SCALE)));

        let raw = '?';
        try {
            if (typeof network !== 'undefined' && network && network.getScale) {
                const s = network.getScale();
                raw = Number.isFinite(s) ? s.toFixed(3) : String(s);
            }
        } catch(e){}

        // 兼容：字符串数字 → number
        if (info && typeof info.size_score === 'string') {
            const n = parseFloat(info.size_score);
            if (!Number.isNaN(n)) info.size_score = n;
        }
        if (info && typeof info.node_weight === 'string') {
            const n = parseFloat(info.node_weight);
            if (!Number.isNaN(n)) info.node_weight = n;
        }
        if (info && typeof info.edge_weight === 'string') {
            const n = parseFloat(info.edge_weight);
            if (!Number.isNaN(n)) info.edge_weight = n;
        }

        const hasScore   = (info && typeof info.size_score === 'number' && Number.isFinite(info.size_score));
        const hasWeights = (info && typeof info.node_weight === 'number' && typeof info.edge_weight === 'number');

        const scoreLine = hasScore
            ? `分数: ${info.size_score.toFixed(2)} (节点权重=${info.node_weight}, 边权重=${info.edge_weight})`
            : '分数: (等待加载...)';

        const nodesLine = (typeof info.node_count === 'number' ? info.node_count : '(待)');
        const edgesLine = (typeof info.edge_count === 'number' ? info.edge_count : '(待)');
        const iterLine  = (typeof info.stab_iter === 'number' ? info.stab_iter : '(待)');

        // === 布局来源标记：physics / cache / 未知 ===
        const layoutSource = (function(){
            try {
                if (window.__LAYOUT_SOURCE === 'cache')   return '缓存';
                if (window.__LAYOUT_SOURCE === 'physics') return '迭代';
                return '未知';
            } catch(e){
                return '未知';
            }
        })();

        const fromCache = (layoutSource === '缓存');

        // === 时间行：优先使用“最终耗时”，没有的话才看 start_time ===
        let timeLine;
        if (fromCache) {
            // 读取缓存布局时，不存在本地稳定迭代过程 → 显示“不适用”
            timeLine = '稳定耗时: (不适用)';
        } else if (Number.isFinite(__stab_duration_final) && __stab_duration_final > 0) {
            timeLine = `稳定耗时: ${(__stab_duration_final / 1000).toFixed(2)}s`;
        } else if (typeof __stab_start_time === 'number') {
            const now = (typeof performance !== 'undefined' && typeof performance.now === 'function')
                ? performance.now()
                : Date.now();
            const dt = (now - __stab_start_time) / 1000;
            timeLine = `稳定耗时: ${dt.toFixed(2)}s (进行中)`;
        } else {
            timeLine = '稳定耗时: (等待)';
        }

        // === 进度行：同样只看缓存，不自己改写迭代数 ===
        let progressLine;
        if (fromCache) {
            // 缓存布局时稳定进度也“没有意义”
            progressLine = '稳定进度: (不适用)';
        } else if (typeof __stab_iterations_final === 'number' &&
                   typeof __stab_total_final      === 'number' &&
                   __stab_total_final > 0) {
            const pct = Math.round(__stab_iterations_final / __stab_total_final * 10000) / 100;
            progressLine = `稳定进度: 迭代: ${__stab_iterations_final}/${__stab_total_final}  进度: ${pct}%`;
        } else if (typeof __stab_iterations_final === 'number') {
            progressLine = `稳定进度: 迭代: ${__stab_iterations_final} (总步数未知)`;
        } else {
            progressLine = '稳定进度: (等待)';
        }

        d.innerHTML = `
            <div><b>调试面板</b></div>
            <div>版本: ${ver}</div>
            <div>节点: ${nodesLine} &nbsp; 边: ${edgesLine}</div>
            <div>迭代次数(stab_iter): ${iterLine}</div>
            <div>${scoreLine}</div>
            <div>缩放(缓存): ${cached} &nbsp; 缩放(实时): ${raw}</div>
            <div>布局来源: ${layoutSource}</div>
            <div id="dbg_stab_time">${timeLine}</div>
            <div id="dbg_stab_progress">${progressLine}</div>
        `;
    }

    function __autoSaveLayoutToServer() {
        try {
            if (!network || !network.body) return;

            // 1) 拿当前所有节点坐标
            const positions = network.getPositions();  // { id: {x,y}, ... }

            // 2) 推断 map_name（来自 debug JSON 最稳妥）
            const mapName = (window.__DEBUG && window.__DEBUG.map_name)
                ? window.__DEBUG.map_name
                : null;

            if (!mapName) {
                console.warn('[TriggerGraph] no map_name in DEBUG; skip autosave layout');
                return;
            }

            // 3) 构造 payload
            const payload = {
                map_name: mapName,
                tool_version: window.__TRIGGER_VIZ_VERSION || 'unknown',
                generated_at: new Date().toISOString(),
                node_positions: positions
            };

            // 4) POST 到本地服务器的一个专用 endpoint
            fetch('/__save_layout', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            }).then(j => {
                console.log('[TriggerGraph] layout autosaved:', j);
            }).catch(err => {
                console.warn('[TriggerGraph] layout autosave failed, you can still export manually:', err);
            });

        } catch (e) {
            console.warn('[TriggerGraph] autoSaveLayout error:', e);
        }
    }


  // ---------- 工具 ----------
  function __baseEdgeOpacityForScale(scale){
    if (scale <= Z_MIN) return OP_AT_FAR;
    if (scale >= Z_MAX) return OP_AT_NEAR;
    const t = (scale - Z_MIN) / (Z_MAX - Z_MIN);
    return OP_AT_FAR + t * (OP_AT_NEAR - OP_AT_FAR);
  }

  // 主题感知的标签颜色（延迟到 DOM Ready 后）
  function __computeTheme(){
    const t = (typeof window.__THEME === 'string') ? window.__THEME.toLowerCase() : null;
    if (t === 'dark') return true;
    if (t === 'light') return false;
    const bg = getComputedStyle(document.body).backgroundColor;
    const m  = bg && bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    if (!m) return false;
    const [r,g,b] = [m[1],m[2],m[3]].map(Number);
    const L = (0.2126*r + 0.7152*g + 0.0722*b) / 255; // WCAG 相对亮度
    return L < 0.5;
  }
  function __getLabelColors(){
    // 若 DOM ready 之前被调用，则给出兜底；ready 后会被 resetDim/高亮用到
    const isDark = (typeof window.__LABEL_COLOR_NORMAL === 'string')
      ? (window.__LABEL_COLOR_NORMAL === '#e5e7eb')
      : __computeTheme();
    const normal = isDark ? "#e5e7eb" : "#111111";
    const faded  = isDark ? "rgba(229,231,235,0.26)" : "rgba(17,17,17,0.22)";
    return {
      normal: window.__LABEL_COLOR_NORMAL || normal,
      faded : window.__LABEL_COLOR_FADED  || faded
    };
  }

  // 主题感知的文字描边颜色
  function __getStrokeColors(){
    const isDark = __computeTheme();
    // 深色背景：深蓝黑描边，淡化时再更浅一点
    // 浅色背景：白色描边，淡化时再更透明
    return isDark
      ? { normal: "#0f172a", faded: "rgba(15,23,42,0.45)" }   // 深色主题
      : { normal: "#f9fafb", faded: "rgba(249,250,251,0.55)" }; // 浅色主题
  }

  // 信息浮窗
  function __showTooltipNear(pointer, html){
    let el = document.getElementById('custom_tooltip');
    if (!el){
      el = document.createElement('div');
      el.id = 'custom_tooltip';
      Object.assign(el.style, {
        position:'fixed', background:'rgba(0,0,0,0.78)', color:'#fff',
        padding:'10px 12px', borderRadius:'8px', maxWidth:'520px',
        fontFamily:'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        fontSize:'12px', lineHeight:'1.5', zIndex:10000,
        boxShadow:'0 6px 20px rgba(0,0,0,0.35)'
      });
      document.body.appendChild(el);
    }
    el.innerHTML = html || '(No details)';
    const x = Math.min((pointer?.DOM?.x ?? 20) + 18, window.innerWidth  - 540);
    const y = Math.min((pointer?.DOM?.y ?? 20) + 18, window.innerHeight - 240);
    el.style.left = x + 'px';
    el.style.top  = y + 'px';
    el.style.display = 'block';
  }
  function __hideTooltip(){ const el = document.getElementById('custom_tooltip'); if (el) el.style.display = 'none'; }

  // 将悬浮窗跟随在选中结点附近
    function __placeTooltipAtNode(nodeId){
    try {
                const pos = network.getPositions([nodeId])[nodeId]; // 画布坐标
                const dom = network.canvasToDOM(pos);               // DOM 坐标
                const node = network.body.data.nodes.get(nodeId);

                // Robust lookup: try several string forms so we survive pyvis coercing numeric-like IDs
                function _lookupDetails(id){
                    try {
                        const s = String(id);
                        if (window.__NODE_DETAILS && window.__NODE_DETAILS[s]) return window.__NODE_DETAILS[s];
                        // if numeric-like, try zero-pad (8) and strip-leading-zeros variants
                        if (/^\d+$/.test(s)){
                            if (s.length < 8){
                                const p = s.padStart(8, '0');
                                if (window.__NODE_DETAILS && window.__NODE_DETAILS[p]) return window.__NODE_DETAILS[p];
                            }
                            const stripped = s.replace(/^0+/, '');
                            if (stripped && window.__NODE_DETAILS && window.__NODE_DETAILS[stripped]) return window.__NODE_DETAILS[stripped];
                        }
                    } catch(e){}
                    return null;
                }

                const external = _lookupDetails(nodeId) || _lookupDetails(node && node.id) || null;
                const html = external || (node && node.detail) || '(No details)';
                __showTooltipNear({ DOM:{ x: dom.x, y: dom.y } }, html);
    } catch(e){}
  }

  // 小工具：把 "local:16" 映射为 "16"
  function extractLocalId(x){
    const s = String(x);
    return s.startsWith('local:') ? s.split(':')[1] : s;
  }

  // 将悬浮窗固定在边的中点，并给出边的语义解释
  function __placeTooltipAtEdge(edgeId){
    try {
      const e = network.body.data.edges.get(edgeId);
      if (!e) return;
 
      const fromPos = network.getPositions([e.from])[e.from];
      const toPos   = network.getPositions([e.to])[e.to];
      if (!fromPos || !toPos) return;

      const mid = { x: (fromPos.x + toPos.x)/2, y: (fromPos.y + toPos.y)/2 };
      const dom = network.canvasToDOM(mid);

      const from  = e.from;
      const to    = e.to;
      const label = String(e.edgeLabel || e.label || '').trim() || '(edge)';

      // —— 语义解释：仅基于 label 映射 —— //
      // 说明：若你在 Python 端把 36/37 的 label 改成了 depends_on_true / depends_on_false，
      // 这里会自动区别两种情况；否则就统一按 depends_on 处理。
      let meaning = '';
      switch (label) {
        case 'linked':
          meaning = `触发 <b>${from}</b> 被关联到了触发 <b>${to}</b>。`;
          break;
        case 'enable':
          meaning = `触发 <b>${from}</b> 启用了触发 <b>${to}</b>。`;
          break;
        case 'disable':
          meaning = `触发 <b>${from}</b> 禁用了触发 <b>${to}</b>。`;
          break;
        case 'destroy':
          meaning = `触发 <b>${from}</b> 销毁了触发 <b>${to}</b>。`;
          break;
        case 'force':
          meaning = `触发 <b>${from}</b> 强制执行了触发 <b>${to}</b>。`;
          break;
        case 'enable_local': {
          // to 往往是 "local:16" 这种形式，此处将截去之前的 "local" 和冒号。
          const localId = (String(to).startsWith('local:') ? String(to).split(':')[1] : String(to));
          meaning = `本地变量 <b>${localId}</b> 被触发 <b>${from}</b> 置为 <b>真</b>。`;
          break;
        }
        case 'disable_local': {
          const localId = (String(to).startsWith('local:') ? String(to).split(':')[1] : String(to));
          meaning = `本地变量 <b>${localId}</b> 被触发 <b>${from}</b> 置为 <b>假</b>。`;
          break;
        }

        // 如果在后端里区分了 36/37：
        case 'depends_on_true': {
          const localId = extractLocalId(from);     // 边方向：local -> trigger
          const trigId  = String(to);
          meaning = `变量 <b>${localId}</b> 为 <b>真</b> 时，触发 <b>${trigId}</b> 的条件才满足。`;
          break;
        }
        case 'depends_on_false': {
          const localId = extractLocalId(from);
          const trigId  = String(to);
          meaning = `变量 <b>${localId}</b> 为 <b>假</b> 时，触发 <b>${trigId}</b> 的条件才满足。`;
          break;
        }

        // 旧兼容：如果你还没区分 36/37，只写了 depends_on，这里给个中性描述
        case 'depends_on': {
          const localId = extractLocalId(from);
          const trigId  = String(to);
          meaning = `触发 <b>${trigId}</b> 和变量 <b>local ${localId}</b> 之间存在依赖关系。`;
          break;
        }

        default:
          meaning = `触发 <b>${from}</b> 与 <b>${to}</b> 之间存在逻辑连接（${label}）。`;
          break;
      }

      const html = `
        <b>Edge</b> ${from} → ${to}<br>
        <span style="opacity:0.8">${label}</span><br>
        <span style="color:#93c5fd;">${meaning}</span>
      `;
      __showTooltipNear({ DOM:{ x: dom.x, y: dom.y } }, html);
    } catch (err) {}
  }

  // 按照选中的结点/边确定高亮对象
  function __placeTooltipAtSelection(){
    if (__LAST_SELECTED_ID != null)   return __placeTooltipAtNode(__LAST_SELECTED_ID);
    if (__LAST_SELECTED_EDGE != null) return __placeTooltipAtEdge(__LAST_SELECTED_EDGE);
  }

  // 边原色
  function __edgeOrigColor(e){
    if (e.origColor) return e.origColor;
    if (typeof e.color === 'string') return e.color;
    if (e.color && e.color.color)   return e.color.color;
    return '#6b7280';
  }

  // ---------- 渲染控制 ----------
  function __resetDim(){
    try{
    const scale = __getAccurateScale();
      const baseOpacity = __baseEdgeOpacityForScale(scale);
      const LC = __getLabelColors();
      const SC = __getStrokeColors();

      const nodesAll = network.body.data.nodes.get();
      nodesAll.forEach(n => {

        if ('x' in n) delete n.x;
        if ('y' in n) delete n.y;

        if (n.origSize == null) n.origSize = n.size;   // 确保一定会写入一次基线尺寸
                if (n.origFontSize == null) n.origFontSize = (n.font && n.font.size) || 16;
        n.opacity = 1.0;
                n.size    = n.origSize || n.size
                // 控制 label 的显隐：在低缩放下把 font.size 设为 0（等同于隐藏），近景恢复为原始字体大小
                const showLabel = (scale >= LABEL_HIDE_BELOW);
                n.font = Object.assign({}, n.font, { 
                    size: showLabel ? n.origFontSize : 0,
                    color: LC.normal,
                    strokeWidth: 5,            // 可调：描边粗细
                    strokeColor: SC.normal
                });
      });
      network.body.data.nodes.update(nodesAll);

      const edgesAll = network.body.data.edges.get();
      edgesAll.forEach(e => {
        e.color = { color: __edgeOrigColor(e), opacity: baseOpacity };
        e.width = 1.8; // 可调：默认线宽
      });
      network.body.data.edges.update(edgesAll);
    }catch(e){}
  }

  // 新增：节流版 resetDim，避免在缩放事件中每帧全量刷新
  function __resetDimThrottled(force){
    try {
      const now = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();

      // 非强制模式下，如果距离上次刷新还没超过间隔，就直接返回
      if (!force && (now - __dimLastUpdate) < DIM_UPDATE_INTERVAL) return;

      __dimLastUpdate = now;
      __resetDim();
    } catch(e){}
  }

  function __updateEdgeOpacityForScale(){
    try {
      const scale = __getAccurateScale();
      const baseOpacity = __baseEdgeOpacityForScale(scale);

      const edgesAll = network.body.data.edges.get();
      edgesAll.forEach(e => {
        const col = __edgeOrigColor(e);
        // 保留原有 color 对象里的其他字段（如 highlight / hover），只更新 color+opacity
        if (typeof e.color === 'object' && e.color !== null) {
          e.color = Object.assign({}, e.color, {
            color: col,
            opacity: baseOpacity
          });
        } else {
          e.color = { color: col, opacity: baseOpacity };
        }
      });
      network.body.data.edges.update(edgesAll);
    } catch(e){}
  }

  // 为“只更新边透明度”增加节流封装
  let __edgeDimLastUpdate = 0;
  function __updateEdgeOpacityForScaleThrottled(force){
    try {
      const now = (typeof performance !== 'undefined' && performance.now)
        ? performance.now()
        : Date.now();

      // 和 resetDim 一样：距离上次不足 DIM_UPDATE_INTERVAL 就直接返回
      if (!force && (now - __edgeDimLastUpdate) < DIM_UPDATE_INTERVAL) return;

      __edgeDimLastUpdate = now;
      __updateEdgeOpacityForScale();
    } catch(e){}
  }


  function __highlightSelection(selectedId, pointer){
    const scale = __getAccurateScale();
    const baseOpacity = __baseEdgeOpacityForScale(scale);
    const LC = __getLabelColors();
    const SC = __getStrokeColors();
    const showLabel = (scale >= LABEL_HIDE_BELOW);

    // 先拿到全部边，算出“出邻居 / 入邻居”
    const edgesAll = network.body.data.edges.get();
    const outNeighbors = new Set(edgesAll.filter(e => e.from === selectedId).map(e => e.to));
    const inNeighbors  = new Set(edgesAll.filter(e => e.to   === selectedId).map(e => e.from));

    // 节点高亮集合
    const neighborSet = new Set([selectedId]);

    // 按模式纳入一跳邻居
    if (EDGE_HILITE_MODE === 'outgoing' || EDGE_HILITE_MODE === 'both') {
      outNeighbors.forEach(n => neighborSet.add(n));
    }
    if (EDGE_HILITE_MODE === 'incoming' || EDGE_HILITE_MODE === 'both') {
      inNeighbors.forEach(n => neighborSet.add(n));
    }

    // 【修复点】在 'outgoing' 模式下，也要点亮“后向一跳节点”，
    // 无论是否存在前向一跳（不改变边的高亮规则）
    if (HILITE_BACKWARD_ONEHOP_NODE_ONLY && EDGE_HILITE_MODE === 'outgoing') {
      inNeighbors.forEach(n => neighborSet.add(n));  // 只加“节点”，不改边
    }

    // 两跳：仍然保持“方向敏感”的规则
    if (INCLUDE_TWO_HOPS) {
      if (EDGE_HILITE_MODE === 'outgoing' || EDGE_HILITE_MODE === 'both') {
        // 选中 -> 一跳(out) -> 二跳(从一跳继续向外)
        outNeighbors.forEach(n1 => {
          edgesAll.forEach(e => {
            if (e.from === n1) neighborSet.add(e.to);
          });
        });
      }
      if (EDGE_HILITE_MODE === 'incoming' || EDGE_HILITE_MODE === 'both') {
        // 选中 <- 一跳(in) <- 二跳(再往回找入边的源头)
        inNeighbors.forEach(n1 => {
          edgesAll.forEach(e => {
            if (e.to === n1) neighborSet.add(e.from);
          });
        });
      }
    }

    // 节点：选中节点最亮且稍大，邻居正常，其他淡化
    const nodesAll = network.body.data.nodes.get();
    nodesAll.forEach(n => {

      if ('x' in n) delete n.x;
      if ('y' in n) delete n.y;
      
      const isSelf = (n.id === selectedId);
      const isNeighbor = neighborSet.has(n.id);
        if (isSelf) {
            n.opacity = 1.0;
            const base = n.origSize || n.size || 14;
            n.size = base * 1.35;
            n.font = Object.assign({}, n.font, { size: showLabel ? (n.origFontSize||16) : 0, color: LC.normal, strokeWidth: 5, strokeColor: SC.normal });
        } else if (isNeighbor) {
            n.opacity = 1.0;
            n.size = n.origSize || n.size;
            n.font = Object.assign({}, n.font, { size: showLabel ? (n.origFontSize||16) : 0, color: LC.normal, strokeWidth: 5, strokeColor: SC.normal });
        } else {
            n.opacity = 0.12;
            n.size = n.origSize || n.size;
            n.font = Object.assign({}, n.font, { size: showLabel ? (n.origFontSize||16) : 0, color: LC.faded, strokeWidth: 5, strokeColor: SC.faded });
        }
    });
    network.body.data.nodes.update(nodesAll);

    // 边：只按模式高亮方向匹配的边；无前向边时，后向一跳节点会亮但边仍不亮
    edgesAll.forEach(e => {
      const isOut = (e.from === selectedId);
      const isIn  = (e.to   === selectedId);
      let on = false;
      if (EDGE_HILITE_MODE === 'both')         on = (isOut || isIn);
      else if (EDGE_HILITE_MODE === 'outgoing') on = isOut;      // 保持只高亮“向外”的边
      else if (EDGE_HILITE_MODE === 'incoming') on = isIn;
 
      e.color = { color: __edgeOrigColor(e), opacity: on ? 0.95 : baseOpacity };
      e.width = on ? 2.6 : 1.0;
    });
    network.body.data.edges.update(edgesAll);

    // 信息框固定到“选中节点”附近
    __placeTooltipAtNode(selectedId);

    // 信息框固定到“选中节点”附近
    const pos = network.getPositions([selectedId])[selectedId];   // 画布坐标
    const dom = network.canvasToDOM(pos);                         // 转成 DOM 坐标
    __placeTooltipAtNode(selectedId);
  }

  function __highlightEdgeSelection(edgeId){
    const scale       = network.getScale();
    const baseOpacity = __baseEdgeOpacityForScale(scale);
    const LC = __getLabelColors();
    const SC = __getStrokeColors();
    const showLabel = (scale >= LABEL_HIDE_BELOW);

    const e = network.body.data.edges.get(edgeId);
    if (!e) return;

    const a = e.from, b = e.to;

    // 节点：仅端点不淡化（选中端点略放大），其他淡化
    const nodesAll = network.body.data.nodes.get();
    nodesAll.forEach(n => {

      if ('x' in n) delete n.x;
      if ('y' in n) delete n.y;

      if (n.origSize == null) n.origSize = n.size;
      const isEndpoint = (n.id === a || n.id === b);
      n.opacity = isEndpoint ? 1.0 : 0.12;
      n.size    = isEndpoint ? (n.origSize||n.size)*1.25 : (n.origSize||n.size);
      n.font    = Object.assign({}, n.font, {
        size:         showLabel ? (n.origFontSize||16) : 0,
        color:       isEndpoint ? LC.normal : LC.faded,
        strokeWidth: isEndpoint ? 6 : 4,
        strokeColor: isEndpoint ? SC.normal : SC.faded
      });
    });
    network.body.data.nodes.update(nodesAll);

    // 边：仅被选中这条接近不透明并加粗，其他按缩放基线透明度
    const edgesAll = network.body.data.edges.get();
    edgesAll.forEach(ed => {
      const isSel = (ed.id === edgeId);
      ed.color = { color: __edgeOrigColor(ed), opacity: isSel ? 0.98 : baseOpacity };
      ed.width = isSel ? 3.2 : 1.0;
    });
    network.body.data.edges.update(edgesAll);

    // 工具条跟随到边中点
    __placeTooltipAtEdge(edgeId);
  }

  // ---------- 绑定事件 ----------
  let __LAST_SELECTED_ID = null;
  let __LAST_SELECTED_EDGE = null;

  
  let __stab_start_time = null;          // 稳定开始时间
  let __stab_iterations_final = null;    // 最终迭代次数
  let __stab_duration_final   = null;    // 耗时（毫秒）
  let __stab_total_final      = null;    // 最终总步数

  __moOnReady(function bindWhenReady(){
    // 设置全局标签颜色（一次性）
    const isDark = __computeTheme();
    window.__LABEL_COLOR_NORMAL = isDark ? "#e5e7eb" : "#111111";
    window.__LABEL_COLOR_FADED  = isDark ? "rgba(229,231,235,0.26)" : "rgba(17,17,17,0.22)";

    const DUMMY = "__DUMMY__";

    // 插入一个不可见的 dummy 节点，用于触发初始的 selection 事件
    function __forceDummySelection() {
        try {
            if (!network || !network.body || !network.body.data) return;
            const node = network.body.data.nodes.get(DUMMY);
            if (!node) return;

            // 核心：触发 highlight pipeline + partial redraw
            network.setSelection({nodes:[DUMMY], edges:[]}, true);
        } catch (e) {
            console.warn("dummy selection failed:", e);
        }
    }

    // 页面初始化后第一次调用
    network.once("afterDrawing", () => {
        setTimeout(()=>__forceDummySelection(), 0);
    });

    network.on("stabilized", () => {
        setTimeout(()=>__forceDummySelection(), 0);
    });

    // 在 draw 之前，将 dummy 的绘制清除掉
    network.on("beforeDrawing", (ctx) => {
        const node = network.body.nodes[DUMMY];
        if (node) node.options.color = {
            border: "rgba(0,0,0,0)",
            background: "rgba(0,0,0,0)",
            highlight: {border: "rgba(0,0,0,0)", background: "rgba(0,0,0,0)"},
            hover: {border: "rgba(0,0,0,0)", background: "rgba(0,0,0,0)"},
        };
    });

    if (typeof network === 'undefined' || !network || !network.body) {
      return setTimeout(bindWhenReady, 50);
    }

    // network 已经可用，再尝试一次应用布局缓存
    __maybeApplyCachedLayout();

    // keep an updated cached scale after fit or stabilization so initial scale reflects fitted view
    try {
        // 关键事件：每次都刷新缓存缩放并更新 HUD
        //--------------------------------------------------------------------
        // Helper: refresh scale + HUD
        //--------------------------------------------------------------------
        //--------------------------------------------------------------------
        // Helper: refresh scale + visibility + HUD
        //--------------------------------------------------------------------
        function __refreshHUD() {
            try {
                // 更新缓存的缩放倍率
                __updateLastScale();
                const z = __getAccurateScale();

                // 如果当前没有选中任何节点 / 边，就按最新缩放重算一次“基线样式”
                // （避免把高亮状态冲掉）
                try {
                    const noNodeSelected =
                        (typeof __LAST_SELECTED_ID   === 'undefined' || __LAST_SELECTED_ID   == null);
                    const noEdgeSelected =
                        (typeof __LAST_SELECTED_EDGE === 'undefined' || __LAST_SELECTED_EDGE == null);

                    if (noNodeSelected && noEdgeSelected) {
                        __resetDimThrottled(true);   // 关键：这里重新根据 z 刷新能见度/label 显示
                        // 参数使用 true，强制刷新不节流
                    }
                } catch (e) {}

                // 更新缩放 HUD
                __showZoomHUD(z);

                // 如开启调试，则刷新调试面板
                const dbg = (window.__DEBUG && window.__DEBUG.debug_cfg)
                        ? window.__DEBUG.debug_cfg
                        : null;
                if (dbg && dbg.enable) {
                    __updateDebugHUD(window.__DEBUG);
                }
            } catch (e) {}
        }
        //====================================================================
        // 1. 事件：fit（缩放后）
        //====================================================================
        network.on('fit', () => {
            __refreshHUD();
        });


        //====================================================================
        // 2. 事件：animationFinished（手动画布或 fit 后）
        //====================================================================
        network.on('animationFinished', () => {
            __refreshHUD();
        });


        //====================================================================
        // 3. 稳定：记录开始时间（如果有该事件）
        //====================================================================
        try {
            network.on('startStabilizing', () => {
                // 统一入口：一旦开始新一轮稳定，重置所有缓存
                try {
                    const now = (typeof performance !== 'undefined' &&
                                 typeof performance.now === 'function')
                        ? performance.now()
                        : Date.now();
                    __stab_start_time       = now;
                    __stab_duration_final   = null;
                    __stab_iterations_final = null;
                    __stab_total_final      = null;
                } catch (e) {
                    __stab_start_time       = Date.now();
                    __stab_duration_final   = null;
                    __stab_iterations_final = null;
                    __stab_total_final      = null;
                }
            });
        } catch (e) {
            // 如果版本里没有 startStabilizing，忽略即可
        }

        //====================================================================
        // 4. 稳定进度：计算百分比并写入 HUD
        //====================================================================
        network.on('stabilizationProgress', (params) => {
            try {
                // 若某些版本没有触发 startStabilizing，则在第一次进度事件里兜底设置开始时间
                if (__stab_start_time == null) {
                    try {
                        const now = (typeof performance !== 'undefined' &&
                                     typeof performance.now === 'function')
                            ? performance.now()
                            : Date.now();
                        __stab_start_time = now;
                    } catch (e) {
                        __stab_start_time = Date.now();
                    }
                }

                const iter  = (params && (params.iterations ?? params.iteration)) ?? null;
                const total = (params && params.total) ?? null;

                if (iter  != null) __stab_iterations_final = iter;
                if (total != null) __stab_total_final      = total;

                // 实时 HUD（可选：你原来的行为）
                const el = document.getElementById('dbg_stab_progress');
                if (el) {
                    if (iter != null && total != null && total > 0) {
                        const pct = Math.round(iter / total * 10000) / 100;
                        el.textContent = `稳定进度: 迭代: ${iter}/${total}  进度: ${pct}%`;
                    } else if (iter != null) {
                        el.textContent = `稳定进度: 迭代: ${iter} (进度未知)`;
                    } else {
                        el.textContent = "稳定进度: 迭代: ? (进度未知)";
                    }
                }
            } catch (e) {}
        });

        //====================================================================
        // 5. 核心：稳定迭代完成 ——> 写耗时 + 关物理 + 停模拟
        //====================================================================
        network.on('stabilizationIterationsDone', (params) => {
            try {
                const iter  = (params && (params.iterations ?? params.iteration)) ?? null;
                const total = (params && params.total) ?? null;

                if (iter  != null) __stab_iterations_final = iter;
                if (total != null) __stab_total_final      = total;

                // 计算最终耗时
                let dt_ms = 0;
                try {
                    const endTs = (typeof performance !== 'undefined' &&
                                   typeof performance.now === 'function')
                        ? performance.now()
                        : Date.now();
                    if (typeof __stab_start_time === 'number') {
                        dt_ms = endTs - __stab_start_time;
                    }
                } catch(e) {
                    if (typeof __stab_start_time === 'number') {
                        dt_ms = Date.now() - __stab_start_time;
                    }
                }
                __stab_duration_final = dt_ms;
                __stab_start_time     = null;   // 标记“已经结束”

                // 若尚未声明布局来源，则视为“由物理迭代得到”
                try {
                    if (!window.__LAYOUT_SOURCE) {
                        window.__LAYOUT_SOURCE = 'physics';
                    }
                } catch(e){}

                // 直接写最终 HUD 文本
                const tEl = document.getElementById('dbg_stab_time');
                if (tEl) {
                    tEl.textContent = `稳定耗时: ${(dt_ms/1000).toFixed(2)}s`;
                }

                const pEl = document.getElementById('dbg_stab_progress');
                if (pEl) {
                    if (typeof __stab_iterations_final === 'number' &&
                        typeof __stab_total_final      === 'number' &&
                        __stab_total_final > 0) {

                        const pct = Math.round(__stab_iterations_final / __stab_total_final * 10000) / 100;
                        pEl.textContent = `稳定进度: 迭代: ${__stab_iterations_final}/${__stab_total_final}  进度: ${pct}%`;
                    } else if (typeof __stab_iterations_final === 'number') {
                        pEl.textContent = `稳定进度: 迭代: ${__stab_iterations_final} (总步数未知)`;
                    } else {
                        pEl.textContent = `稳定进度: 完成 (总步数未知)`;
                    }
                }

            } catch (e) {}

            try { __autoSaveLayoutToServer(); } catch(e){}

            // —— 你原先的：关闭物理 & 停止模拟 —— 
            try {
                network.setOptions({
                    physics: {
                        enabled: false,
                        stabilization: { enabled: false }
                    }
                });
                if (typeof network.stopSimulation === 'function') {
                    network.stopSimulation();
                }
            } catch (e) {}

            __refreshHUD();
        });

        //====================================================================
        // 6. stabilized（无须关物理，只作为兜底更新 HUD）
        //====================================================================
        network.on('stabilized', () => {
            __refreshHUD();
        });


        //====================================================================
        // 6. stabilized（无须关物理，只作为兜底更新 HUD）
        //====================================================================
        network.on('stabilized', () => {
            __refreshHUD();
        });

    } catch(e){}

        // 已移除早期的延迟轮询校准逻辑；现在依赖 fit / stabilized / zoom 事件即时更新缩放与高亮。
        // 若以后需要恢复，可在此重新插入轮询函数。

        // 若调试数据尚未到达，开启一个轮询观察者，避免一次 fetch 失败后永远不更新 HUD。
        (function __ensureDebugWatcher(){
            let tries = 0; const MAX = 50; // ~12.5s @250ms
            const timer = setInterval(() => {
                tries++;
                try {
                    const info = window.__DEBUG;
                    if (info && typeof info.size_score === 'number' && Number.isFinite(info.size_score)) {
                        try { const _d=(window.__DEBUG&&window.__DEBUG.debug_cfg)?window.__DEBUG.debug_cfg:null; if(_d&&_d.enable) __updateDebugHUD(info); } catch(e){}
                        clearInterval(timer);
                        return;
                    }
                } catch(e){}
                if (tries >= MAX) clearInterval(timer);
            }, 250);
        })();

    network.on('selectNode', (params) => {
      const id = params.nodes[0];

      // 选中假结点时，不触发高亮逻辑，也不记录 __LAST_SELECTED_ID
      if (id === DUMMY) {
        return;
      }

      __LAST_SELECTED_ID = id;
      __LAST_SELECTED_EDGE = null;   // 确保互斥

      __highlightSelection(id, params.pointer);
      // 先停一次，防止策略切换后残留
      __stopFollow();
      __alignTooltipByPolicy('select');
    });

    network.on('deselectNode', () => {
      __LAST_SELECTED_ID = null;
      if (__LAST_SELECTED_EDGE == null) {
        __stopFollow();
        __resetDim(); __hideTooltip();
      }
      // 取消选中时，尝试选中假结点，使 partial redraw 继续生效
      setTimeout(()=>__forceDummySelection(), 0);
    });

    network.on('selectEdge', (params) => {
      __LAST_SELECTED_ID   = null;                 // 互斥
      __LAST_SELECTED_EDGE = params.edges[0];
      __highlightEdgeSelection(__LAST_SELECTED_EDGE);
      __stopFollow();
      __alignTooltipByPolicy('select');
    });

    network.on('deselectEdge', () => {
      __LAST_SELECTED_EDGE = null;
      if (__LAST_SELECTED_ID == null) {            // 若没选中节点，才真正复位
        __stopFollow();
        __resetDim(); __hideTooltip();
        // 取消选中时，尝试选中假结点，使 partial redraw 继续生效
        setTimeout(()=>__forceDummySelection(), 0);
      }
    });

    network.on('click', (params) => {
    // 1) 点击到节点：无论是否已经被选中，都刷新高亮和 tooltip
    if (params.nodes && params.nodes.length) {
        const id = params.nodes[0];
        if (id !== DUMMY) {
        __LAST_SELECTED_ID   = id;
        __LAST_SELECTED_EDGE = null;
        __highlightSelection(id, params.pointer);
        __stopFollow();
        __alignTooltipByPolicy('select');
        }
        return;
    }

    // 2) 点击到边：可以选择是否在这里也刷新一次（可选）
    if (params.edges && params.edges.length) {
        const eid = params.edges[0];
        __LAST_SELECTED_ID   = null;
        __LAST_SELECTED_EDGE = eid;
        __highlightEdgeSelection(eid);
        __stopFollow();
        __alignTooltipByPolicy('select');
        return;
    }

    // 3) 点击空白：清空选中 & 复位样式
    __LAST_SELECTED_ID = null;
    __LAST_SELECTED_EDGE = null;
    __stopFollow();
    __resetDim(); __hideTooltip();
    setTimeout(()=>__forceDummySelection(), 0);
    });

    network.on('zoom', () => {
        try { __updateLastScale(); } catch(e){}

        // 1) vis 内部是否有任何选中（包括 dummy）
        let hasSelection = false;
        try {
            const selNodes = network.getSelectedNodes();
            const selEdges = network.getSelectedEdges();
            hasSelection = (selNodes && selNodes.length > 0) ||
                        (selEdges && selEdges.length > 0);
        } catch (e) {
            hasSelection = false;
        }

        // 2) 是否有“语义上的真实选择”（真节点 / 真边）
        const hasRealSelection =
            (__LAST_SELECTED_ID   != null) ||
            (__LAST_SELECTED_EDGE != null);

        // 真选中时：只做 tooltip 对齐，不乱动高亮状态
        if (hasRealSelection) {
            __alignTooltipByPolicy('zoom');
        }

        // 完全没有任何选中（连 dummy 也没选）→ 走 full reset
        if (!hasSelection) {
            __resetDimThrottled(false);
        }
        // 有选中但只是 dummy（或其它“无语义”的选中）→ 只更新边透明度
        else if (!hasRealSelection) {
            __updateEdgeOpacityForScaleThrottled(false);
        }

        __showZoomHUD(__getAccurateScale());
        try {
            const _d=(window.__DEBUG&&window.__DEBUG.debug_cfg)?window.__DEBUG.debug_cfg:null;
            if(_d&&_d.enable) __updateDebugHUD(window.__DEBUG);
        } catch(e){}
    });


    network.on('dragging', function () {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) {
        __alignTooltipByPolicy('drag');
      }
    });

    network.on('animationFinished', () => {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) __alignTooltipByPolicy('other');
    });

    // 初始按当前缩放设定基线 (may use placeholder scale; calibration will refine soon)
    // 参数使用 true，强制刷新不节流
    __resetDimThrottled(true);
    try { setTimeout(__updateLastScale, 50); } catch(e){}
    // show initial HUD quickly (will update after calibration if scale changes)
    try { __showZoomHUD(__getAccurateScale()); } catch(e){}
    // debug HUD: only create/update when debug is enabled in the sidecar/cfg
    try {
        const _dbg = (window.__DEBUG && window.__DEBUG.debug_cfg) ? window.__DEBUG.debug_cfg : null;
        if (_dbg && _dbg.enable) {
            __updateDebugHUD(window.__DEBUG);
        }
    } catch(e){}
    // 初始时选中假结点，触发 partial redraw 优化
    try { __forceDummySelection(); } catch(e){}
});

/***************************************************************************
 * SIMPLE FPS METER  —— 仅在 debug_cfg.enable = true 时启用
***************************************************************************/
(function(){

    function startFPSMeter(){
        let last   = performance.now();
        let frames = 0;
        let fps    = 0;

        const div = document.createElement("div");
        div.style.position      = "fixed";
        div.style.right         = "10px";
        div.style.bottom        = "10px";
        div.style.padding       = "4px 6px";
        div.style.background    = "rgba(0,0,0,0.6)";
        div.style.color         = "#0f0";
        div.style.fontSize      = "12px";
        div.style.zIndex        = 99999;
        div.style.borderRadius  = "4px";
        div.textContent         = "FPS: --";
        document.body.appendChild(div);

        function loop(){
            const now = performance.now();
            frames++;

            // 每 250ms 更新一次显示（不会影响性能）
            if (now - last >= 250){
                fps = frames * 1000 / (now - last);
                frames = 0;
                last   = now;
                div.textContent = "FPS: " + fps.toFixed(1);
            }
            requestAnimationFrame(loop);
        }
        requestAnimationFrame(loop);
    }

    // 等 DOM Ready，再根据 debug_cfg.enable 决定是否启动
    __moOnReady(function(){
        try {
            const cfg = (window.__DEBUG && window.__DEBUG.debug_cfg)
                ? window.__DEBUG.debug_cfg
                : null;

            // 和 Debug 菜单用同一个开关：
            if (cfg && cfg.enable) {
                startFPSMeter();
            }
        } catch(e){}
    });

})();

})(); // IIFE end
</script>
""".replace("{THEME}", THEME).replace(
         "{CFG_INTERACT_JSON}",
         _json.dumps(CFG.get("interact", {}))
     ).replace(
         "{TOOL_VERSION}", TOOL_VERSION
     )
    # insert the debug_cfg fallback into the JS (so HUD can appear even if fetch is blocked)
    js = js.replace('%s', _json.dumps(CFG.get('debug', {})))
    # only substitute the runtime filenames for the sidecar JSONs (avoid inlining large JSON blobs)
    js = js.replace('{NODE_JSON}', node_json_name).replace('{DEBUG_JSON}', debug_json_name)

    # 将脚本安全插入到 </body> 之前
    html = html_path.read_text(encoding="utf-8")
    # 如果文件位于 data/maps/<map>/ 下，需要修正相对资源路径（如 lib/bindings/utils.js）
    try:
        rel_parts = list(html_path.parts)
        needs_up = ('data' in rel_parts and 'maps' in rel_parts)
        if needs_up:
            # 计算到仓库根的层级数：例如 data/maps/aanes => 3 级，需要 ../../../
            depth_to_root = len(rel_parts) - rel_parts.index('data')
            # 这里简单写死为 ../../../ 因为当前结构固定为 root/data/maps/<map>
            prefix = '../../../'
            # 将 src="lib/... 替换为 src="/lib/... 或 prefix + lib
            # 选用绝对根相对路径更稳：/lib/... 前提是本地 server 的 root 为仓库根
            if '/lib/bindings/utils.js' not in html:
                html = html.replace('src="lib/bindings/utils.js"', 'src="/lib/bindings/utils.js"')
        else:
            # 在根目录版本上也统一用 /lib/ 形式，减少相对路径歧义
            html = html.replace('src="lib/bindings/utils.js"', 'src="/lib/bindings/utils.js"')
    except Exception:
        pass
    html = html.replace("</body>", js + "\n</body>")
    html_path.write_text(html, encoding="utf-8")

# ---------- export ----------
def export_pyvis(G: nx.DiGraph, out_html: Path):
    # local cdn to avoid blocking
    net = Network(
        height="100vh",             # 可调：初始画布高度
        width="100%", 
        directed=True, 
        bgcolor=BG_COLOR,          # 背景色
        font_color=FONT_COLOR,     # 主题文字色
        cdn_resources="local"
    )

    # 结点尺寸随度数缩放（可调：基线与缩放幅度）
    degree = dict(G.degree())
    max_deg = max(degree.values()) if degree else 1

    # mapping of node_id -> html detail (kept external to node payload)
    _NODE_DETAILS: dict = {}

    # nodes
    for nid, attrs in G.nodes(data=True):
        ntype = attrs.get('type', 'trigger')
        style = NODE_STYLE.get(ntype, NODE_STYLE['unknown'])

        # 可调：结点尺寸 size = base + scale * (degree/max)
        size_base = 10
        size_scale = 25
        size = size_base + size_scale * (degree.get(nid, 0) / max_deg)
        if ntype == 'local_var':
            size *= 0.8 # 可调：局部变量结点的尺寸倍率
            
        # Collect large detail strings externally to avoid heavy per-node payloads
        node_detail = attrs.get('title', '')
        # store detail in mapping, but do not include it in node payload
        _NODE_DETAILS[nid] = node_detail
        net.add_node(
            nid,
            label=str(attrs.get('label', nid)),
            shape=style['shape'],      # 可调（在 NODE_STYLE 中修改）
            color=style['color'],      # 可调（在 NODE_STYLE 中修改）
            size=size,
            detail=f"ID: {nid}",
            origSize=size,
        )

    # edges (no labels; semi-transparent; arrows kept)
    for u, v, ed in G.edges(data=True):
        label = ed.get('label', '')
        style = ed.get('style', 'solid')
        color = ed.get('color') or EDGE_COLOR.get(label, '#6b7280')
        dashes = style in ('dashed','dot')
        net.add_edge(
            u, v, 
            # 可调：也可在此处直接替换颜色对象中的值（如 hover/highlight）
            color={"color": color, "highlight": color, "hover": color}, 
            dashes=dashes, 
            arrows='to', 
            origColor=color,
            edgeLabel=label,)
        
    # === add a dummy node to trigger partial redraw optimization ===
    net.add_node(
        "__DUMMY__",
        label="",
        color="rgba(0,0,0,0)",  # 完全透明
        size=0.01,
        hidden=False,           # 必须可见以参与 selection pipeline
        opacity=0.0,            # 尽可能隐藏
        physics=False,          # 不参与布局
        x=0, y=0                # 不重要
    )

    # physics & interaction (no hover tooltip)
    # adapt stabilization iterations based on graph size (consider both nodes and edges)
    node_count = len(G.nodes())
    edge_count = len(G.edges())
    layout_cfg = CFG.get('layout', {}) if isinstance(CFG, dict) else {}
    # thresholds and iterations (backwards compatible)
    mt = layout_cfg.get('medium_threshold', DEFAULT_LAYOUT['medium_threshold'])
    lt = layout_cfg.get('large_threshold', DEFAULT_LAYOUT['large_threshold'])
    it_def = layout_cfg.get('iterations_default', DEFAULT_LAYOUT['iterations_default'])
    it_med = layout_cfg.get('iterations_medium', DEFAULT_LAYOUT['iterations_medium'])
    it_lrg = layout_cfg.get('iterations_large', DEFAULT_LAYOUT['iterations_large'])

    # new: configurable weights for node vs edge influence
    node_w = float(layout_cfg.get('node_weight', 1.0))
    edge_w = float(layout_cfg.get('edge_weight', 0.5))

    # compute a simple combined "size score" = node_w * N + edge_w * E
    size_score = node_w * node_count + edge_w * edge_count

    # thresholds are treated as size_score thresholds.
    # If your config used node-counts previously, convert them to score
    # using node_weight/edge_weight externally. The code compares the
    # computed `size_score` directly to the configured thresholds.
    try:
        mt_val = float(mt)
    except Exception:
        mt_val = float(DEFAULT_LAYOUT['medium_threshold'])
    try:
        lt_val = float(lt)
    except Exception:
        lt_val = float(DEFAULT_LAYOUT['large_threshold'])

    if size_score > lt_val:
        stab_iter = it_lrg
    elif size_score > mt_val:
        stab_iter = it_med
    else:
        stab_iter = it_def

    # 额外：根据节点数调 nodeDistance
    # 基础距离 280，随 sqrt(N) 缓慢增加，避免 600+ 点的图挤成一团
    import math
    base_dist   = layout_cfg.get('base_node_distance', 280)
    scale_dist  = layout_cfg.get('node_distance_scale', 6.0)  # 可在 config.yml 中覆盖
    node_dist   = base_dist + scale_dist * math.sqrt(max(node_count, 1))

    options = {
        "interaction": {
            "hover": False,
            "tooltipDelay": 0,
            "hoverConnectedEdges": False,
            "selectConnectedEdges": False
        },
        "nodes": {
            "font": {"size": 16, "strokeWidth": 0},  # 可调：节点标签字号/描边
            "chosen": False
        },
        "edges": {
            "smooth": {"type": "dynamic"},
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},                   # 可调：箭头大小
            "color": {"inherit": False, "opacity": (0.55 if THEME=='dark' else 0.45)}, # 可调：连线透明度
            "width": 1.5,  # 可调：初始默认线宽
            "font": {"size": 1},
            "selectionWidth": 0,
            "chosen": False
        },
        "physics": {
            "enabled": True,                  # 可调：启动/关闭物理模拟
            "solver": "repulsion",            # 可调：物理算法（barnesHut/repulsion/forceAtlas2Based等）
            "repulsion": {
                "nodeDistance": node_dist,       # 可调：结点间目标距离
                "centralGravity": 0.06,          # 可调：收拢到中心的力度
                "springLength": node_dist*0.55,  # 可调：边为弹簧时的自然长度
                "springConstant": 0.020,         # 可调：边为弹簧时的刚度
                "damping": 0.10                  # 可调：阻尼系数
            },
            "stabilization": {
                "enabled": True,              # 自动稳定
                "iterations": stab_iter,      # 可调：稳定时最大迭代次数
                "updateInterval": 25,         # 可调：每隔多少次迭代更新一次画布
                "fit": True                   # 稳定后自动 fit 画布
            }    
        }
    }
    net.set_options(json.dumps(options))

    # write html next
    try:
        net.write_html(str(out_html), open_browser=False, notebook=False)
    except TypeError:
        net.write_html(str(out_html), open_browser=False)

    map_name = out_html.parent.name  # or你如果已有变量就直接用现成的

    # prepare debug info and write external JSONs to avoid inlining large payloads
    debug_info = {
        'generated_at': __import__('time').time(),
        'node_count': len(G.nodes()),
        'edge_count': len(G.edges()),
        'stab_iter': stab_iter,
        'debug_cfg': CFG.get('debug', {}),
        'node_weight': node_w,
        'edge_weight': edge_w,
        'size_score': size_score,
        'tool_version': TOOL_VERSION,
        'map_name': map_name,
    }

    nd_path = out_html.with_name(out_html.stem + "_node_details.json")
    dbg_path = out_html.with_name(out_html.stem + "_debug.json")
    nd_path.write_text(_json.dumps(_NODE_DETAILS), encoding='utf-8')
    dbg_path.write_text(_json.dumps(debug_info), encoding='utf-8')

    # 关键：将交互脚本追加写进生成的 HTML，脚本会 fetch 这两个 JSON
    _append_custom_js(out_html, node_details=None, debug_info=None)

# ---------- path helpers ----------
def resolve_map_dir(arg: str|None) -> Path|None:
    if not arg: return None
    p = Path(arg)
    if p.exists():
        if p.is_dir(): return p
        if p.suffix.lower()=='.map':
            cand = REPO_ROOT / 'data' / 'maps' / p.stem
            return cand if cand.exists() else p.parent
        return p.parent
    cand = REPO_ROOT / 'data' / 'maps' / arg
    return cand if cand.exists() else None

def guess_map_name(map_arg: str|None, map_dir: Path) -> str:
    if map_arg:
        p = Path(map_arg)
        if p.suffix.lower()=='.map': return p.stem
        if not p.exists():          return str(p)
        return p.stem if p.is_file() else p.name
    return map_dir.name

def resolve_json(map_dir: Path, kind: str, map_name: str) -> Path:
    candidates = [map_dir / f'{map_name}_{kind}.json', map_dir / f'{kind}.json']
    for c in candidates:
        if c.exists(): return c
    raise FileNotFoundError(f"Missing {kind} JSON. Tried: " + ", ".join(str(c) for c in candidates))

def ensure_jsons_via_map_parser(map_arg: str|None, map_dir: Path|None, map_name: str, *, quiet: bool = False) -> Path|None:
    """
    如果缺少 *_triggers/_actions/_events.json，则尝试自动调用 map_parser.py 生成。
    返回实际可用的 map_dir（生成成功后就是 ./data/maps/<mapname>），否则返回 None。
    规则：
      1) 优先使用传入的 map_dir；
      2) 若未提供 map_dir，则尝试从 --map 解析出 .map 文件路径或名字；
      3) 找到 map 文件后，调用:  python map_parser.py --map <mapfile or name>
         - 你的 map_parser 支持“自动目录优先”的逻辑，生成到 ./data/maps/<name>/ 下。
    """
    # 已经有目录就先检查三件套
    def _has_all_json(d: Path, name: str) -> bool:
        return all((d / f"{name}_{k}.json").exists() or (d / f"{k}.json").exists()
                   for k in ("triggers","actions","events"))

    # 1) map_dir 已有且完整
    if map_dir and map_dir.exists():
        if _has_all_json(map_dir, map_name):
            return map_dir

    # 2) 推断 .map 路径
    map_file: Path|None = None
    if map_arg:
        p = Path(map_arg)
        if p.suffix.lower()=='.map' and p.exists():
            map_file = p.resolve()
        elif (Path('.')/f"{map_arg}.map").exists():
            map_file = (Path('.')/f"{map_arg}.map").resolve()

    # 如果还没找到 .map，就再在 data/maps/<name>/ 下碰碰运气
    if not map_file:
        candidate = REPO_ROOT / 'data' / 'maps' / map_name / f"{map_name}.map"
        if candidate.exists():
            map_file = candidate.resolve()

    if not map_file:
        # 找不到 map 文件，无法生成
        return None

    # 3) 调用 map_parser.py
    # Try multiple candidate locations for map_parser.py to support different repo layouts
    candidates = [
        Path(__file__).parent / 'map_parser.py',   # tools/map_parser.py
        Path(__file__).resolve().parents[1] / 'map_parser.py',  # ../map_parser.py (repo root)
        Path('map_parser.py'),                     # cwd map_parser.py
        Path('tools') / 'map_parser.py',          # tools/map_parser.py from cwd
    ]
    parser_py = None
    for c in candidates:
        if c.exists():
            parser_py = c
            break
    if not parser_py:
        _log("map_parser.py not found, cannot auto-generate JSON. Tried: " + ', '.join(str(c) for c in candidates), level='WARNING', quiet=quiet)
        return None

    import subprocess, sys as _sys
    try:
        _log(f"Invoking map_parser to parse: {map_file}", level='INFO', quiet=quiet)
        # 这里仅传入 --map，让你的 map_parser 走“自动目录优先”的分支
        # Run from repository root so map_parser's relative output logic behaves consistently
        repo_root = Path(__file__).resolve().parents[1]
        subprocess.check_call([_sys.executable, str(parser_py), str(map_file)], cwd=str(repo_root))
    except subprocess.CalledProcessError as e:
        _log(f"map_parser failed: {e}", level='ERROR', quiet=quiet)
        return None

    # 4) 生成后的目录固定是 ./data/maps/<map_name>（你的 map_parser 约定）
    out_dir = REPO_ROOT / 'data' / 'maps' / map_name
    if out_dir.exists() and _has_all_json(out_dir, map_name):
        _log(f"Generation succeeded: {out_dir}", level='INFO', quiet=quiet)
        return out_dir

    _log("map_parser ran but full JSON set still not found.", level='WARNING', quiet=quiet)
    return None

# ---------- CLI ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description='Interactive trigger graph for YR/MO maps')
    ap.add_argument('--map', default=None, help='Map name (e.g., yours), directory with JSONs, or path to .map')
    ap.add_argument('--map-dir', default=None, help='Directory containing *_triggers.json/_actions.json/_events.json')
    ap.add_argument('--actions-yml', default=str(REPO_ROOT / 'data' / 'dicts' / 'merged' / 'actions_all.yml'))
    ap.add_argument('--conditions-yml', default=str(REPO_ROOT / 'data' / 'dicts' / 'merged' / 'conditions_all.yml'))
    ap.add_argument('--out', default=None, help='Output HTML (default: <script dir>/<mapname>_trigger_graph.html)')
    ap.add_argument('--quiet', action='store_true', help='Suppress verbose generation output; write capture to <map>_report.json')
    args = ap.parse_args(argv)

    map_dir = Path(args.map_dir) if args.map_dir else resolve_map_dir(args.map)
    if not map_dir or not map_dir.exists():
        # 尝试自动生成
        guessed_name = (Path(args.map).stem if (args.map and Path(args.map).suffix.lower()=='.map')
                        else (Path(args.map).name if args.map else ''))
        # 如果没法猜到名字，就沿用后面 guess_map_name 的逻辑
        map_name_temp = guessed_name or 'temp'
        auto_dir = ensure_jsons_via_map_parser(args.map, map_dir, map_name_temp, quiet=args.quiet)
        if auto_dir and auto_dir.exists():
            map_dir = auto_dir
        else:
            _log('Could not resolve map directory. Use --map-dir or --map (name/dir/.map)', level='ERROR', print_always=True, quiet=args.quiet)
            return 2
        
    map_name = guess_map_name(args.map, map_dir) if args.map else map_dir.name

    try:
        triggers_path = resolve_json(map_dir, 'triggers', map_name)
        actions_path  = resolve_json(map_dir, 'actions',  map_name)
        events_path   = resolve_json(map_dir, 'events',   map_name)
    except FileNotFoundError as e:
        _log(str(e), level='ERROR', print_always=True, quiet=args.quiet)
        return 2

    locals_path = map_dir / f"{map_name}_locals.json"
    out_html = Path(args.out) if args.out else (map_dir / f"{map_name}_trigger_graph.html")

    actions_yml = Path(args.actions_yml)
    conditions_yml = Path(args.conditions_yml)
    if not actions_yml.exists():
        _log(f'actions YAML not found: {actions_yml}', level='ERROR', print_always=True, quiet=args.quiet)
        return 2
    if not conditions_yml.exists():
        _log(f'conditions YAML not found: {conditions_yml}', level='ERROR', print_always=True, quiet=args.quiet)
        return 2

    triggers_json = load_json(triggers_path)
    actions_json  = load_json(actions_path)
    events_json   = load_json(events_path)
    locals_dict   = load_json(locals_path) if locals_path.exists() else {}

    actions_dict = load_actions_dict(actions_yml)
    conditions_dict = load_conditions_dict(conditions_yml)

    # Overrides (optional)
    over_dir = REPO_ROOT / 'data' / 'dicts' / 'overrides'
    merge_overrides(actions_dict,    over_dir/'actions_edges.yml',    'actions', quiet=args.quiet)
    merge_overrides(conditions_dict, over_dir/'conditions_refs.yml',  'conditions', quiet=args.quiet)

    # Fallbacks (only if not defined)
    for k, v in {
        12: [{'to': 'trigger_id','from_param':2,'label':'destroy','style':'solid'}],
        22: [{'to': 'trigger_id','from_param':2,'label':'force','style':'solid'}],
        53: [{'to': 'trigger_id','from_param':2,'label':'enable','style':'solid'}],
        54: [{'to': 'trigger_id','from_param':2,'label':'disable','style':'solid'}],
        56: [{'to': 'local_id',  'from_param':2,'label':'set_local','style':'dashed'}],
        57: [{'to': 'local_id',  'from_param':2,'label':'disable_local','style':'dashed'}],
    }.items():
        actions_dict.setdefault(k, {}).setdefault('produces_edges', v)

    for k, v in {
        36: [{'param':2,'type':'local_id','role':'depends_on'}],
        37: [{'param':2,'type':'local_id','role':'depends_on'}],
    }.items():
        conditions_dict.setdefault(k, {}).setdefault('references', v)

    G = build_graph(triggers_json, actions_json, events_json, actions_dict, conditions_dict, locals_dict)

    for u, v, ed in list(G.edges(data=True)):
        ed['label'] = canon_label(ed.get('label', ''))

    export_pyvis(G, out_html)
    # Record a concise success line and persist the captured generation log into the map's report JSON
    _log(f"Graph built: {out_html}", level='INFO', print_always=not args.quiet, quiet=args.quiet)
    try:
        report_path = map_dir / f"{map_name}_report.json"
        rep = {}
        if report_path.exists():
            try:
                rep = json.loads(report_path.read_text(encoding='utf-8')) or {}
            except Exception:
                rep = {}
        rep['generation_log'] = _GEN_LOG[:]
        report_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
        _log(f"Wrote generation log into: {report_path}", level='INFO', quiet=args.quiet)
    except Exception as e:
        _log(f"Failed to write generation log into report: {e}", level='WARNING', quiet=args.quiet)
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _log(tb, level='ERROR', print_always=True)
        try:
            input("\nPress Enter to exit...")
        except Exception:
            pass
        sys.exit(1)