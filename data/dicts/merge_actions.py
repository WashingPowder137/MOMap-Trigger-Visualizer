#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_actions.py
- Merge multiple actions_*.yml files into a single actions_all.yml
- Later files override earlier entries on duplicate action IDs
- Validate structure: params length = 7, indices in references/context/value_fields valid, etc.
- Emit actions_merge.log with detailed report.

Usage:
  python merge_actions.py                  # auto-glob actions_*.yml in script directory (excluding actions_all.yml)
  python merge_actions.py file1.yml file2.yml ...  # explicit files, order matters (later wins)
  python merge_actions.py --strict         # treat warnings as errors (non-zero exit)
"""

from __future__ import annotations
import sys
import re
import argparse

# --- dependency check for PyYAML ---
try:
    import yaml
except ImportError:
    print("⚠️ Missing dependency: PyYAML. Please install it first:\n   pip install pyyaml")
    sys.exit(1)
# --- end dependency check ---

from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime

ALLOWED_KEYS = {
    "name", "description", "params", "references", "context_refs", "value_fields",
    "produces_edges", "notes"
}
ACTION_KEY = "actions"

@dataclass
class Issue:
    level: str  # "INFO" | "WARN" | "ERROR"
    msg: str

@dataclass
class MergeState:
    actions: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    sources: Dict[int, str] = field(default_factory=dict)
    issues: List[Issue] = field(default_factory=list)
    files: List[Path] = field(default_factory=list)

def log(state: MergeState, level: str, msg: str) -> None:
    state.issues.append(Issue(level, msg))

def is_intlike(x: Any) -> bool:
    return isinstance(x, int) or (isinstance(x, str) and re.fullmatch(r"[+-]?\d+", x) is not None)

def to_int(x: Any) -> Optional[int]:
    if isinstance(x, int):
        return x
    if isinstance(x, str) and re.fullmatch(r"[+-]?\d+", x):
        try:
            return int(x)
        except ValueError:
            return None
    return None

def load_yaml_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def validate_action(aid: int, act: Dict[str, Any], state: MergeState) -> None:
    # name
    if "name" not in act or not isinstance(act["name"], str) or not act["name"]:
        log(state, "ERROR", f"[{aid}] missing or invalid 'name'")
    # params
    params = act.get("params")
    if not isinstance(params, list):
        log(state, "ERROR", f"[{aid}] 'params' must be a list of length 7")
    else:
        if len(params) != 7:
            log(state, "ERROR", f"[{aid}] 'params' must have length 7, got {len(params)}")
        else:
            for i, p in enumerate(params, start=1):
                if not isinstance(p, str):
                    log(state, "WARN", f"[{aid}] params[{i}] should be a string type label, got {type(p).__name__}")
    # warn about needs_string key (should not appear)
    if "needs_string" in act:
        log(state, "WARN", f"[{aid}] contains 'needs_string' which should be omitted for Actions")
    # unknown keys
    for k in act.keys():
        if k not in ALLOWED_KEYS:
            # allow unknown top-level like vendor-specific extras but warn
            log(state, "INFO", f"[{aid}] unknown key '{k}' kept as-is")
    # references
    refs = act.get("references", [])
    if refs is not None and not isinstance(refs, list):
        log(state, "ERROR", f"[{aid}] 'references' must be a list")
    else:
        for r in refs or []:
            if not isinstance(r, dict):
                log(state, "ERROR", f"[{aid}] reference entry must be a dict")
                continue
            p = r.get("param")
            if not is_intlike(p):
                log(state, "ERROR", f"[{aid}] reference.param must be int 1..7")
            else:
                pi = to_int(p)
                if pi is None or pi < 1 or pi > 7:
                    log(state, "ERROR", f"[{aid}] reference.param={p} out of range 1..7")
            if "type" not in r or not isinstance(r["type"], str) or not r["type"]:
                log(state, "ERROR", f"[{aid}] reference.type must be non-empty string")
            role = r.get("role")
            if role is not None and not isinstance(role, str):
                log(state, "WARN", f"[{aid}] reference.role should be string if provided")
    # context_refs
    crefs = act.get("context_refs", [])
    if crefs is not None and not isinstance(crefs, list):
        log(state, "ERROR", f"[{aid}] 'context_refs' must be a list")
    else:
        for r in crefs or []:
            if not isinstance(r, dict):
                log(state, "ERROR", f"[{aid}] context_ref entry must be a dict")
                continue
            if "source" not in r or not isinstance(r["source"], str) or not r["source"]:
                log(state, "ERROR", f"[{aid}] context_ref.source must be non-empty string")
            if "type" not in r or not isinstance(r["type"], str) or not r["type"]:
                log(state, "ERROR", f"[{aid}] context_ref.type must be non-empty string")
            role = r.get("role")
            if role is not None and not isinstance(role, str):
                log(state, "WARN", f"[{aid}] context_ref.role should be string if provided")
    # value_fields
    vfs = act.get("value_fields", [])
    if vfs is not None and not isinstance(vfs, list):
        log(state, "ERROR", f"[{aid}] 'value_fields' must be a list")
    else:
        for v in vfs or []:
            if not isinstance(v, dict):
                log(state, "ERROR", f"[{aid}] value_field entry must be a dict")
                continue
            p = v.get("param")
            if not is_intlike(p):
                log(state, "ERROR", f"[{aid}] value_field.param must be int 1..7")
            else:
                pi = to_int(p)
                if pi is None or pi < 1 or pi > 7:
                    log(state, "ERROR", f"[{aid}] value_field.param={p} out of range 1..7")
            if "name" not in v or not isinstance(v["name"], str) or not v["name"]:
                log(state, "ERROR", f"[{aid}] value_field.name must be non-empty string")
            unit = v.get("unit")
            if unit is not None and not isinstance(unit, str):
                log(state, "WARN", f"[{aid}] value_field.unit should be string if provided")
    # produces_edges (optional, for graph rendering)
    pe = act.get("produces_edges", [])
    if pe is not None and not isinstance(pe, list):
        log(state, "ERROR", f"[{aid}] 'produces_edges' must be a list")
    else:
        for e in pe or []:
            if not isinstance(e, dict):
                log(state, "ERROR", f"[{aid}] produces_edges entry must be a dict")
                continue
            fp = e.get("from_param")
            if fp is not None and not is_intlike(fp):
                log(state, "ERROR", f"[{aid}] produces_edges.from_param must be int 1..7 when present")
            elif fp is not None:
                fpi = to_int(fp)
                if fpi is None or fpi < 1 or fpi > 7:
                    log(state, "ERROR", f"[{aid}] produces_edges.from_param={fp} out of range 1..7")
            for key in ("to","label","style"):
                val = e.get(key)
                if val is not None and not isinstance(val, str):
                    log(state, "WARN", f"[{aid}] produces_edges.{key} should be string if provided")

def merge_files(files: List[Path], strict: bool=False) -> Tuple[Dict[str, Any], List[Issue]]:
    state = MergeState(files=files)
    for f in files:
        try:
            data = load_yaml_file(f)
        except Exception as e:
            log(state, "ERROR", f"Failed to read {f.name}: {e}")
            continue
        acts = data.get(ACTION_KEY)
        if acts is None:
            log(state, "WARN", f"{f.name}: missing top-level 'actions'")
            continue
        if not isinstance(acts, dict):
            log(state, "ERROR", f"{f.name}: 'actions' must be a mapping")
            continue
        for k, v in acts.items():
            if not is_intlike(k):
                log(state, "ERROR", f"{f.name}: action id '{k}' is not an integer key")
                continue
            aid = to_int(k)
            if not isinstance(v, dict):
                log(state, "ERROR", f"{f.name}: action [{aid}] entry must be a mapping")
                continue
            # validate now
            validate_action(aid, v, state)
            # merge: later files override
            prev = state.sources.get(aid)
            state.actions[aid] = v
            state.sources[aid] = f.name
            if prev and prev != f.name:
                log(state, "INFO", f"[{aid}] overridden: {prev} -> {f.name}")
    # build output document
    merged = {
        "schema_version": 1,
        "source": "merged",
        "notes": "Merged by merge_actions.py; later files override earlier ones.",
        "actions": {int(k): state.actions[k] for k in sorted(state.actions.keys())}
    }
    return merged, state.issues

def write_outputs(dirpath: Path, merged: Dict[str, Any], issues: List[Issue]) -> None:
    out_dir = dirpath / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_yaml = out_dir / "actions_all.yml"
    out_log = out_dir / "actions_merge.log"
    with out_yaml.open("w", encoding="utf-8") as f:
        yaml.dump(merged, f, sort_keys=False, allow_unicode=True)
    # Log
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[merge_actions] {now}",
             f"Merged actions: {len(merged.get('actions', {}))} entries",
             "Issues:"]
    for it in issues:
        lines.append(f" - {it.level}: {it.msg}")
    with out_log.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Merge actions_*.yml into actions_all.yml with validation.")
    ap.add_argument("files", nargs="*", help="Input YAML files in desired precedence order (later wins). If omitted, auto-glob actions_*.yml in script directory.")
    ap.add_argument("--strict", action="store_true", help="Treat WARN as ERROR and exit non-zero if any WARN/ERROR occurs")
    args = ap.parse_args(argv)

    script_dir = Path(__file__).parent
    if args.files:
        files = [Path(p) if Path(p).is_absolute() else (script_dir / p) for p in args.files]
    else:
        # 同时匹配 .yml / .yaml；大小写不敏感；并且必须以 actions_ 开头
        import re
        pat = re.compile(r"^actions_.*\.(yml|yaml)$", re.IGNORECASE)
        candidates = [p for p in script_dir.iterdir() if p.is_file() and pat.match(p.name)]
        # 排除已有的合并产物
        candidates = [p for p in candidates if p.name.lower() != "actions_all.yml" and p.name.lower() != "actions_all.yaml"]
        files = sorted(candidates, key=lambda x: x.name.lower())

    if not files:
        print("No input files found. Provide files explicitly or place actions_*.yml|yaml next to this script.", file=sys.stderr)
        return 2

    # 记录即将合并的文件，排查时更直观
    print("[merge_actions] input files in order:")
    for p in files:
        print(" -", p.name)

    merged, issues = merge_files(files, strict=args.strict)
    write_outputs(script_dir, merged, issues)

    # exit code handling
    has_error = any(i.level == "ERROR" for i in issues)
    has_warn = any(i.level == "WARN" for i in issues)
    if has_error or (args.strict and has_warn):
        print(f"Completed with issues. See actions_merge.log (errors={has_error}, warns={has_warn}).", file=sys.stderr)
        return 1 if has_error else 3
    print("Merged successfully. See actions_all.yml and actions_merge.log.")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
