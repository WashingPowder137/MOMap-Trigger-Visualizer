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
        "HUD_FADE_DELAY_MS": 2000,
        "HUD_FADE_DURATION_MS": 1000,
    }
}

# ---- try load external config (优先级更高) ----
# 你可以把路径定成 data/config/trigger_viz.yml 或同目录 config.yml
CFG_PATHS = [
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

def merge_overrides(base: dict[int, dict], override_path: Path, top_key: str):
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
        print(f"ℹ️ Applied overrides from {override_path}")
    except Exception as e:
        print(f"⚠️ Failed to apply overrides from {override_path}: {e}")

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

def _letters_to_waypoint(s: str) -> int | str:
    """
    将 1 位或 2 位大写字母的路径点标签转换为十进制索引：
      A..Z -> 0..25
      AA..AZ -> 0..25, BA..BZ -> 26..51, ...
    若是数字或数字字符串则直接转 int；无法解析则原样返回。
    """
    if isinstance(s, str) and s.isalpha() and s.isupper():
        if len(s) == 1:
            return ord(s[0]) - 65
        if len(s) == 2:
            return (ord(s[0]) - 65) * 26 + (ord(s[1]) - 65)
    try:
        return int(s)
    except Exception:
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

def _append_custom_js(html_path: Path):
    """
    Append our interaction script (zoom-aware opacity + label fading + zoom HUD)
    to the generated HTML. 主题通过 {THEME} 占位符注入 ('dark' or 'light')。
    """
    js = r"""
<script type="text/javascript">
/* ===== Mental Omega Trigger Graph – injected interaction script ===== */
window.__THEME = "{THEME}";                    // 注入主题色 'dark' | 'light'，若未替换则自动按 <body> 背景判断
window.__CFG_INTERACT = {CFG_INTERACT_JSON};   // 注入用户设置

(function(){
  // ---------- 私有 DOM 就绪工具（避免与其它脚本同名冲突） ----------
  const __moOnReady = (fn) => {
    if (document.readyState !== 'loading') { try { fn(); } catch(e){} }
    else { document.addEventListener('DOMContentLoaded', () => { try { fn(); } catch(e){} }, { once:true }); }
  };

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

  // Zoom HUD
  const HUD_FADE_DELAY_MS    = (window.__CFG_INTERACT?.HUD_FADE_DELAY_MS    ?? 2000);  // 没有操作多久时间后开始淡出
  const HUD_FADE_DURATION_MS = (window.__CFG_INTERACT?.HUD_FADE_DURATION_MS ?? 1000);  // 淡出动画时长
  const HUD_BG               = "rgba(0,0,0,0.55)";
  const HUD_TEXT_COLOR       = "#ffffff";
  const HUD_BORDER_RADIUS    = "8px";
  const HUD_FONT             = "12px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";
  let   __hudFadeTimer = null;
  
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
      __showTooltipNear({ DOM:{ x: dom.x, y: dom.y } }, (node && node.detail) || '(No details)');
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
      const scale = network.getScale();
      const baseOpacity = __baseEdgeOpacityForScale(scale);
      const LC = __getLabelColors();
      const SC = __getStrokeColors();

      const nodesAll = network.body.data.nodes.get();
      nodesAll.forEach(n => {
        if (n.origSize == null) n.origSize = n.size;   // 确保一定会写入一次基线尺寸
        n.opacity = 1.0;
        n.size    = n.origSize || n.size
        n.font = Object.assign({}, n.font, { 
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

  function __highlightSelection(selectedId, pointer){
    const scale = network.getScale();
    const baseOpacity = __baseEdgeOpacityForScale(scale);
    const LC = __getLabelColors();
    const SC = __getStrokeColors();

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
      const isSelf = (n.id === selectedId);
      const isNeighbor = neighborSet.has(n.id);
      if (isSelf) {
        n.opacity = 1.0;
        const base = n.origSize || n.size || 14;
        n.size = base * 1.35;
        n.font = Object.assign({}, n.font, { color: LC.normal, strokeWidth: 5, strokeColor: SC.normal });
      } else if (isNeighbor) {
        n.opacity = 1.0;
        n.size = n.origSize || n.size;
        n.font = Object.assign({}, n.font, { color: LC.normal, strokeWidth: 5, strokeColor: SC.normal });
      } else {
        n.opacity = 0.12;
        n.size = n.origSize || n.size;
        n.font = Object.assign({}, n.font, { color: LC.faded, strokeWidth: 5, strokeColor: SC.faded });
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

    const e = network.body.data.edges.get(edgeId);
    if (!e) return;

    const a = e.from, b = e.to;

    // 节点：仅端点不淡化（选中端点略放大），其他淡化
    const nodesAll = network.body.data.nodes.get();
    nodesAll.forEach(n => {
      if (n.origSize == null) n.origSize = n.size;
      const isEndpoint = (n.id === a || n.id === b);
      n.opacity = isEndpoint ? 1.0 : 0.12;
      n.size    = isEndpoint ? (n.origSize||n.size)*1.25 : (n.origSize||n.size);
      n.font    = Object.assign({}, n.font, {
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

  __moOnReady(function bindWhenReady(){
    // 设置全局标签颜色（一次性）
    const isDark = __computeTheme();
    window.__LABEL_COLOR_NORMAL = isDark ? "#e5e7eb" : "#111111";
    window.__LABEL_COLOR_FADED  = isDark ? "rgba(229,231,235,0.26)" : "rgba(17,17,17,0.22)";

    if (typeof network === 'undefined' || !network || !network.body) {
      return setTimeout(bindWhenReady, 50);
    }

    network.on('selectNode', (params) => {
      __LAST_SELECTED_ID = null;
      __LAST_SELECTED_ID = params.nodes[0];
      __highlightSelection(__LAST_SELECTED_ID, params.pointer);
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
      }
    });
    network.on('click', (params) => {
      if (!params.nodes.length && !params.edges.length) { 
        __LAST_SELECTED_ID = null;
        __LAST_SELECTED_EDGE = null;
        __stopFollow();
        __resetDim(); __hideTooltip(); }
    });
    network.on('zoom', () => {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) {
        __alignTooltipByPolicy('zoom');
      } else {
        __resetDim();
      }
      __showZoomHUD(network.getScale());
    });

    network.on('dragging', function () {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) {
        __alignTooltipByPolicy('drag');
      }
    });

    // 可选：物理动画结束时再“对齐”一次（即便 rAF 在跑也无碍）
    network.on('stabilized', () => {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) __alignTooltipByPolicy('other');
    });
    network.on('animationFinished', () => {
      if (__LAST_SELECTED_ID != null || __LAST_SELECTED_EDGE != null) __alignTooltipByPolicy('other');
    });

    // 初始按当前缩放设定基线
    __resetDim();
  });

})(); // IIFE end
</script>
""".replace("{THEME}", THEME).replace(
       "{CFG_INTERACT_JSON}",
       _json.dumps(CFG.get("interact", {}))
    )

    # 将脚本安全插入到 </body> 之前
    html = html_path.read_text(encoding="utf-8")
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
            
        net.add_node(
            nid,
            label=str(attrs.get('label', nid)),
            shape=style['shape'],      # 可调（在 NODE_STYLE 中修改）
            color=style['color'],      # 可调（在 NODE_STYLE 中修改）
            size=size,
            detail=attrs.get('title', ''),
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

    # physics & interaction (no hover tooltip)
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
                "nodeDistance": 320,          # 可调：结点间目标距离
                "centralGravity": 0.10,       # 可调：收拢到中心的力度
                "springLength": 200,          # 可调：边为弹簧时的自然长度
                "springConstant": 0.025,      # 可调：边为弹簧时的刚度
                "damping": 0.10               # 可调：阻尼系数
            },
            "stabilization": {"iterations": 120}    # 可调：稳定时迭代次数
        }
    }
    net.set_options(json.dumps(options))

    # write html next
    try:
        net.write_html(str(out_html), open_browser=False, notebook=False)
    except TypeError:
        net.write_html(str(out_html), open_browser=False)

    # 关键：将交互脚本追加写进生成的 HTML
    _append_custom_js(out_html)

