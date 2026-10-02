#!/usr/bin/env python3
"""Record UCC's verdict on the probe suite: the goldens the parser is scored against.

A probe is tests/uparse/probes/<id>.uc, a single class named Probe, built alone as
package Probe. Its golden is <id>.json beside it:

    {"outcome": "error", "errors": [{"file": "Probe.uc", "line": 3,
     "message": "Missing ';' before 'function'"}], "ucc_id": "..."}

When the probe compiles, the golden also holds "defaults": the class defaults UCC
actually stored, decoded by reflect.py. That is what catches the silent failures --
a value written one way and stored as something else, or not stored at all.

A suite is many generated probes in one file, tests/uparse/suites/<name>.jsonl, one
JSON object per line: {"id": ..., "src": <source, Latin-1 text>, "golden": {...}}.
Generators write the src; this script fills in the golden, the same as for a probe.
A case may add "files": {"Other.uc": <source>}, more classes built in package Probe
beside Probe.uc (see `package_of`).

Only UCC writes goldens. Nobody edits one by hand -- that is what keeps the
scoreboard honest when agents work on the parser unattended.

    probes.py               record probes that have no golden yet
    probes.py --refresh     re-record every probe (after a UCC change, say)
    probes.py --check       re-run UCC and report goldens it now disagrees with
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox import Oracle, default_root  # noqa: E402
import reflect  # noqa: E402

PROBES = Path(__file__).resolve().parents[2] / "tests" / "uparse" / "probes"
SUITES = PROBES.parent / "suites"


def golden_of(result, ucc_id: str, u_dir: Path | None = None) -> dict:
    g = {
        "outcome": result.outcome,
        "errors": [{"file": d.file, "line": d.line, "message": d.message}
                   for d in result.errors],
        "ucc_id": ucc_id,
    }
    if result.outcome == "ok" and u_dir and (u_dir / "Probe.u").exists():
        pkg, objects, _ = reflect.load(u_dir / "Probe.u")
        g["defaults"] = reflect.decode_defaults(pkg, objects, "Probe.Probe")
        lits = literals_of(objects)
        if lits:
            g["literals"] = lits
    return g


LITERAL_TOKENS = {"IntConst", "IntConstByte", "FloatConst", "StringConst",
                  "UnicodeStringConst", "NameConst", "ByteConst", "ObjectConst",
                  "VectorConst", "RotationConst"}
FIXED_LITERALS = {"IntZero": 0, "IntOne": 1, "True": True, "False": False}


def literals_of(objects: dict) -> dict:
    """{function: [literal, ...]}: the constants UCC compiled into each function of
    the probe class, in bytecode order. How the lexer read `1e5` or "a\\nb" shows
    up here as the value UCC actually stored."""
    out = {}
    for path, o in objects.items():
        if o["kind"] != "Function" or not path.startswith("Probe.Probe.") or not o.get("script"):
            continue
        found = []

        def walk(tok):
            if not isinstance(tok, list) or len(tok) < 2 or not isinstance(tok[1], str):
                return
            name = tok[1]
            if name in LITERAL_TOKENS:
                found.append([name, tok[2]])
            elif name in FIXED_LITERALS:
                found.append([name, FIXED_LITERALS[name]])
            for x in tok[2:]:
                if isinstance(x, list):
                    if x and isinstance(x[0], list):
                        for y in x:
                            walk(y)
                    else:
                        walk(x)

        for tok in o["script"]:
            walk(tok)
        if found:
            out[path[len("Probe.Probe."):]] = found
    return out


# Suite cases with more classes than Probe.uc: id -> {file name: source bytes}.
EXTRA_FILES: dict[str, dict[str, bytes]] = {}


def package_of(pid: str, src: bytes) -> dict[str, bytes]:
    """The files of a probe's package: Probe.uc plus any the case adds."""
    return {"Probe.uc": src, **EXTRA_FILES.get(pid, {})}


def load_probes(root: Path = PROBES, suites: bool = True) -> list[tuple[str, bytes, dict | None]]:
    """Every probe and suite case as (id, source bytes, golden or None).
    A suite case's id is <suite>/<case id>."""
    out = []
    for uc in sorted(root.glob("*.uc")):
        gj = uc.with_suffix(".json")
        golden = json.loads(gj.read_text()) if gj.exists() else None
        out.append((uc.stem, uc.read_bytes(), golden))
    if suites:
        for path in sorted(SUITES.glob("*.jsonl")):
            for rec in _read_suite(path):
                pid = f"{path.stem}/{rec['id']}"
                if rec.get("files"):
                    EXTRA_FILES[pid] = {k: v.encode("latin-1") for k, v in rec["files"].items()}
                out.append((pid, rec["src"].encode("latin-1"), rec.get("golden")))
    return out


def _read_suite(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _save_golden(pid: str, golden: dict) -> None:
    if "/" not in pid:
        (PROBES / f"{pid}.json").write_text(json.dumps(golden, indent=1) + "\n")
        return
    suite, cid = pid.split("/", 1)
    path = SUITES / f"{suite}.jsonl"
    recs = _read_suite(path)
    for rec in recs:
        if rec["id"] == cid:
            rec["golden"] = golden
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--quiet", "-q", action="store_true")
    ap.add_argument("--only", help="only probes whose id starts with this (e.g. a suite name)")
    ap.add_argument("--timeout", type=float, default=10,
                    help="per build; a real probe build takes ~1s")
    a = ap.parse_args()

    probes = load_probes()
    if a.only:
        probes = [p for p in probes if p[0].startswith(a.only)]
    todo = probes if (a.refresh or a.check) else [p for p in probes if p[2] is None]
    if not todo:
        print(f"all {len(probes)} probes have goldens")
        return 0
    o = Oracle(a.n, timeout=a.timeout, use_cache=False)
    work = Path(tempfile.mkdtemp(dir=default_root()))
    import concurrent.futures as cf

    def build(k):
        keep = work / str(k)
        return o.ask({"Probe": package_of(todo[k][0], todo[k][1])}, keep_u=keep), keep

    with cf.ThreadPoolExecutor(a.n) as ex:
        built = list(ex.map(build, range(len(todo))))

    # A hang under a full parallel load can be a stall. Before one becomes a golden,
    # build it again on its own with a generous limit (a native class once "hung"
    # at 10s/20s under load and compiled in 1.5s alone).
    solo = Oracle(1, timeout=max(30.0, a.timeout * 3), use_cache=False)
    for k, (r, keep) in enumerate(built):
        if r.outcome == "hang":
            r2 = solo.ask({"Probe": package_of(todo[k][0], todo[k][1])}, keep_u=keep, retry_hang=False)
            if r2.outcome != "hang":
                built[k] = (r2, keep)

    bad = 0
    keys = ("outcome", "errors", "defaults", "literals")
    for (pid, _, old), (r, keep) in zip(todo, built):
        new = golden_of(r, o.ucc_id, keep)
        if a.check:
            same = old and {k: old.get(k) for k in keys} == {k: new.get(k) for k in keys}
            if not same:
                bad += 1
                print(f"DIFFERS  {pid}: golden {old and old['outcome']}, UCC now {r.outcome}")
            continue
        _save_golden(pid, new)
        if not a.quiet:
            first = f"  {r.errors[0].line}: {r.errors[0].message}" if r.errors else ""
            print(f"{r.outcome:6s} {pid}{first}")
    shutil.rmtree(work, ignore_errors=True)
    if a.check:
        print(f"{len(todo) - bad} of {len(todo)} goldens agree with UCC")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
