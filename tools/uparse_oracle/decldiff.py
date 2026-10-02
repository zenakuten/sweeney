#!/usr/bin/env python3
"""Diff the parser's declarations against the compiled class, field by field.

    decldiff.py Engine/Actor.uc              one class (path under the engine checkout,
                                             or any .uc path)
    decldiff.py --summary [--level full]     tally which attribute differs, over the
                                             engine corpus, most common first
    decldiff.py --summary --local            the same over the local corpus

The comparison is score.py's: fields matched by (kind, lowercased name), every
attribute compared except script sizes and replication offsets.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import reflect  # noqa: E402
import uparse  # noqa: E402
from score import engine_packages, local_packages, install_root, engine_root  # noqa: E402

IGNORED = {"script_size", "rep_offset"}


def compiled_views(pkg: str) -> dict:
    u = install_root() / "System" / f"{pkg}.u"
    if not u.exists():
        return {}
    rpkg, objects, _ = reflect.load(u)
    return {o["name"].lower(): reflect.class_view(rpkg, objects, p)
            for p, o in objects.items() if o["kind"] == "Class"}


def _key(f: dict) -> tuple:
    return (f["kind"], f["name"].lower())


def diff_fields(want: list, got: list, where: str, out: list) -> None:
    w = {_key(f): f for f in want}
    g = {_key(f): f for f in got}
    for k in sorted(set(w) - set(g)):
        out.append((where, "missing", f"{k[0]} {w[k]['name']}", None, None))
    for k in sorted(set(g) - set(w)):
        out.append((where, "extra", f"{k[0]} {g[k]['name']}", None, None))
    for k in sorted(set(w) & set(g)):
        a, b = w[k], g[k]
        name = f"{where}.{a['name']}" if where else a["name"]
        for attr in sorted((set(a) | set(b)) - {"name", "kind", "fields"} - IGNORED):
            va, vb = a.get(attr), b.get(attr)
            if attr == "flags":
                va, vb = sorted(va or []), sorted(vb or [])
            same = (va == vb) if (a["kind"] == "Const" and attr == "value") else \
                json.dumps(va, sort_keys=True).lower() == json.dumps(vb, sort_keys=True).lower()
            if not same:
                out.append((name, attr, f"{k[0]}", va, vb))
        if "fields" in a or "fields" in b:
            diff_fields(a.get("fields", []), b.get("fields", []), name, out)


def diff_class(want: dict, got: dict | None) -> list:
    out: list = []
    if got is None:
        return [("", "unparsed", "", None, None)]
    for attr in ("super", "config", "within"):
        if (want.get(attr) or "").lower() != (got.get(attr) or "").lower():
            out.append(("<class>", attr, "", want.get(attr), got.get(attr)))
    if sorted(want.get("class_flags", [])) != sorted(got.get("class_flags", [])):
        out.append(("<class>", "class_flags", "", sorted(want.get("class_flags", [])),
                    sorted(got.get("class_flags", []))))
    diff_fields(want.get("fields", []), got.get("fields", []), "", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--examples", type=int, default=2)
    a = ap.parse_args()

    if a.file:
        p = Path(a.file)
        if not p.exists() and engine_root():
            p = engine_root() / a.file
        pkg = p.parent.parent.name if p.parent.name == "Classes" else p.parent.name
        want = compiled_views(pkg).get(p.stem.lower())
        if want is None:
            print(f"no compiled class {p.stem} in {pkg}.u")
            return 1
        got = uparse.declarations(p.name, p.read_bytes(), package=pkg, path=p)
        for where, attr, kind, va, vb in diff_class(want, got):
            if va is None and vb is None:
                print(f"{attr:8s} {kind} {where}")
            else:
                print(f"{where:40s} {attr:12s} want={json.dumps(va)}  got={json.dumps(vb)}")
        return 0

    packages = local_packages() if a.local else engine_packages()
    tally = collections.Counter()
    examples = collections.defaultdict(list)
    for pkg, files in packages:
        views = compiled_views(pkg)
        for f in files:
            want = views.get(f.stem.lower())
            if want is None:
                continue
            got = uparse.declarations(f.name, f.read_bytes(), package=pkg, path=f)
            seen = set()
            for where, attr, kind, va, vb in diff_class(want, got):
                key = (attr, kind)
                if key in seen:
                    continue
                seen.add(key)
                tally[key] += 1
                if len(examples[key]) < a.examples:
                    examples[key].append(f"{pkg}/{f.stem}:{where} want={json.dumps(va)[:70]} "
                                         f"got={json.dumps(vb)[:70]}")
    print("classes affected, by (attribute, field kind):")
    for (attr, kind), n in tally.most_common(40):
        print(f"{n:6d}  {attr} {kind}")
        for e in examples[(attr, kind)]:
            print(f"          {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
