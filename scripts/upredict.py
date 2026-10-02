#!/usr/bin/env python3
"""Predict what `UCC make` will do with a mod, without running UCC.

    upredict.py MyMod                       build MyMod (a dir holding Classes/)
    upredict.py WSUTComp WS3SPN             build both, in that order
    upredict.py WS3SPN --deps WSUTComp      WSUTComp loaded from its compiled .u
    upredict.py MyMod --json

The answer is one of:

  ok       UCC compiles it. Every stage of every class was checked: the importer,
           lexer, declarations, function bodies and defaultproperties.
  error    UCC stops with this error, at this file and line (line 0 for errors
           raised importing defaultproperties, as UCC reports them).
  hang     UCC hangs on "Analyzing..." because of this line.
  unknown  Nothing wrong was found, but something wasn't modelled, so it can't
           promise "ok". The classes involved and the reason are listed.

It is calibrated against UCC itself (see SweeneyParser.md) and has never predicted
the wrong outcome on its test sets. "unknown" is the honest answer when it isn't
sure; build to find out.

Packages listed are built from source in the order given, after the stock
EditPackages and --deps. A dep must be a compiled .u in the install (System/); a
package you are building must not be listed in --deps. Include files (#include) are
read from the package directory.

Exit status: 0 ok, 1 error or hang, 2 unknown.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uparse  # noqa: E402

INCLUDE_SUFFIXES = {".uci", ".uc"}


def package_files(arg: str) -> tuple[str, dict[str, bytes]]:
    """(package name, {file key: bytes}) for a mod dir or its Classes dir. Classes
    are keyed by file name; other files under Classes (includes) by their path
    relative to the package dir, as predict() expects."""
    d = Path(arg).expanduser().resolve()
    if d.name.lower() == "classes":
        d = d.parent
    cls = next((c for c in d.iterdir() if c.is_dir() and c.name.lower() == "classes"), None) \
        if d.is_dir() else None
    if cls is None:
        raise SystemExit(f"upredict: no Classes directory in {d}")
    files: dict[str, bytes] = {}
    for f in sorted(cls.glob("*.uc")):
        files[f.name] = f.read_bytes()
    for f in sorted(cls.rglob("*")):
        if f.is_file() and f.parent != cls and f.suffix.lower() in INCLUDE_SUFFIXES:
            files[f.relative_to(d).as_posix()] = f.read_bytes()
    if not any(k.lower().endswith(".uc") and "/" not in k for k in files):
        raise SystemExit(f"upredict: no .uc files in {cls}")
    return d.name, files


def check_install() -> None:
    """The prediction loads the stock packages from the install, as UCC does.
    Without them every mod would look broken, so refuse rather than guess."""
    try:
        cfg = json.loads((Path.home() / ".sweeney" / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    root = Path(cfg.get("install_root") or "")
    system = root / "System"
    if not (cfg.get("install_root") and (system / "Default.ini").exists()
            and any(system.glob("[Cc]ore.u"))):
        raise SystemExit("upredict: needs a UT2004 install with its compiled packages; "
                         "run sweeney's scripts/setup.sh (sets install_root in "
                         "~/.sweeney/config.json)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("packages", nargs="+", help="mod directories, built in this order")
    ap.add_argument("--deps", default="",
                    help="comma-separated compiled packages loaded first (e.g. WSUTComp)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    check_install()
    packages: dict[str, dict[str, bytes]] = {}
    for p in a.packages:
        name, files = package_files(p)
        packages[name] = files
    deps = [d.strip() for d in a.deps.split(",") if d.strip()]
    clash = [d for d in deps if d.lower() in {p.lower() for p in packages}]
    if clash:
        raise SystemExit(f"upredict: {', '.join(clash)} is both built and a dep")

    pred = uparse.predict(packages, deps)

    if a.json:
        print(json.dumps({
            "outcome": pred.outcome,
            "errors": [{"file": e.file, "line": e.line, "message": e.message}
                       for e in pred.errors],
            "unknown": [{"file": f, "reason": r} for f, r in pred.unknown],
        }, indent=1))
    else:
        n = sum(1 for f in packages.values() for k in f if k.lower().endswith(".uc") and "/" not in k)
        print(f"{pred.outcome}  ({n} classes in {', '.join(packages)})")
        for e in pred.errors:
            print(f"  {e.file}({e.line}) : {e.message}")
        for f, r in pred.unknown:
            print(f"  not checked: {f}: {r}")
    return {"ok": 0, "error": 1, "hang": 1}.get(pred.outcome, 2)


if __name__ == "__main__":
    sys.exit(main())
