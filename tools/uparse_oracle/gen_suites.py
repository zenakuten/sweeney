#!/usr/bin/env python3
"""Generate probe suites: many small cases aimed at one behaviour, labelled by UCC.

    gen_suites.py quotes [--n 300]      write tests/uparse/suites/quotes.jsonl
    then: probes.py --only quotes       record UCC's verdict on each case

Generation is seeded, so a suite regenerates identically. Writing a suite replaces
it, goldens included, so re-run probes.py afterwards.

Suites:
  quotes   short runs of quotes, backslashes, slashes and stars placed in a line
           comment, a block comment, a code string and a defaultproperties string.
           This is the importer's string scan, the source of the comment hang.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SUITES = Path(__file__).resolve().parents[2] / "tests" / "uparse" / "suites"

HEADER = "class Probe extends Info;\n"
EMPTY_DEFAULTS = "defaultproperties\n{\n}\n"

QUOTE_CONTEXTS = {
    "line-comment": lambda x: f"{HEADER}// {x}\nvar int M;\n{EMPTY_DEFAULTS}",
    "trailing-comment": lambda x: f"{HEADER}var int M; // {x}\n{EMPTY_DEFAULTS}",
    "block-comment": lambda x: f"{HEADER}/* {x} */\nvar int M;\n{EMPTY_DEFAULTS}",
    "block-comment-body": lambda x: f"{HEADER}/*\n{x}\n*/\nvar int M;\n{EMPTY_DEFAULTS}",
    "code-string": lambda x: f"{HEADER}function F() {{ Log(\"{x}\"); }}\n{EMPTY_DEFAULTS}",
    "default-string": lambda x: f"{HEADER}var string S;\ndefaultproperties\n{{\n    S=\"{x}\"\n}}\n",
}


def gen_quotes(n: int, rng: random.Random) -> list[dict]:
    alphabet = ['"', '"', "\\", "\\", "a", " ", "/", "*"]
    cases, seen = [], set()
    contexts = list(QUOTE_CONTEXTS)
    while len(cases) < n:
        ctx = contexts[len(cases) % len(contexts)]
        body = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 7)))
        if '"' not in body and "\\" not in body:
            continue
        # A '*/' would end the block comment early and '//' changes what a line is;
        # both are interesting, but keep this suite about quotes and backslashes.
        if ctx.startswith("block") and "*/" in body:
            continue
        key = (ctx, body)
        if key in seen:
            continue
        seen.add(key)
        cases.append({"id": f"{ctx}-{len(cases):03d}", "src": QUOTE_CONTEXTS[ctx](body)})
    return cases


GENERATORS = {"quotes": gen_quotes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("suite", choices=sorted(GENERATORS))
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=2004)
    a = ap.parse_args()
    cases = GENERATORS[a.suite](a.n, random.Random(a.seed))
    SUITES.mkdir(parents=True, exist_ok=True)
    path = SUITES / f"{a.suite}.jsonl"
    path.write_text("".join(json.dumps(c) + "\n" for c in cases))
    print(f"wrote {len(cases)} cases to {path}; now run probes.py --only {a.suite}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
