#!/usr/bin/env python3
"""The parser's scoreboard: how often uparse agrees with UCC.

Runs offline -- every answer it compares against was recorded from UCC earlier, by
probes.py (the goldens), seeds.py (seeds.json) or the compiled packages in the
install (via reflect.py). Nothing here starts UCC.

Sections, each a set of agreement rates in [0, 1]:

  probes   tests/uparse/probes goldens: same outcome (ok/error/hang); and, over the
           probes where UCC reported an error, the same first error line and message
  hang     recall and precision of hang predictions, per source (probes, seeds)
  corpus   engine source files (and local mod files) that check_file() parses clean
  decl     engine classes (and local mod classes) whose declarations() match the
           compiled class, by field names and kinds ("names") and in full ("full")
  seeds    seeds.json, UCC's verdict on every corpus class built alone: same outcome,
           first error line and message

Two ratchet files, so the repo only carries numbers anyone can reproduce:

  tests/uparse/score.json          probes, engine corpus, engine decl
  ~/.sweeney/oracle/score-local.json   everything (adds local mods and seeds)

    score.py                 print the scoreboard and the biggest disagreements
    score.py --check         also fail if any number dropped below its ratchet
    score.py --update        write the ratchet files (refuses if a number dropped)
    score.py --only probes   restrict to some sections (comma-separated)
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import uparse  # noqa: E402
from probes import load_probes  # noqa: E402
from sandbox import default_root, sweeney_config  # noqa: E402

REPO_SCORE = REPO / "tests" / "uparse" / "score.json"
LOCAL_SCORE = default_root() / "score-local.json"
LOCAL_CORPUS = default_root() / "corpus-local.txt"
SECTIONS = ("probes", "hang", "corpus", "decl", "seeds")

# Metrics only reproducible on this machine (local mods, seeds) carry this mark and
# stay out of the repo ratchet.
LOCAL = "local"


# ---------------------------------------------------------------- helpers

def template(msg: str) -> str:
    """Fold a message's variable parts so similar errors cluster together."""
    msg = re.sub(r"'[^']*'", "'_'", msg)
    return re.sub(r"\b\d+\b", "N", msg)


class Tally:
    """Agreement counts plus the disagreements, clustered."""

    def __init__(self):
        self.counts: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
        self.clusters: dict[tuple, list] = collections.defaultdict(list)

    def add(self, metric: str, agree: bool) -> None:
        c = self.counts[metric]
        c[0] += agree
        c[1] += 1

    def diverge(self, section: str, key: tuple, example: tuple) -> None:
        self.clusters[(section,) + key].append(example)

    def rates(self) -> dict[str, dict]:
        return {m: {"agree": a, "total": n, "rate": round(a / n, 4) if n else None}
                for m, (a, n) in sorted(self.counts.items())}


def compare_outcome(t: Tally, section: str, ident: str, size: int,
                    ucc: dict, pred: "uparse.Prediction") -> None:
    """Score one labelled case: UCC's {outcome, errors} vs a Prediction."""
    t.add(f"{section}.outcome", pred.outcome == ucc["outcome"])
    # Per-outcome too: most cases compile, so an always-"ok" parser would score well
    # on the overall rate alone (85% on seeds). Agreement on UCC's errors can't be faked.
    t.add(f"{section}.outcome.{ucc['outcome']}", pred.outcome == ucc["outcome"])
    if ucc["outcome"] == "error" and ucc["errors"]:
        want = ucc["errors"][0]
        got = pred.errors[0] if pred.errors else None
        t.add(f"{section}.line", bool(got) and got.line == want["line"])
        t.add(f"{section}.message", bool(got) and got.message == want["message"])
    if ucc["outcome"] == "hang":
        t.add(f"hang.{section}.recall", pred.outcome == "hang")
    if pred.outcome == "hang":
        t.add(f"hang.{section}.precision", ucc["outcome"] == "hang")

    ucc_msg = template(ucc["errors"][0]["message"]) if ucc["errors"] else ""
    pred_msg = template(pred.errors[0].message) if pred.errors else ""
    ucc_line = ucc["errors"][0]["line"] if ucc["errors"] else None
    pred_line = pred.errors[0].line if pred.errors else None
    agree = pred.outcome == ucc["outcome"] and ucc_msg == pred_msg and ucc_line == pred_line
    if not agree:
        t.diverge(section, (ucc["outcome"], ucc_msg, pred.outcome, pred_msg), (size, ident))


