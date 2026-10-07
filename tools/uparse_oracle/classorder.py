#!/usr/bin/env python3
"""Record the order UCC's first pass visits the stock class tree.

UCC parses a package's classes parents first, but which of two classes in different
branches comes first (a Controller subclass or an Info subclass) follows the order
the loaded classes sit in memory. Native classes are registered by the engine
binary, so that order can't be read from the packages. This measures it instead:
one probe package holding a subclass of every stock class, built once, whose
"Parsing S_<n>" log lines give each stock class's turn.

    classorder.py            write scripts/uparse/data/classorder.json
    classorder.py --check    rebuild and report whether the order changed

The file lists stock classes ("Package.Class") in the order UCC reached a new
subclass of each. A package class whose parent is P is parsed at P's position;
classes the probe couldn't subclass are left out (the predictor says "don't know"
for them). Only UCC writes this file, like a probe golden.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox import Oracle, stock_packages  # noqa: E402
import reflect  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "scripts" / "uparse" / "data" / "classorder.json"
PARSING = re.compile(r"Parsing (S_\d+)\b")
BAD = re.compile(r"S_(\d+)\.uc\(\d+\) : Error")


def stock_classes(install: Path) -> list[str]:
    out = []
    for p in stock_packages(install):
        u = install / "System" / f"{p}.u"
        if not u.exists():
            continue
        pkg, objects, _ = reflect.load(u)
        for o in objects.values():
            if o["kind"] == "Class" and o["name"].lower() != "object":
                out.append(f"{pkg.name}.{o['name']}")
    return out


def measure(o: Oracle, classes: list[str]) -> tuple[list[str], list[str]]:
    """(stock classes in parse order, classes that couldn't be subclassed)."""
    left, dropped = list(classes), []
    for _ in range(200):
        files = {f"S_{i}.uc": f"class S_{i} extends {c};\r\ndefaultproperties\r\n{{\r\n}}\r\n"
                 .encode("latin-1") for i, c in enumerate(left)}
        r = o.ask({"OrderProbe": files})
        order = [left[int(m.group(1)[2:])] for m in PARSING.finditer(r.output)]
        if r.outcome == "ok":
            seen, out = set(), []
            for c in order:
                if c not in seen:
                    seen.add(c)
                    out.append(c)
            return out, dropped
        bad = BAD.search(r.output)
        if bad is None:
            raise SystemExit(f"classorder: UCC said {r.outcome}, no class to drop:\n{r.output[-2000:]}")
        i = int(bad.group(1))
        dropped.append(left.pop(i))
    raise SystemExit("classorder: too many classes failed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    o = Oracle(n=1, timeout=600, use_cache=False)
    order, dropped = measure(o, stock_classes(o.install))
    data = {"ucc_id": o.ucc_id, "order": order, "not_subclassable": dropped}
    if a.check:
        old = json.loads(OUT.read_text()) if OUT.exists() else {}
        same = old.get("order") == order
        print("classorder: unchanged" if same else "classorder: CHANGED")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=0) + "\n")
    print(f"classorder: {len(order)} classes, {len(dropped)} left out -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
