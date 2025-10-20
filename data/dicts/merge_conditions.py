#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_conditions.py
- Merge multiple conditions_*.yml files into merged/conditions_all.yml
- Later files override earlier entries on duplicate condition IDs
- Validate structure: params length 2 or 3, indices in references/context/value_fields valid, etc.
- Emit merged/conditions_merge.log with detailed report.

Usage:
  python merge_conditions.py                         # auto-glob conditions_*.yml in script directory (excluding conditions_all.yml)
  python merge_conditions.py file1.yml file2.yml ... # explicit files, order matters (later wins)
  python merge_conditions.py --strict                # treat warnings as errors (non-zero exit)
"""

from __future__ import annotations
import sys
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime

# --- dependency check for PyYAML ---
try:
    import yaml
except ImportError:
    print("⚠️ Missing dependency: PyYAML.\nPlease install it first:\n   pip install pyyaml")
    sys.exit(1)
# --- end dependency check ---

ALLOWED_KEYS = {
    "name", "description", "params", "needs_string",
    "references", "context_refs", "value_fields", "produces_edges", "notes"
}
COND_KEY = "conditions"

@dataclass
class Issue:
    level: str  # "INFO" | "WARN" | "ERROR"
    msg: str

@dataclass
class MergeState:
    conds: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    sources: Dict[int, str] = field(default_factory=dict)
    issues: List[Issue] = field(default_factory=list)
    files: List[Path] = field(default_factory=list)

def log(state: MergeState, level: str, msg: str) -> None:
    state.issues.append(Issue(level, msg))

def is_intlike(x: Any) -> bool:
    import re as _re
    return isinstance(x, int) or (isinstance(x, str) and _re.fullmatch(r"[+-]?\d+", x) is not None)

def to_int(x: Any) -> Optional[int]:
    if isinstance(x, int):
        return x
    if isinstance(x, str) and is_intlike(x):
        try:
            return int(x)
        except ValueError:
            return None
    return None

def load_yaml_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        import yaml as _yaml
        return _yaml.safe_load(f) or {}

def validate_condition(cid: int, c: Dict[str, Any], state: MergeState) -> None:
    # name
    if "name" not in c or not isinstance(c["name"], str) or not c["name"]:
        log(state, "ERROR", f"[{cid}] missing or invalid 'name'")
    # params: 2 or 3 entries
    params = c.get("params")
    if not isinstance(params, list):
        log(state, "ERROR", f"[{cid}] 'params' must be a list of length 2 or 3")
    else:
        if len(params) not in (2,3):
            log(state, "ERROR", f"[{cid}] 'params' must have length 2 or 3, got {len(params)}")
        else:
            for i, p in enumerate(params, start=1):
                if not isinstance(p, str):
                    log(state, "WARN", f"[{cid}] params[{i}] should be a string type label, got {type(p).__name__}")
    # needs_string: allowed for conditions
    if "needs_string" in c and not isinstance(c["needs_string"], bool):
        log(state, "WARN", f"[{cid}] 'needs_string' should be boolean when present")
    # unknown keys
    for k in c.keys():
        if k not in ALLOWED_KEYS:
            log(state, "INFO", f"[{cid}] unknown key '{k}' kept as-is")
    # references (param range 1..3)
    refs = c.get("references", [])
    if refs is not None and not isinstance(refs, list):
        log(state, "ERROR", f"[{cid}] 'references' must be a list")
    else:
        for r in refs or []:
            if not isinstance(r, dict):
                log(state, "ERROR", f"[{cid}] reference entry must be a dict")
                continue
            p = r.get("param")
            if not is_intlike(p):
                log(state, "ERROR", f"[{cid}] reference.param must be int 1..3")
            else:
                pi = to_int(p)
                if pi is None or pi < 1 or pi > 3:
                    log(state, "ERROR", f"[{cid}] reference.param={p} out of range 1..3")
            if "type" not in r or not isinstance(r["type"], str) or not r["type"]:
                log(state, "ERROR", f"[{cid}] reference.type must be non-empty string")
            role = r.get("role")
            if role is not None and not isinstance(role, str):
                log(state, "WARN", f"[{cid}] reference.role should be string if provided")
    # context_refs
    crefs = c.get("context_refs", [])
    if crefs is not None and not isinstance(crefs, list):
        log(state, "ERROR", f"[{cid}] 'context_refs' must be a list")
    else:
        for r in crefs or []:
            if not isinstance(r, dict):
                log(state, "ERROR", f"[{cid}] context_ref entry must be a dict")
                continue
            if "source" not in r or not isinstance(r["source"], str) or not r["source"]:
                log(state, "ERROR", f"[{cid}] context_ref.source must be non-empty string")
            if "type" not in r or not isinstance(r["type"], str) or not r["type"]:
                log(state, "ERROR", f"[{cid}] context_ref.type must be non-empty string")
            role = r.get("role")
            if role is not None and not isinstance(role, str):
                log(state, "WARN", f"[{cid}] context_ref.role should be string if provided")
    # value_fields
    vfs = c.get("value_fields", [])
    if vfs is not None and not isinstance(vfs, list):
        log(state, "ERROR", f"[{cid}] 'value_fields' must be a list")
    else:
        for v in vfs or []:
            if not isinstance(v, dict):
                log(state, "ERROR", f"[{cid}] value_field entry must be a dict")
                continue
            p = v.get("param")
            if not is_intlike(p):
                log(state, "ERROR", f"[{cid}] value_field.param must be int 1..3")
            else:
                pi = to_int(p)
                if pi is None or pi < 1 or pi > 3:
                    log(state, "ERROR", f"[{cid}] value_field.param={p} out of range 1..3")
            if "name" not in v or not isinstance(v["name"], str) or not v["name"]:
                log(state, "ERROR", f"[{cid}] value_field.name must be non-empty string")
            unit = v.get("unit")
            if unit is not None and not isinstance(unit, str):
                log(state, "WARN", f"[{cid}] value_field.unit should be string if provided")
    # produces_edges (optional)
    pe = c.get("produces_edges", [])
    if pe is not None and not isinstance(pe, list):
        log(state, "ERROR", f"[{cid}] 'produces_edges' must be a list")
    else:
        for e in pe or []:
            if not isinstance(e, dict):
                log(state, "ERROR", f"[{cid}] produces_edges entry must be a dict")
                continue
            fp = e.get("from_param")
            if fp is not None and not is_intlike(fp):
                log(state, "ERROR", f"[{cid}] produces_edges.from_param must be int 1..3 when present")
            elif fp is not None:
                fpi = to_int(fp)
                if fpi is None or fpi < 1 or fpi > 3:
                    log(state, "ERROR", f"[{cid}] produces_edges.from_param={fp} out of range 1..3")
            for key in ("to","label","style"):
                val = e.get(key)
                if val is not None and not isinstance(val, str):
                    log(state, "WARN", f"[{cid}] produces_edges.{key} should be string if provided")

def merge_files(files: List[Path], strict: bool=False) -> Tuple[Dict[str, Any], List[Issue]]:
    state = MergeState(files=files)
    for f in files:
        try:
            data = load_yaml_file(f)
        except Exception as e:
            log(state, "ERROR", f"Failed to read {f.name}: {e}")
            continue
        conds = data.get(COND_KEY, data)  # allow flat id->entry files
        if conds is None:
            log(state, "WARN", f"{f.name}: missing top-level 'conditions'")
            continue
        if not isinstance(conds, dict):
            log(state, "ERROR", f"{f.name}: 'conditions' must be a mapping")
            continue
        for k, v in conds.items():
            if not is_intlike(k):
                log(state, "ERROR", f"{f.name}: condition id '{k}' is not an integer key")
                continue
            cid = to_int(k)
            if not isinstance(v, dict):
                log(state, "ERROR", f"{f.name}: condition [{cid}] entry must be a mapping")
                continue
            validate_condition(cid, v, state)
            prev = state.sources.get(cid)
            state.conds[cid] = v
            state.sources[cid] = f.name
            if prev and prev != f.name:
                log(state, "INFO", f"[{cid}] overridden: {prev} -> {f.name}")
    merged = {
        "schema_version": 1,
        "source": "merged",
        "notes": "Merged by merge_conditions.py; later files override earlier ones.",
        "conditions": {int(k): state.conds[k] for k in sorted(state.conds.keys())}
    }
    return merged, state.issues

def write_outputs(dirpath: Path, merged: Dict[str, Any], issues: List[Issue]) -> None:
    out_dir = dirpath / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_yaml = out_dir / "conditions_all.yml"
    out_log = out_dir / "conditions_merge.log"
    with out_yaml.open("w", encoding="utf-8") as f:
        import yaml as _yaml
        _yaml.dump(merged, f, sort_keys=False, allow_unicode=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[merge_conditions] {now}",
             f"Merged conditions: {len(merged.get('conditions', {}))} entries",
             "Issues:"]
    for it in issues:
        lines.append(f" - {it.level}: {it.msg}")
    with out_log.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Merge conditions_*.yml into merged/conditions_all.yml with validation.")
    ap.add_argument("files", nargs="*", help="Input YAML files in desired precedence order (later wins). If omitted, auto-glob conditions_*.yml in script directory.")
    ap.add_argument("--strict", action="store_true", help="Treat WARN as ERROR and exit non-zero if any WARN/ERROR occurs")
    args = ap.parse_args(argv)

    script_dir = Path(__file__).parent
    if args.files:
        files = [Path(p) if Path(p).is_absolute() else (script_dir / p) for p in args.files]
    else:
        auto = [p for p in script_dir.glob("conditions_*.yml") if p.name != "conditions_all.yml"]
        files = sorted(auto)

    if not files:
        print("No input files found. Provide files explicitly or place conditions_*.yml next to this script.", file=sys.stderr)
        return 2

    merged, issues = merge_files(files, strict=args.strict)
    write_outputs(script_dir, merged, issues)

    has_error = any(i.level == "ERROR" for i in issues)
    has_warn = any(i.level == "WARN" for i in issues)
    if has_error or (args.strict and has_warn):
        print(f"Completed with issues. See merged/conditions_merge.log (errors={has_error}, warns={has_warn}).", file=sys.stderr)
        return 1 if has_error else 3
    print("Merged successfully. See merged/conditions_all.yml and merged/conditions_merge.log.")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