# ---------------------------------------------------------------- corpora

def engine_root() -> Path | None:
    p = Path(sweeney_config().get("engine_source") or "")
    return p if p.is_dir() else None


def install_root() -> Path | None:
    p = Path(sweeney_config().get("install_root") or "")
    return p if (p / "System").is_dir() else None


def engine_packages() -> list[tuple[str, list[Path]]]:
    root = engine_root()
    if not root:
        return []
    return [(d.name, sorted(d.glob("*.uc"))) for d in sorted(root.iterdir())
            if d.is_dir() and any(d.glob("*.uc"))]


def local_packages() -> list[tuple[str, list[Path]]]:
    """Mod source dirs listed in ~/.sweeney/oracle/corpus-local.txt, one per line
    (each a <Mod> dir holding Classes/, or a Classes dir itself; # comments)."""
    if not LOCAL_CORPUS.exists():
        return []
    out = []
    for line in LOCAL_CORPUS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        d = Path(line).expanduser()
        cls = d / "Classes" if (d / "Classes").is_dir() else d
        files = sorted(cls.glob("*.uc"))
        if files:
            out.append((cls.parent.name if cls.name == "Classes" else d.name, files))
    return out


# ---------------------------------------------------------------- sections

def score_probes(t: Tally) -> None:
    for pid, src, golden in load_probes():
        if golden is None:
            print(f"warning: probe {pid} has no golden; run probes.py", file=sys.stderr)
            continue
        pred = uparse.predict({"Probe": {"Probe.uc": src}})
        compare_outcome(t, "probes", pid, len(src), golden, pred)


def score_corpus(t: Tally, packages, label: str) -> None:
    for pkg, files in packages:
        for f in files:
            diags = uparse.check_file(f.name, f.read_bytes())
            clean = diags == []
            t.add(f"corpus.{label}", clean)
            if not clean:
                first = template(diags[0].message) if diags else ""
                kind = "unknown" if diags is None else "error"
                t.diverge("corpus", (label, kind, first), (f.stat().st_size, f"{pkg}/{f.name}"))


def _norm_field(f: dict, full: bool) -> tuple:
    base = (f["kind"], f["name"].lower())
    if not full:
        return base
    extra = tuple((k, json.dumps(f[k], sort_keys=True)) for k in sorted(f)
                  if k not in ("name", "kind", "fields", "script_size", "rep_offset"))
    sub = tuple(_norm_field(g, True) for g in f.get("fields", []))
    return base + extra + (sub,)


def _norm_class(view: dict, full: bool) -> tuple:
    fields = tuple(sorted(_norm_field(f, full) for f in view.get("fields", [])))
    if not full:
        return fields
    return ((view.get("super") or "").lower(), tuple(sorted(view.get("class_flags", []))),
            (view.get("config") or "").lower(), fields)


def score_decl(t: Tally, packages, label: str) -> None:
    import reflect
    inst = install_root()
    if not inst:
        return
    for pkg, files in packages:
        u = inst / "System" / f"{pkg}.u"
        if not u.exists():
            continue
        rpkg, objects, _ = reflect.load(u)
        compiled = {}
        for path, o in objects.items():
            if o["kind"] == "Class":
                compiled[o["name"].lower()] = reflect.class_view(rpkg, objects, path)
        for f in files:
            want = compiled.get(f.stem.lower())
            if want is None:
                continue                      # source with no compiled class: skip
            got = uparse.declarations(f.name, f.read_bytes())
            for level in ("names", "full"):
                full = level == "full"
                ok = got is not None and _norm_class(got, full) == _norm_class(want, full)
                t.add(f"decl.{label}.{level}", ok)
            if got is None or _norm_class(got, False) != _norm_class(want, False):
                kind = "unknown" if got is None else "mismatch"
                t.diverge("decl", (label, kind), (f.stat().st_size, f"{pkg}/{f.name}"))


