#!/usr/bin/env python3
"""Mutants: seeds with one thing broken inside a function body, and UCC's verdict.

Seeds (seeds.py) are corpus classes UCC accepts when built alone. A mutant is a seed
with a single edit inside a function body or state code -- a dropped semicolon, an
undefined name, a wrong argument, a misused operator, a misplaced statement. Built by
UCC, each mutant gives one more error (or, sometimes, a surprising "ok") recorded from
the compiler itself. This is what tests the body compiler's error messages on real
code: seed errors come mostly from renaming a class, and probes are small.

    mutants.py [--per 2] [--n 16] [--limit N] [--ops a,b]
    mutants.py --report

Writes <oracle root>/mutants.json: per mutant the seed's source path, its deps, the
edit (operator, script-text offsets, replacement) and UCC's outcome and errors. The
mutant's text is rebuilt from the seed and the edit, so the manifest stays small;
score.py's "mutants" section scores uparse against it.

Edits are chosen deterministically from the seed's name, so a re-run makes the same
mutants.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from sandbox import Oracle, default_root  # noqa: E402
from seeds import rename_class, EXEC_LINE  # noqa: E402

from uparse.importer import import_class, decode  # noqa: E402
from uparse.lexer import tokenize_partial, IDENT, INT, FLOAT, STRING, SYMBOL  # noqa: E402
from uparse.decl import parse_declarations  # noqa: E402

KEYWORDS = {
    "if", "else", "while", "for", "do", "until", "switch", "case", "default", "break",
    "continue", "return", "local", "const", "foreach", "self", "super", "global",
    "static", "none", "true", "false", "new", "class", "goto", "stop", "assert",
    "int", "float", "byte", "bool", "string", "name", "vector", "rotator", "array",
    "function", "event", "state", "begin", "end", "default", "arraycount", "vect",
    "rot", "rng", "outer",
}


# ---------------------------------------------------------------- the seed

def seed_source(it: dict) -> tuple[str, bytes] | None:
    """(class file name, source bytes) for a seeds.json entry, as seeds.py built it."""
    r = rename_class(EXEC_LINE.sub(b"", Path(it["source"]).read_bytes()))
    if not r:
        return None
    old, src = r
    return f"Seed_{old}.uc", src


def full_text(src: bytes) -> str:
    """The source as the importer sees it: decoded, CRLF line ends. The importer's
    script text is a prefix of this, so token offsets index it directly."""
    return decode(src).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def apply(src: bytes, edit: dict) -> bytes:
    t = full_text(src)
    return (t[:edit["start"]] + edit["text"] + t[edit["end"]:]).encode("latin-1")


def body_tokens(src: bytes):
    """(tokens, [(first, last) token index of each function body / state code])."""
    im = import_class(src)
    st = im.script_text()
    if not full_text(src).startswith(st):
        return None
    tokens, err = tokenize_partial(st)
    if err is not None:
        return None
    view = parse_declarations(tokens, "", st.count("\r\n") + 1)
    if view.get("_error") is not None:
        return None
    spans = []
    for f in view.get("fields", []):
        if f["kind"] == "Function" and f.get("_body"):
            spans.append(f["_body"])
        elif f["kind"] == "State":
            for g in f.get("fields", []):
                if g["kind"] == "Function" and g.get("_body"):
                    spans.append(g["_body"])
            if f.get("_code"):
                spans.append(f["_code"])
    return tokens, [s for s in spans if s[1] >= s[0]]


# ---------------------------------------------------------------- operators
# Each takes (tokens, span, rng) and returns an edit {start, end, text} or None.

def _pick(rng, xs):
    return rng.choice(xs) if xs else None


def _in(tokens, span, pred):
    a, b = span
    return [i for i in range(a, b + 1) if pred(i, tokens[i])]


def _edit(tok, text):
    return {"start": tok.start, "end": tok.end, "text": text}


def _matching(tokens, i, b):
    depth = 0
    for j in range(i, b + 1):
        if tokens[j].kind == SYMBOL and tokens[j].text == "(":
            depth += 1
        elif tokens[j].kind == SYMBOL and tokens[j].text == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def op_drop_semicolon(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == SYMBOL and k.text == ";"))
    return None if i is None else _edit(t[i], "")


def op_undef_ident(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == IDENT and k.text.lower() not in KEYWORDS
                       and i > 0 and t[i - 1].text != "." and t[i - 1].text != "local"
                       and not (i + 1 < len(t) and t[i + 1].text in ("(", "'"))))
    return None if i is None else _edit(t[i], "ZzUndefined")


def op_undef_call(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == IDENT and k.text.lower() not in KEYWORDS
                       and i + 1 < len(t) and t[i + 1].text == "("))
    return None if i is None else _edit(t[i], "ZzNoFunction")


def op_undef_member(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == IDENT and i > 0 and t[i - 1].text == "."
                       and k.text.lower() not in ("default", "static")))
    return None if i is None else _edit(t[i], "ZzNoMember")


def op_int_to_string(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind in (INT, FLOAT)))
    return None if i is None else _edit(t[i], '"zz"')


def op_string_to_int(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == STRING))
    return None if i is None else _edit(t[i], "7")


def _calls(t, s):
    a, b = s
    out = []
    for i in range(a, b):
        if t[i].kind == IDENT and t[i + 1].kind == SYMBOL and t[i + 1].text == "(" \
                and t[i].text.lower() not in KEYWORDS:
            j = _matching(t, i + 1, b)
            if j is not None:
                out.append((i + 1, j))
    return out


def op_drop_arg(t, s, rng):
    calls = []
    for lo, hi in _calls(t, s):
        depth, last = 0, None
        for j in range(lo + 1, hi):
            if t[j].text in ("(", "["):
                depth += 1
            elif t[j].text in (")", "]"):
                depth -= 1
            elif t[j].text == "," and depth == 0:
                last = j
        if last is not None:
            calls.append((last, hi))
        elif hi > lo + 1:
            calls.append((lo + 1, hi))       # the only argument
    c = _pick(rng, calls)
    if c is None:
        return None
    first, hi = c
    return {"start": t[first].start if t[first].text == "," else t[first].start,
            "end": t[hi].start, "text": ""}


def op_extra_arg(t, s, rng):
    c = _pick(rng, _calls(t, s))
    if c is None:
        return None
    lo, hi = c
    text = "1" if hi == lo + 1 else ", 1"
    return {"start": t[hi].start, "end": t[hi].start, "text": text}


def op_eq_to_assign(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == SYMBOL and k.text == "=="))
    return None if i is None else _edit(t[i], "=")


def op_swap_operator(t, s, rng):
    swaps = {"+": "$", "&&": "+", "<": "$", "-": "@", "||": "*", "*": "~=", "!=": "<<"}
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == SYMBOL and k.text in swaps))
    return None if i is None else _edit(t[i], swaps[t[i].text])


def op_drop_paren(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == SYMBOL and k.text == ")"))
    return None if i is None else _edit(t[i], "")


def op_return_value(t, s, rng):
    i = _pick(rng, _in(t, s, lambda i, k: k.kind == IDENT and k.text.lower() == "return"))
    if i is None:
        return None
    if t[i + 1].text == ";":
        return {"start": t[i].end, "end": t[i].end, "text": " 1"}
    j = i + 1
    while j < len(t) and t[j].text != ";":
        j += 1
    if j >= len(t):
        return None
    return {"start": t[i].end, "end": t[j].start, "text": ""}


def _statement_starts(t, s):
    """Token indexes that begin a statement: after { ; or } inside the span, past locals."""
    a, b = s
    out = []
    for i in range(a, b + 1):
        prev = t[i - 1].text if i > 0 else ""
        if prev in ("{", ";", "}") and t[i].text.lower() not in ("local", "const", "}", "else", "case", "default"):
            out.append(i)
    return out


def op_insert_statement(t, s, rng):
    i = _pick(rng, _statement_starts(t, s))
    if i is None:
        return None
    stmt = rng.choice(["break;", "1 = 2;", "continue;", "ZzUndefined = 1;", "self;",
                       "return ZzUndefined;", "local int ZzLate;", "goto ZzNoLabel;",
                       "if (1) { }", "while (\"a\") { }", "Spawn(1);", "none.Destroy();",
                       "super.ZzNoFunction();", "default.ZzNoProp = 1;"])
    return {"start": t[i].start, "end": t[i].start, "text": stmt + " "}


def op_drop_token(t, s, rng):
    a, b = s
    if b < a:
        return None
    i = rng.randint(a, b)
    return _edit(t[i], "")


OPS = {
    "drop-semicolon": op_drop_semicolon,
    "undef-ident": op_undef_ident,
    "undef-call": op_undef_call,
    "undef-member": op_undef_member,
    "int-to-string": op_int_to_string,
    "string-to-int": op_string_to_int,
    "drop-arg": op_drop_arg,
    "extra-arg": op_extra_arg,
    "eq-to-assign": op_eq_to_assign,
    "swap-operator": op_swap_operator,
    "drop-paren": op_drop_paren,
    "return-value": op_return_value,
    "insert-statement": op_insert_statement,
    "drop-token": op_drop_token,
}


def make_edits(name: str, src: bytes, per: int, ops: list[str]) -> list[dict]:
    bt = body_tokens(src)
    if bt is None:
        return []
    tokens, spans = bt
    if not spans:
        return []
    rng = random.Random(name)
    out, tries = [], 0
    while len(out) < per and tries < per * 12:
        tries += 1
        op = rng.choice(ops)
        e = OPS[op](tokens, rng.choice(spans), rng)
        if e is None or any(x["start"] == e["start"] and x["op"] == op for x in out):
            continue
        e["op"] = op
        out.append(e)
    return out


# ---------------------------------------------------------------- run

def run(items: list[dict], o: Oracle) -> list[dict]:
    groups = collections.defaultdict(list)
    for k, it in enumerate(items):
        groups[tuple(it["deps"])].append(k)
    done, t0 = 0, time.monotonic()
    for deps, ks in groups.items():
        for chunk in range(0, len(ks), 64):
            part = ks[chunk:chunk + 64]
            jobs = []
            for k in part:
                it = items[k]
                fname, src = seed_source(it)
                jobs.append({"Probe": {fname: apply(src, it["edit"])}})
            for k, r in zip(part, o.ask_many(jobs, list(deps))):
                items[k]["outcome"] = r.outcome
                items[k]["errors"] = [f"{d.file}({d.line}) : {d.message}" for d in r.errors][:3]
            done += len(part)
            rate = done / max(time.monotonic() - t0, 1e-6) * 60
            print(f"  {done}/{len(items)}  ({rate:.0f}/min)", file=sys.stderr)
    return items


def report(items: list[dict]) -> None:
    by = collections.Counter((it["edit"]["op"], it.get("outcome")) for it in items)
    ops = sorted({k[0] for k in by})
    print(f"{len(items)} mutants")
    for op in ops:
        row = {oc: by[(op, oc)] for oc in ("ok", "error", "hang")}
        print(f"  {op:18s} " + "  ".join(f"{k} {v:5d}" for k, v in row.items()))
    msgs = collections.Counter()
    for it in items:
        if it.get("errors"):
            m = it["errors"][0].split(" : ", 1)[-1]
            msgs[__import__("re").sub(r"'[^']*'", "'_'", m)] += 1
    print("most common first errors:")
    for m, n in msgs.most_common(25):
        print(f"  {n:5d}  {m[:100]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--per", type=int, default=2, help="mutants per seed")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="first N seeds only")
    ap.add_argument("--ops", default="", help="comma-separated operators (default: all)")
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    manifest = default_root() / "mutants.json"
    if a.report:
        report(json.loads(manifest.read_text()))
        return 0
    seeds = json.loads((default_root() / "seeds.json").read_text())
    seeds = [s for s in seeds if s.get("outcome") == "ok"]
    if a.limit:
        seeds = seeds[:a.limit]
    ops = [x for x in a.ops.split(",") if x] or list(OPS)
    items = []
    for s in seeds:
        r = seed_source(s)
        if r is None:
            continue
        fname, src = r
        for e in make_edits(fname, src, a.per, ops):
            items.append({"source": s["source"], "package": s["package"],
                          "deps": s.get("deps", []), "edit": e})
    print(f"{len(items)} mutants from {len(seeds)} seeds", file=sys.stderr)
    o = Oracle(a.n, timeout=a.timeout)
    run(items, o)
    manifest.write_text(json.dumps(items, indent=1))
    report(items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