# ---------- path helpers ----------
def resolve_map_dir(arg: str|None) -> Path|None:
    if not arg: return None
    p = Path(arg)
    if p.exists():
        if p.is_dir(): return p
        if p.suffix.lower()=='.map':
            cand = Path('data')/'maps'/p.stem
            return cand if cand.exists() else p.parent
        return p.parent
    cand = Path('data')/'maps'/arg
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

def ensure_jsons_via_map_parser(map_arg: str|None, map_dir: Path|None, map_name: str) -> Path|None:
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
        candidate = Path('data')/'maps'/map_name/f"{map_name}.map"
        if candidate.exists():
            map_file = candidate.resolve()

    if not map_file:
        # 找不到 map 文件，无法生成
        return None

    # 3) 调用 map_parser.py
    parser_py = Path(__file__).parent / "map_parser.py"
    if not parser_py.exists():
        # 如果 map_parser.py 不在同目录，再尝试当前工作目录
        if Path("map_parser.py").exists():
            parser_py = Path("map_parser.py")
        else:
            print("⚠️ 找不到 map_parser.py，无法自动生成 JSON。")
            return None

    import subprocess, sys as _sys
    try:
        print(f"ℹ️ 正在调用 map_parser 解析：{map_file}")
        # 这里仅传入 --map，让你的 map_parser 走“自动目录优先”的分支
        subprocess.check_call([_sys.executable, str(parser_py), str(map_file)])
    except subprocess.CalledProcessError as e:
        print(f"❌ map_parser 运行失败：{e}")
        return None

    # 4) 生成后的目录固定是 ./data/maps/<map_name>（你的 map_parser 约定）
    out_dir = Path('data')/'maps'/map_name
    if out_dir.exists() and _has_all_json(out_dir, map_name):
        print(f"✅ 生成成功：{out_dir}")
        return out_dir

    print("⚠️ map_parser 运行后仍未找到完整的 JSON。")
    return None

