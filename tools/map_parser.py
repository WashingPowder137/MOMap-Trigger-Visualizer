from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Tuple, Any
import json
import re
import sys
from pathlib import Path

def make_output_dir_for_map(input_path: str) -> Path:
    """
    Create ./data/maps/<map_name>/ under the repository root (not under tools/).
    Return the created/existing directory Path.
    """
    # prefer repository root: two parents up from this script (tools/ -> repo root)
    repo_root = Path(__file__).resolve().parents[1]
    base_dir = repo_root / "data" / "maps"
    base_dir.mkdir(parents=True, exist_ok=True)
    map_name = Path(input_path).stem
    out_dir = base_dir / map_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir

# ---- sections ----
_SECTION_TRIGGERS = "Triggers"
_SECTION_EVENTS = "Events"
_SECTION_ACTIONS = "Actions"
_SECTION_VARIABLES = "VariableNames"  # NEW

def _split_csv(s: str) -> List[str]:
    return [tok.strip() for tok in s.split(",")]

_int_re = re.compile(r"^[+-]?\d+$")
_id_like_8_re = re.compile(r"^\d{8}$")  # 恰好 8 位数字：触发ID等零填充字段

def _to_int_or_str(tok: str) -> Any:
    tok = tok.strip()
    # ✅ 保护 8 位零填充ID：保持为字符串，避免丢前导 0
    if _id_like_8_re.match(tok):
        return tok
    # 其他“长得像整数”的，照旧转成 int
    if _int_re.match(tok):
        try:
            return int(tok)
        except ValueError:
            return tok
    return tok

def _looks_like_string(tok: str) -> bool:
    return not _int_re.match(tok)

# ---- dataclasses ----
@dataclass
class SourceLoc:
    section: str
    line: int

@dataclass
class TriggerRow:
    id: str
    house: str
    linked_trigger: Optional[str]
    name: str
    disabled: int
    easy: int
    normal: int
    hard: int
    persistence: int
    source: SourceLoc

@dataclass
class EventCondition:
    cond_id: int
    p1: int
    p2: int
    p3: Optional[str] = None

@dataclass
class EventRow:
    id: str
    num: int
    conditions: List[EventCondition]
    source: SourceLoc

@dataclass
class ActionEntry:
    act_id: int
    p1: Any
    p2: Any
    p3: Any
    p4: Any
    p5: Any
    p6: Any
    p7: Any

@dataclass
class ActionRow:
    id: str
    num: int
    actions: List[ActionEntry]
    source: SourceLoc

# NEW: local variables
@dataclass
class LocalVarRow:
    id: int
    name: str
    initial: int  # 0/1
    source: SourceLoc

@dataclass
class ParseResult:
    triggers: Dict[str, TriggerRow] = field(default_factory=dict)
    events: Dict[str, EventRow] = field(default_factory=dict)
    actions: Dict[str, ActionRow] = field(default_factory=dict)
    locals: Dict[str, LocalVarRow] = field(default_factory=dict)  # NEW
    errors: List[str] = field(default_factory=list)

_comment_re = re.compile(r"^\s*[;#/]|^\s*$")

# --- VariableNames line regex: 25=HCoreConditionB,1
_var_line_re = re.compile(r"^\s*(\d+)\s*=\s*(.*?),\s*([01])\s*$")

