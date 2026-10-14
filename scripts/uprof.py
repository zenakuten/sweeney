#!/usr/bin/env python3
"""Read a UT2004 script profile (.uprof) and report where script time went.

    uprof.py                                newest .uprof in the install's System/
    uprof.py FILE.uprof                     that capture
    uprof.py --client                       newest in the client install's System/
    uprof.py -m WSUTComp                    only functions whose path matches a regex
    uprof.py --func BS_xPlayer.PlayerTick   one function: its callers and callees
    uprof.py --packages                     self time summed per package
    uprof.py --compare BEFORE.uprof AFTER.uprof
    uprof.py --json                         machine-readable, for your own diffing

A capture comes from the engine console command PROFILESCRIPT: START begins
recording, STOP writes ut2004-<date>_<time>.uprof into the working directory
(System/) of the process that ran it, server or client. RESET throws away what
has been recorded so far without stopping.

What is recorded: every UnrealScript function call, and every event the engine
sends into script (Tick, Timer, Touch ...), with its time. Natives such as
InStr, Mid or VSize do not appear: their time lands in the self time of the
script function that called them. Time outside script (rendering, physics,
net) is not recorded at all.

Columns:
  self      time in the function itself, not in anything it called
  incl      time including everything it called (a recursive call is
            counted once, at its outermost level)
  calls     how many times it was called
  us/call   incl divided by calls
  /frame    per server or client tick, so captures of different length compare

Times are in milliseconds unless the column says otherwise. The first, partial
tick is dropped by the engine; the frame count is ticks after that.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

TOKEN_FN, TOKEN_CYCLES, TOKEN_FRAME = 0, 1, 3


class Profile:
    def __init__(self, path: Path):
        self.path = path
        data = path.read_bytes()
        if len(data) < 12:
            raise SystemExit(f"uprof: {path} is empty; was anything recorded before STOP?")
        (self.us_per_cycle,) = struct.unpack_from("<d", data, 0)
        (count,) = struct.unpack_from("<I", data, 8)
        self.ptr = self._pointer_width(data, count)
        fmt = "<Q" if self.ptr == 8 else "<I"
        off = 12
        self.names: dict[int, str] = {}
        for _ in range(count):
            (p,) = struct.unpack_from(fmt, data, off)
            (n,) = struct.unpack_from(fmt, data, off + self.ptr)
            off += 2 * self.ptr
            self.names[p] = data[off:off + n].decode("latin-1")
            off += n
        body = data[off:]
        body = body[:len(body) - len(body) % self.ptr]
        self.tokens = struct.unpack(f"<{len(body) // self.ptr}{fmt[1]}", body)
        self._walk()

    @staticmethod
    def _pointer_width(data: bytes, count: int) -> int:
        """The file is written with the engine's pointer size: 8 from a 64-bit
        build, 4 from the retail 32-bit one. Pick whichever makes the first
        function table entry a sane name."""
        if count == 0:
            return 8
        for w, fmt in ((8, "<Q"), (4, "<I")):
            try:
                (n,) = struct.unpack_from(fmt, data, 12 + w)
            except struct.error:
                continue
            name = data[12 + 2 * w:12 + 2 * w + n]
            if 0 < n < 1024 and len(name) == n and all(32 < b < 127 for b in name):
                return w
        raise SystemExit("uprof: not a .uprof file (no readable function table)")

    def name(self, p: int) -> str:
        return self.names.get(p) or f"<unloaded 0x{p:x}>"

    def _walk(self) -> None:
        c = self.us_per_cycle / 1000.0  # cycles -> ms
        self.calls: dict[int, int] = defaultdict(int)
        self.incl: dict[int, float] = defaultdict(float)
        self.self_: dict[int, float] = defaultdict(float)
        self.edges: dict[tuple[int, int], list] = defaultdict(lambda: [0, 0.0])  # (caller, callee) -> [calls, incl]
        self.frames = 0
        self.total = 0.0          # top-level script time, ms
        self.broken = 0           # frames that ended with calls still open
        self.first_tick = self.last_tick = None
        stack: list[list] = []    # [fn, child cycles]
        depth: dict[int, int] = defaultdict(int)
        for t in self.tokens:
            kind = t & 3
            if t == 0:
                break
            if kind == TOKEN_FN:
                stack.append([t, 0])
                depth[t] += 1
            elif kind == TOKEN_CYCLES:
                if not stack:
                    continue
                fn, child = stack.pop()
                cyc = t & ~3
                ms = cyc * c
                depth[fn] -= 1
                self.calls[fn] += 1
                self.self_[fn] += max(cyc - child, 0) * c
                if depth[fn] == 0:
                    self.incl[fn] += ms
                if stack:
                    stack[-1][1] += cyc
                    e = self.edges[(stack[-1][0], fn)]
                    e[0] += 1
                    e[1] += ms
                else:
                    self.total += ms
            elif kind == TOKEN_FRAME:
                tick = t >> 2
                if self.first_tick is None:
                    self.first_tick = tick
                self.last_tick = tick
                self.frames += 1
                if stack:
                    self.broken += 1
                    stack.clear()
                    depth.clear()
        # The stream starts on a frame marker, so frames counted = markers - 1.
        self.frames = max(self.frames - 1, 1)

    def rows(self, pattern: re.Pattern | None) -> list[dict]:
        out = []
        for fn, n in self.calls.items():
            name = self.name(fn)
            if pattern and not pattern.search(name):
                continue
            out.append({
                "name": name, "calls": n,
                "self_ms": self.self_[fn], "incl_ms": self.incl[fn],
                "us_per_call": self.incl[fn] * 1000.0 / n,
                "self_ms_per_frame": self.self_[fn] / self.frames,
                "calls_per_frame": n / self.frames,
            })
        return out


def newest(install: Path) -> Path:
    files = sorted((install / "System").glob("*.uprof"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"uprof: no .uprof in {install / 'System'}; run PROFILESCRIPT START, "
                         "then PROFILESCRIPT STOP")
    return files[-1]


def install_root(client: bool) -> Path:
    try:
        cfg = json.loads((Path.home() / ".sweeney" / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    root = cfg.get("client_install_root") if client else None
    root = root or cfg.get("install_root")
    if not root:
        raise SystemExit("uprof: no install configured; pass a .uprof path, or run "
                         "sweeney's scripts/setup.sh")
    return Path(root)


def header(p: Profile) -> str:
    ms_frame = p.total / p.frames
    s = (f"{p.path.name}: {p.frames} frames, {p.total:.1f} ms of script "
         f"({ms_frame:.3f} ms/frame), {len(p.calls)} functions, "
         f"{p.ptr * 8}-bit capture, {p.us_per_cycle:g} us/cycle")
    if p.broken:
        s += (f"\n  warning: {p.broken} frames ended inside a call; their open calls were "
              f"dropped")
    return s


def table(rows: list[dict], key: str, top: int, frames: int) -> str:
    rows = sorted(rows, key=lambda r: r[key], reverse=True)[:top]
    lines = [f"{'self':>9} {'incl':>9} {'calls':>8} {'us/call':>8} {'self/fr':>8} {'calls/fr':>8}  function"]
    for r in rows:
        lines.append(f"{r['self_ms']:9.2f} {r['incl_ms']:9.2f} {r['calls']:8d} "
                     f"{r['us_per_call']:8.2f} {r['self_ms_per_frame']:8.4f} "
                     f"{r['calls_per_frame']:8.2f}  {r['name']}")
    return "\n".join(lines)


def find_fn(p: Profile, query: str) -> list[int]:
    q = query.lower()
    exact = [fn for fn in p.calls if p.name(fn).lower() == q]
    if exact:
        return exact
    return [fn for fn in p.calls if p.name(fn).lower().endswith("." + q) or q in p.name(fn).lower()]


def func_report(p: Profile, query: str, top: int) -> str:
    fns = find_fn(p, query)
    if not fns:
        return f"no recorded function matches {query!r}"
    if len(fns) > 1 and len(fns) > top:
        return f"{len(fns)} functions match {query!r}; be more specific"
    out = []
    for fn in sorted(fns, key=lambda f: p.incl[f], reverse=True):
        n = p.calls[fn]
        out.append(f"\n{p.name(fn)}: {n} calls, self {p.self_[fn]:.2f} ms, "
                   f"incl {p.incl[fn]:.2f} ms, {p.incl[fn] * 1000 / n:.2f} us/call, "
                   f"{n / p.frames:.2f} calls/frame")
        callers = sorted(((k[0], v) for k, v in p.edges.items() if k[1] == fn),
                         key=lambda x: x[1][1], reverse=True)[:top]
        callees = sorted(((k[1], v) for k, v in p.edges.items() if k[0] == fn),
                         key=lambda x: x[1][1], reverse=True)[:top]
        top_level = n - sum(v[0] for k, v in p.edges.items() if k[1] == fn)
        out.append("  called by:")
        if top_level:
            out.append(f"    {top_level:8d} calls  from the engine (event or top-level call)")
        for c, (cn, ms) in callers:
            out.append(f"    {cn:8d} calls  {ms:9.2f} ms  {p.name(c)}")
        out.append("  calls:")
        for c, (cn, ms) in callees:
            out.append(f"    {cn:8d} calls  {ms:9.2f} ms  {p.name(c)}")
        if not callees:
            out.append("    (nothing)")
    return "\n".join(out)


def package_report(p: Profile, top: int) -> str:
    agg: dict[str, list] = defaultdict(lambda: [0.0, 0])
    for fn, n in p.calls.items():
        pkg = p.name(fn).split(".", 1)[0]
        agg[pkg][0] += p.self_[fn]
        agg[pkg][1] += n
    lines = [f"{'self':>9} {'self/fr':>8} {'%':>6} {'calls':>9}  package"]
    for pkg, (ms, n) in sorted(agg.items(), key=lambda x: x[1][0], reverse=True)[:top]:
        pct = 100.0 * ms / p.total if p.total else 0.0
        lines.append(f"{ms:9.2f} {ms / p.frames:8.4f} {pct:6.1f} {n:9d}  {pkg}")
    return "\n".join(lines)


def compare(a: Profile, b: Profile, pattern, top: int) -> str:
    ra = {r["name"]: r for r in a.rows(pattern)}
    rb = {r["name"]: r for r in b.rows(pattern)}
    rows = []
    for name in set(ra) | set(rb):
        x, y = ra.get(name), rb.get(name)
        sa = x["self_ms_per_frame"] if x else 0.0
        sb = y["self_ms_per_frame"] if y else 0.0
        ca = x["calls_per_frame"] if x else 0.0
        cb = y["calls_per_frame"] if y else 0.0
        rows.append((sb - sa, sa, sb, ca, cb, name))
    rows.sort(key=lambda r: abs(r[0]), reverse=True)
    ta, tb = a.total / a.frames, b.total / b.frames
    lines = [f"before: {header(a)}", f"after:  {header(b)}",
             f"script ms/frame {ta:.4f} -> {tb:.4f} ({tb - ta:+.4f})", "",
             "per-frame figures, sorted by the size of the self-time change",
             f"{'self/fr before':>14} {'after':>9} {'change':>9} {'calls/fr before':>15} {'after':>8}  function"]
    for d, sa, sb, ca, cb, name in rows[:top]:
        lines.append(f"{sa:14.4f} {sb:9.4f} {d:+9.4f} {ca:15.2f} {cb:8.2f}  {name}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("file", nargs="?", help=".uprof file (default: newest in the install)")
    ap.add_argument("--client", action="store_true", help="look in the client install")
    ap.add_argument("-m", "--match", help="only functions whose Package.Class.Function matches this regex (case-insensitive)")
    ap.add_argument("-n", "--top", type=int, default=30, help="rows per table (default 30)")
    ap.add_argument("--sort", choices=("self", "incl", "calls"), default="self")
    ap.add_argument("--func", help="callers and callees of one function")
    ap.add_argument("--packages", action="store_true", help="self time per package")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pattern = re.compile(args.match, re.I) if args.match else None
    if args.compare:
        a, b = (Profile(Path(f)) for f in args.compare)
        print(compare(a, b, pattern, args.top))
        return 0

    path = Path(args.file) if args.file else newest(install_root(args.client))
    p = Profile(path)
    if args.json:
        json.dump({"file": str(path), "frames": p.frames, "script_ms": p.total,
                   "us_per_cycle": p.us_per_cycle, "pointer_bytes": p.ptr,
                   "functions": sorted(p.rows(pattern), key=lambda r: r["self_ms"], reverse=True)},
                  sys.stdout, indent=1)
        print()
        return 0
    print(header(p))
    if args.func:
        print(func_report(p, args.func, args.top))
    elif args.packages:
        print(package_report(p, args.top))
    else:
        key = {"self": "self_ms", "incl": "incl_ms", "calls": "calls"}[args.sort]
        print(table(p.rows(pattern), key, args.top, p.frames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
