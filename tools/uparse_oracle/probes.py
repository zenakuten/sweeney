#!/usr/bin/env python3
"""Record UCC's verdict on the probe suite: the goldens the parser is scored against.

A probe is tests/uparse/probes/<id>.uc, a single class named Probe, built alone as
package Probe. Its golden is <id>.json beside it:

    {"outcome": "error", "errors": [{"file": "Probe.uc", "line": 3,
     "message": "Missing ';' before 'function'"}], "ucc_id": "..."}

Only UCC writes goldens. Nobody edits one by hand -- that is what keeps the
scoreboard honest when agents work on the parser unattended.

    probes.py               record probes that have no golden yet
    probes.py --refresh     re-record every probe (after a UCC change, say)
    probes.py --check       re-run UCC and report goldens it now disagrees with
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox import Oracle  # noqa: E402

PROBES = Path(__file__).resolve().parents[2] / "tests" / "uparse" / "probes"


def golden_of(result, ucc_id: str) -> dict:
    return {
        "outcome": result.outcome,
        "errors": [{"file": d.file, "line": d.line, "message": d.message}
                   for d in result.errors],
        "ucc_id": ucc_id,
    }


def load_probes(root: Path = PROBES) -> list[tuple[str, bytes, dict | None]]:
    out = []
    for uc in sorted(root.glob("*.uc")):
        gj = uc.with_suffix(".json")
        golden = json.loads(gj.read_text()) if gj.exists() else None
        out.append((uc.stem, uc.read_bytes(), golden))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=10,
                    help="per build; a real probe build takes ~1s")
    a = ap.parse_args()

    probes = load_probes()
    todo = probes if (a.refresh or a.check) else [p for p in probes if p[2] is None]
    if not todo:
        print(f"all {len(probes)} probes have goldens")
        return 0
    o = Oracle(a.n, timeout=a.timeout, use_cache=not (a.refresh or a.check))
    results = o.ask_many([{"Probe.uc": src} for _, src, _ in todo])

    bad = 0
    for (pid, _, old), r in zip(todo, results):
        new = golden_of(r, o.ucc_id)
        if a.check:
            same = old and {k: old[k] for k in ("outcome", "errors")} == \
                {k: new[k] for k in ("outcome", "errors")}
            if not same:
                bad += 1
                print(f"DIFFERS  {pid}: golden {old and old['outcome']}, UCC now {r.outcome}")
            continue
        (PROBES / f"{pid}.json").write_text(json.dumps(new, indent=1) + "\n")
        first = f"  {r.errors[0].line}: {r.errors[0].message}" if r.errors else ""
        print(f"{r.outcome:6s} {pid}{first}")
    if a.check:
        print(f"{len(todo) - bad} of {len(todo)} goldens agree with UCC")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