def parse_map_text(text: str) -> ParseResult:
    res = ParseResult()
    section = None
    lines = text.splitlines()
    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if _comment_re.match(line):
            continue
        if line.startswith("[") and line.endswith("]") and "," not in line:
            hdr = line.strip("[]").strip()
            if hdr in {_SECTION_TRIGGERS, _SECTION_EVENTS, _SECTION_ACTIONS, _SECTION_VARIABLES}:
                section = hdr
            else:
                section = None
            continue
        if section not in {_SECTION_TRIGGERS, _SECTION_EVENTS, _SECTION_ACTIONS, _SECTION_VARIABLES}:
            continue

        # VariableNames 行的格式与其他不同：可以不含 '='？（规范里含 =）
        if section == _SECTION_VARIABLES:
            # 允许空行/注释已在上面过滤
            if "=" not in line:
                res.errors.append(f"{section}:{idx}: missing '=' -> {raw}")
                continue
            try:
                _parse_variable_line(line, idx, res)
            except Exception as e:
                res.errors.append(f"{section}:{idx}: {e} | line='{raw}'")
            continue

        if "=" not in line:
            res.errors.append(f"{section}:{idx}: missing '=' -> {raw}")
            continue
        key, val = line.split("=", 1)
        id_key = key.strip()
        values = _split_csv(val)
        try:
            if section == _SECTION_TRIGGERS:
                _parse_trigger_line(id_key, values, idx, res)
            elif section == _SECTION_EVENTS:
                _parse_event_line(id_key, values, idx, res)
            elif section == _SECTION_ACTIONS:
                _parse_action_line(id_key, values, idx, res)
        except Exception as e:
            res.errors.append(f"{section}:{idx}: {e} | line='{raw}'")
    return res

def _parse_variable_line(line: str, line_no: int, res: ParseResult):
    m = _var_line_re.match(line)
    if not m:
        raise ValueError(f"Bad VariableNames line: '{line}'")
    idx = int(m.group(1))
    name = m.group(2).strip()
    initial = int(m.group(3))
    res.locals[str(idx)] = LocalVarRow(
        id=idx, name=name, initial=initial,
        source=SourceLoc(_SECTION_VARIABLES, line_no)
    )

def _parse_trigger_line(id_key: str, values: List[str], line_no: int, res: ParseResult):
    expect = 8
    if len(values) != expect:
        raise ValueError(f"Triggers expects {expect} fields, got {len(values)}: {values}")
    house = values[0]
    linked = values[1]
    linked = None if linked.lower() == "<none>" else linked
    name = values[2]
    try:
        disabled, easy, normal, hard, persistence = map(int, values[3:])
    except Exception:
        raise ValueError(f"Bad integer fields in Triggers: {values[3:]}")
    row = TriggerRow(
        id=id_key,
        house=house,
        linked_trigger=linked,
        name=name,
        disabled=disabled,
        easy=easy,
        normal=normal,
        hard=hard,
        persistence=persistence,
        source=SourceLoc(_SECTION_TRIGGERS, line_no),
    )
    res.triggers[id_key] = row

def _parse_event_line(id_key: str, values: List[str], line_no: int, res: ParseResult):
    if not values:
        raise ValueError("Events line missing NUM/values")
    try:
        num = int(values[0])
    except Exception:
        raise ValueError(f"Events NUM must be int, got '{values[0]}'")
    tokens = values[1:]
    conditions: List[EventCondition] = []
    i = 0
    while i < len(tokens):
        if i + 2 >= len(tokens):
            raise ValueError(f"Events condition triplet incomplete near tokens[{i}]: {tokens[i:]}")
        try:
            cond_id = int(tokens[i]); p1 = int(tokens[i+1]); p2 = int(tokens[i+2])
        except Exception:
            raise ValueError(f"Bad condition numeric triplet: {tokens[i:i+3]} in events tokens={tokens}")
        i += 3
        p3 = None
        if i < len(tokens) and not _int_re.match(tokens[i]):
            p3 = tokens[i]
            i += 1
        conditions.append(EventCondition(cond_id=cond_id, p1=p1, p2=p2, p3=p3))
        if len(conditions) == num:
            break
    if len(conditions) != num:
        raise ValueError(f"Events NUM={num} but parsed {len(conditions)} conditions. tokens={tokens}")
    row = EventRow(
        id=id_key,
        num=num,
        conditions=conditions,
        source=SourceLoc(_SECTION_EVENTS, line_no),
    )
    res.events[id_key] = row

