#!/usr/bin/env python3
"""Find corpus classes that compile on their own, as seeds for mutation.

Every class in the corpus is copied into a throwaway package under a new name
(Foo -> Seed_Foo, so it cannot collide with the original, which stays loaded) and
built alone. The ones UCC accepts are seeds: a mutant of a seed that fails, fails
because of the mutation. The ones it rejects are kept too -- each is a real piece of
code with UCC's verdict attached, which is probe data in its own right.

Corpora:
  - the engine script source (~/.sweeney/config.json "engine_source"), flat
    <Package>/<Class>.uc, built against the stock packages
  - mod trees, <Mod>/Classes/*.uc, given with --mod. A mod whose .u is in the
    install's System/ is loaded as a dependency, so its classes can refer to each
    other; otherwise only the stock packages are there. Extra dependencies, in load
    order, go after an '=':  --mod ~/UT2004/WS3SPN=WSUTComp,WS3SPN

    seeds.py [--engine] [--mod DIR[=DEP,...]] ... [--n 16] [--limit N]
    seeds.py --report                 summarize the last run

Writes <oracle root>/seeds.json and the accepted sources under <oracle root>/seeds/.
#exec lines are dropped before building: they import resources relative to the
original package directory, which a probe package does not have.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sandbox import Oracle, default_root, sweeney_config  # noqa: E402

SEED_PREFIX = "Seed_"
CLASS_DECL = re.compile(rb"\bclass\s+(\w+)", re.IGNORECASE)
EXEC_LINE = re.compile(rb"^[ \t]*#exec\b[^\n]*", re.IGNORECASE | re.MULTILINE)


def code_mask(src: bytes) -> bytearray:
    """1 where src is code, 0 inside comments, strings and name literals."""
    mask = bytearray(b"\x01" * len(src))
    i, n = 0, len(src)
    while i < n:
        c = src[i:i + 1]
        two = src[i:i + 2]
        if two == b"//":
            j = src.find(b"\n", i)
            j = n if j < 0 else j
        elif two == b"/*":
            j = src.find(b"*/", i + 2)
            j = n if j < 0 else j + 2
        elif c in (b'"', b"'"):
            j = i + 1
            while j < n and src[j:j + 1] not in (c, b"\n"):
                j += 2 if src[j:j + 1] == b"\\" else 1
            j = min(j + 1, n)
        else:
            i += 1
            continue
        mask[i:j] = bytes(j - i)
        i = j
    return mask


def rename_class(src: bytes) -> tuple[str, bytes] | None:
    """Rename class Foo -> Seed_Foo, including the class's references to itself.

    Every code token equal to the class name (case-insensitive, as UCC is) is
    renamed unless it is package-qualified (preceded by '.'). So `local Foo F; F =
    Self;` and `OnClick=Foo.InternalOnClick` in defaultproperties still refer to the
    class being built. Strings and name literals (class'Foo') are left alone; they
    keep referring to the original, which stays loaded. Returns (old name, source).
    """
    mask = code_mask(src)
    old = None
    for m in CLASS_DECL.finditer(src):
        if mask[m.start()]:
            old = m.group(1)
            break
    if old is None:
        return None
    new = SEED_PREFIX.encode() + old
    ident = re.compile(rb"(?<![\w.])" + re.escape(old) + rb"(?!\w)", re.IGNORECASE)
    out, last = [], 0
    for m in ident.finditer(src):
        if not mask[m.start()]:
            continue
        out += [src[last:m.start()], new]
        last = m.end()
    out.append(src[last:])
    return old.decode("latin-1"), b"".join(out)


def corpus(engine: bool, mods: list[str], install: Path) -> list[dict]:
    """Every class to try, with the dependencies it is built against."""
    items = []
    if engine:
        root = Path(sweeney_config().get("engine_source") or "")
        if not root.is_dir():
            raise SystemExit("no engine source: run sweeney setup")
        for f in sorted(root.glob("*/*.uc")):
            items.append({"source": str(f), "package": f.parent.name, "deps": []})
    for spec in mods:
        path, _, extra = spec.partition("=")
        d = Path(path).expanduser()
        name = d.name
        if extra:
            deps = [x for x in extra.split(",") if x]
        elif (install / "System" / f"{name}.u").exists():
            deps = [name]
        else:
            deps = []
        for f in sorted((d / "Classes").glob("*.uc")):
            items.append({"source": str(f), "package": name, "deps": deps})
    return items


def run(items: list[dict], o: Oracle, out_dir: Path) -> list[dict]:
    jobs, keep = [], []
    for it in items:
        src = Path(it["source"]).read_bytes()
        r = rename_class(EXEC_LINE.sub(b"", src))
        if not r:
            it.update(outcome="skipped", reason="no class declaration")
            continue
        old, new_src = r
        if old.lower() == "object":
            it.update(outcome="skipped", reason="Core.Object has no parent")
            continue
        it.update({"class": old, "seed_class": SEED_PREFIX + old})
        jobs.append((it, {"Probe": {f"{SEED_PREFIX}{old}.uc": new_src}}))
        keep.append(new_src)

    # Group by dependency list: ask_many takes one dependency list per batch.
    groups = collections.defaultdict(list)
    for k, (it, job) in enumerate(jobs):
        groups[tuple(it["deps"])].append(k)
    done, t0 = 0, time.monotonic()
    for deps, ks in groups.items():
        for chunk in range(0, len(ks), 64):
            part = ks[chunk:chunk + 64]
            results = o.ask_many([jobs[k][1] for k in part], list(deps))
            for k, r in zip(part, results):
                it = jobs[k][0]
                it["outcome"] = r.outcome
                it["errors"] = [f"{d.file}({d.line}) : {d.message}" for d in r.errors][:3]
                if r.outcome == "ok":
                    p = out_dir / it["package"] / f"{it['seed_class']}.uc"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(keep[k])
                    it["seed"] = str(p)
            done += len(part)
            rate = done / max(time.monotonic() - t0, 1e-6) * 60
            print(f"  {done}/{len(jobs)}  ({rate:.0f}/min)", file=sys.stderr)
    return items


def report(items: list[dict]) -> None:
    by_pkg = collections.defaultdict(collections.Counter)
    for it in items:
        by_pkg[it["package"]][it["outcome"]] += 1
    total = collections.Counter()
    print(f"{'package':28s} {'ok':>5s} {'error':>6s} {'hang':>5s} {'other':>6s}")
    for pkg in sorted(by_pkg):
        c = by_pkg[pkg]
        total.update(c)
        other = sum(v for k, v in c.items() if k not in ("ok", "error", "hang"))
        print(f"{pkg:28s} {c['ok']:5d} {c['error']:6d} {c['hang']:5d} {other:6d}")
    other = sum(v for k, v in total.items() if k not in ("ok", "error", "hang"))
    print(f"{'TOTAL':28s} {total['ok']:5d} {total['error']:6d} {total['hang']:5d} {other:6d}")

    # The commonest reasons a class does not stand alone, with the message's
    # variable parts (quoted names, numbers) folded so similar errors group.
    reasons = collections.Counter()
    for it in items:
        if it["outcome"] == "error" and it.get("errors"):
            msg = it["errors"][0].split(" : ", 1)[-1]
            reasons[re.sub(r"'[^']*'", "'_'", msg)] += 1
    print("\ntop rejection reasons:")
    for msg, n in reasons.most_common(15):
        print(f"  {n:5d}  {msg}")
    hangs = [it["source"] for it in items if it["outcome"] == "hang"]
    if hangs:
        print("\nhangs:")
        for h in hangs:
            print("  " + h)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path)
    ap.add_argument("--install", type=Path)
    ap.add_argument("--engine", action="store_true", help="include the engine source")
    ap.add_argument("--mod", action="append", default=[], metavar="DIR[=DEP,...]")
    ap.add_argument("--n", type=int, default=16, help="parallel sandboxes")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--limit", type=int, help="try only the first N classes")
    ap.add_argument("--report", action="store_true", help="summarize the last run")
    a = ap.parse_args()

    root = Path(a.root or default_root())
    manifest = root / "seeds.json"
    if a.report:
        report(json.loads(manifest.read_text()))
        return 0
    if not a.engine and not a.mod:
        ap.error("give --engine and/or --mod")

    o = Oracle(a.n, root, a.install, a.timeout)
    items = corpus(a.engine, a.mod, o.install)
    if a.limit:
        items = items[:a.limit]
    print(f"trying {len(items)} classes on {a.n} sandboxes", file=sys.stderr)
    items = run(items, o, root / "seeds")
    manifest.write_text(json.dumps(items, indent=1))
    report(items)
    print(f"\nwrote {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