def score_seeds(t: Tally) -> None:
    manifest = default_root() / "seeds.json"
    if not manifest.exists():
        return
    from seeds import rename_class, EXEC_LINE
    for it in json.loads(manifest.read_text()):
        if it.get("outcome") not in ("ok", "error", "hang"):
            continue
        src = Path(it["source"])
        if not src.exists():
            continue
        r = rename_class(EXEC_LINE.sub(b"", src.read_bytes()))
        if not r:
            continue
        old, seed_src = r
        ucc = {"outcome": it["outcome"], "errors": [_seed_error(e) for e in it.get("errors", [])]}
        pred = uparse.predict({"Probe": {f"Seed_{old}.uc": seed_src}}, it.get("deps", []))
        compare_outcome(t, "seeds", f"{it['package']}/{src.name}", len(seed_src), ucc, pred)


def _seed_error(s: str) -> dict:
    """seeds.json keeps errors as 'File.uc(N) : message'."""
    m = re.match(r"^(.*?)\((\d+)\) : (.*)$", s)
    if m:
        return {"file": m.group(1), "line": int(m.group(2)), "message": m.group(3)}
    return {"file": "", "line": 0, "message": s}


# ---------------------------------------------------------------- ratchet

def flatten(rates: dict) -> dict[str, float]:
    return {k: v["rate"] for k, v in rates.items() if v["rate"] is not None}


def is_local(metric: str) -> bool:
    return metric.startswith(("seeds.", "hang.seeds.")) or f".{LOCAL}" in metric


def drops(current: dict[str, float], ratchet: dict[str, float]) -> list[str]:
    return [f"{k}: {ratchet[k]:.4f} -> {current[k]:.4f}" for k in sorted(ratchet)
            if k in current and current[k] < ratchet[k] - 1e-9]


def load_ratchet(p: Path) -> dict[str, float]:
    try:
        return json.loads(p.read_text()).get("rates", {})
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated sections")
    ap.add_argument("--clusters", type=int, default=10, help="disagreement clusters to show")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(","))) or set(SECTIONS)

    t = Tally()
    t0 = time.monotonic()
    if "probes" in only or "hang" in only:
        score_probes(t)
    if "corpus" in only:
        score_corpus(t, engine_packages(), "engine")
        score_corpus(t, local_packages(), LOCAL)
    if "decl" in only:
        score_decl(t, engine_packages(), "engine")
        score_decl(t, local_packages(), LOCAL)
    if "seeds" in only or "hang" in only:
        score_seeds(t)
    secs = time.monotonic() - t0

    rates = t.rates()
    print(f"{'metric':28s} {'agree':>7s} {'total':>7s}   rate")
    for m, v in rates.items():
        rate = "   -" if v["rate"] is None else f"{v['rate'] * 100:6.2f}%"
        print(f"{m:28s} {v['agree']:7d} {v['total']:7d} {rate}")
    print(f"({secs:.1f}s)")

    if a.clusters:
        print(f"\nbiggest disagreements (cluster: count, smallest examples):")
        ranked = sorted(t.clusters.items(), key=lambda kv: -len(kv[1]))
        for key, examples in ranked[:a.clusters]:
            smallest = ", ".join(e[1] for e in sorted(examples)[:3])
            print(f"  {len(examples):5d}  {' | '.join(str(k) for k in key)}")
            print(f"         e.g. {smallest}")

    current = flatten(rates)
    repo_now = {k: v for k, v in current.items() if not is_local(k)}
    problems = drops(repo_now, load_ratchet(REPO_SCORE)) + \
        drops(current, load_ratchet(LOCAL_SCORE))
    if problems and (a.check or a.update):
        print("\nREGRESSED below the ratchet:")
        for p in problems:
            print("  " + p)
        return 1
    if a.update:
        if only != set(SECTIONS):
            print("--update needs every section; drop --only", file=sys.stderr)
            return 2
        merged_repo = {**load_ratchet(REPO_SCORE), **repo_now}
        merged_local = {**load_ratchet(LOCAL_SCORE), **current}
        REPO_SCORE.write_text(json.dumps({"rates": merged_repo}, indent=1, sort_keys=True) + "\n")
        LOCAL_SCORE.write_text(json.dumps({"rates": merged_local}, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {REPO_SCORE} and {LOCAL_SCORE}")
    elif a.check:
        print("\nno regressions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