# ---------- CLI ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description='Interactive trigger graph for YR/MO maps')
    ap.add_argument('--map', default=None, help='Map name (e.g., yours), directory with JSONs, or path to .map')
    ap.add_argument('--map-dir', default=None, help='Directory containing *_triggers.json/_actions.json/_events.json')
    ap.add_argument('--actions-yml', default=str(Path('data')/'dicts'/'merged'/'actions_all.yml'))
    ap.add_argument('--conditions-yml', default=str(Path('data')/'dicts'/'merged'/'conditions_all.yml'))
    ap.add_argument('--out', default=None, help='Output HTML (default: <script dir>/<mapname>_trigger_graph.html)')
    args = ap.parse_args(argv)

    map_dir = Path(args.map_dir) if args.map_dir else resolve_map_dir(args.map)
    if not map_dir or not map_dir.exists():
        # 尝试自动生成
        guessed_name = (Path(args.map).stem if (args.map and Path(args.map).suffix.lower()=='.map')
                        else (Path(args.map).name if args.map else ''))
        # 如果没法猜到名字，就沿用后面 guess_map_name 的逻辑
        map_name_temp = guessed_name or 'temp'
        auto_dir = ensure_jsons_via_map_parser(args.map, map_dir, map_name_temp)
        if auto_dir and auto_dir.exists():
            map_dir = auto_dir
        else:
            print('❌ Could not resolve map directory. Use --map-dir or --map (name/dir/.map)')
            return 2
        
    map_name = guess_map_name(args.map, map_dir) if args.map else map_dir.name

    try:
        triggers_path = resolve_json(map_dir, 'triggers', map_name)
        actions_path  = resolve_json(map_dir, 'actions',  map_name)
        events_path   = resolve_json(map_dir, 'events',   map_name)
    except FileNotFoundError as e:
        print('❌', e); return 2

    locals_path = map_dir / f"{map_name}_locals.json"
    out_html = Path(args.out) if args.out else (Path(__file__).parent / f"{map_name}_trigger_graph.html")

    actions_yml = Path(args.actions_yml)
    conditions_yml = Path(args.conditions_yml)
    if not actions_yml.exists():
        print(f'❌ actions YAML not found: {actions_yml}')
        return 2
    if not conditions_yml.exists():
        print(f'❌ conditions YAML not found: {conditions_yml}')
        return 2

    triggers_json = load_json(triggers_path)
    actions_json  = load_json(actions_path)
    events_json   = load_json(events_path)
    locals_dict   = load_json(locals_path) if locals_path.exists() else {}

    actions_dict = load_actions_dict(actions_yml)
    conditions_dict = load_conditions_dict(conditions_yml)

    # Overrides (optional)
    over_dir = Path('data')/'dicts'/'overrides'
    merge_overrides(actions_dict,    over_dir/'actions_edges.yml',    'actions')
    merge_overrides(conditions_dict, over_dir/'conditions_refs.yml',  'conditions')

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
    print(f"✅ Graph built: {out_html}")
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            input("\nPress Enter to exit...")
        except Exception:
            pass
        sys.exit(1)