def _parse_action_line(id_key: str, values: List[str], line_no: int, res: ParseResult):
    if not values:
        raise ValueError("Actions line missing NUM/values")
    try:
        num = int(values[0])
    except Exception:
        raise ValueError(f"Actions NUM must be int, got '{values[0]}'")
    tokens = values[1:]
    needed = num * 8
    if len(tokens) != needed:
        raise ValueError(f"Actions expects {needed} tokens after NUM for {num} actions, got {len(tokens)}")
    actions: List[ActionEntry] = []
    j = 0
    for k in range(num):
        chunk = tokens[j:j+8]
        j += 8
        if len(chunk) != 8:
            raise ValueError(f"Incomplete action chunk at action #{k+1}: {chunk}")
        try:
            act_id = int(chunk[0])
        except Exception:
            raise ValueError(f"Action ID must be int, got '{chunk[0]}'")
        params = [_to_int_or_str(tok) for tok in chunk[1:]]
        entry = ActionEntry(
            act_id=act_id,
            p1=params[0], p2=params[1], p3=params[2], p4=params[3],
            p5=params[4], p6=params[5], p7=params[6]
        )
        actions.append(entry)
    row = ActionRow(
        id=id_key,
        num=num,
        actions=actions,
        source=SourceLoc(_SECTION_ACTIONS, line_no),
    )
    res.actions[id_key] = row

def parse_map_file(path: str) -> ParseResult:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return parse_map_text(text)

def dump_json(pr: ParseResult, out_base: str) -> tuple[str, str, str, str, str]:
    """
    Write:
      <base>_triggers.json
      <base>_events.json
      <base>_actions.json
      <base>_locals.json     <-- NEW
      <base>_report.json
    Return their paths as a tuple of 5 strings.
    """
    base = Path(out_base)
    base.parent.mkdir(parents=True, exist_ok=True)
    triggers_path = str(base.with_name(base.name + "_triggers.json"))
    events_path   = str(base.with_name(base.name + "_events.json"))
    actions_path  = str(base.with_name(base.name + "_actions.json"))
    locals_path   = str(base.with_name(base.name + "_locals.json"))   # NEW
    report_path   = str(base.with_name(base.name + "_report.json"))

    with open(triggers_path, "w", encoding="utf-8") as f:
        json.dump({k: asdict(v) for k, v in pr.triggers.items()}, f, ensure_ascii=False, indent=2)

    with open(events_path, "w", encoding="utf-8") as f:
        json.dump({k: {
            "id": v.id,
            "num": v.num,
            "conditions": [asdict(c) for c in v.conditions],
            "source": asdict(v.source)
        } for k, v in pr.events.items()}, f, ensure_ascii=False, indent=2)

    with open(actions_path, "w", encoding="utf-8") as f:
        json.dump({k: {
            "id": v.id,
            "num": v.num,
            "actions": [asdict(a) for a in v.actions],
            "source": asdict(v.source)
        } for k, v in pr.actions.items()}, f, ensure_ascii=False, indent=2)

    # NEW: locals
    with open(locals_path, "w", encoding="utf-8") as f:
        json.dump({k: {
            "id": v.id,
            "name": v.name,
            "initial": v.initial,
            "source": asdict(v.source)
        } for k, v in pr.locals.items()}, f, ensure_ascii=False, indent=2)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"errors": pr.errors}, f, ensure_ascii=False, indent=2)

    return triggers_path, events_path, actions_path, locals_path, report_path

def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Parse [Triggers]/[Events]/[Actions]/[VariableNames] from a Mental Omega .map file")
    ap.add_argument("input", help=".map file path")
    ap.add_argument("--out-base", default=None,
                    help="Optional output file base path (prefix). If omitted, will use ./data/maps/<map_name>/<map_name>")
    args = ap.parse_args(argv)
    pr = parse_map_file(args.input)
    if args.out_base:
        out_base = Path(args.out_base)
    else:
        out_dir = make_output_dir_for_map(args.input)
        out_base = out_dir / Path(args.input).stem
    t,e,a,l,r = dump_json(pr, str(out_base))
    print("Wrote:")
    print(t); print(e); print(a); print(l); print(r)
    if pr.errors:
        print("\nParse warnings/errors:")
        for err in pr.errors[:20]:
            print(" -", err)
        if len(pr.errors) > 20:
            print(f"... and {len(pr.errors)-20} more")
    return 0

if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))