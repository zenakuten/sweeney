#!/usr/bin/env python3
"""Run UCC on throwaway packages and report what it did: the parser's oracle.

Each sandbox is a directory that looks like an install to UCC:

    <root>/sb-NN/System/     every file of the install's System/, symlinked, except
                             *.ini (copied, UCC may write them) and *.log (skipped)
    <root>/sb-NN/Textures    and the other content dirs, symlinked
    <root>/sb-NN/<Package>/Classes/*.uc     the probe source, rewritten per run

so nothing is ever written into the real install. A build is
`UCC make -ini=<sandbox make.ini>` where make.ini is the install's UT2004.ini with
EditPackages replaced by the stock list from Default.ini, any dependencies, and the
probe packages.

Outcomes:
    ok      the package was written
    error   UCC reported an error; errors[] has (file, line, message)
    hang    no exit within the time limit, twice (the second try at double the limit)
    failed  UCC exited without a package and without an error line

    sandbox.py init [--n 8]            create sandboxes
    sandbox.py run Probe.uc [...]      build these files as package Probe
    sandbox.py bench [--n 8]           time a batch of builds in parallel
    sandbox.py selftest                known ok / error / hang cases
    sandbox.py clean                   remove the sandboxes

Results are cached in <root>/cache.sqlite, keyed by the sources, the package list
and the UCC binary, so re-asking UCC the same question is free.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

CONTENT_DIRS = ("Textures", "Sounds", "StaticMeshes", "Animations", "Music", "Maps",
                "KarmaData", "Speech")
DEFAULT_TIMEOUT = 30
# Part of every cache key: bump when parse_output changes what a build's output
# means, so results parsed the old way aren't served from the cache.
PARSE_VERSION = 3
IS_WINDOWS = os.name == "nt"

ERROR_RE = re.compile(r"^(?P<file>.*?\.uc)\((?P<line>\d+)\) : (?P<kind>Error|Warning), (?P<msg>.*)$")
SUMMARY_RE = re.compile(r"^(Failure|Success) - \d+ error\(s\), \d+ warning\(s\)$")
STAGE_RE = re.compile(r"^(?:Parsing|Compiling|Importing Defaults for) (\w+)")


def sweeney_config() -> dict:
    try:
        with (Path.home() / ".sweeney" / "config.json").open() as f:
            return json.load(f)
    except Exception:
        return {}


def default_root() -> Path:
    return Path.home() / ".sweeney" / "oracle"


def windows_path(p: Path) -> str:
    """UCC is a Windows program: under wine it needs Z:\\... with backslashes."""
    p = Path(p).resolve()
    if IS_WINDOWS:
        return str(p)
    return "Z:" + str(p).replace("/", "\\")


def _link(src: Path, dst: Path) -> None:
    """Symlink, else hardlink, else copy -- Windows may not permit symlinks."""
    try:
        os.symlink(src, dst, target_is_directory=src.is_dir())
        return
    except OSError:
        pass
    if src.is_dir():
        shutil.copytree(src, dst)
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def stock_packages(install: Path) -> list[str]:
    """EditPackages as shipped, from Default.ini -- UT2004.ini may carry mod entries."""
    out = []
    text = (install / "System" / "Default.ini").read_text(encoding="latin-1")
    for line in text.splitlines():
        if line.strip().lower().startswith("editpackages="):
            out.append(line.split("=", 1)[1].strip())
    return out


def make_ini(install: Path, packages: list[str]) -> str:
    """The install's UT2004.ini with its EditPackages list replaced."""
    lines = (install / "System" / "UT2004.ini").read_text(encoding="latin-1").splitlines()
    out, inserted = [], False
    for line in lines:
        if line.strip().lower().startswith("editpackages="):
            if not inserted:
                out.extend(f"EditPackages={p}" for p in packages)
                inserted = True
            continue
        out.append(line)
    if not inserted:
        raise RuntimeError("UT2004.ini has no EditPackages lines")
    return "\r\n".join(out) + "\r\n"


@dataclasses.dataclass
class Diag:
    file: str       # basename, e.g. Probe.uc
    line: int
    kind: str       # Error | Warning
    message: str


@dataclasses.dataclass
class Result:
    outcome: str                    # ok | error | hang | failed
    errors: list[Diag]
    warnings: list[Diag]
    last_stage: str                 # last "Parsing X" / "Compiling X" seen
    seconds: float
    output: str
    cached: bool = False

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("cached")
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Result":
        d = dict(d)
        d["errors"] = [Diag(**e) for e in d["errors"]]
        d["warnings"] = [Diag(**e) for e in d["warnings"]]
        return cls(**d, cached=True)


PROGRESS = ("Loading ", "Exporting Cache", "Analyzing", "Success -", "Failure -", "Calling ",
            "Warning: ", "Log: ")
FAILURE_RE = re.compile(r"Failure - (\d+) error\(s\)")


def parse_output(text: str, class_names: tuple = ()) -> tuple[list[Diag], list[Diag], str]:
    """UCC's console output into (errors, warnings, last stage). `class_names` are the
    classes being built: a fatal message can be glued onto a progress line
    ("Parsing ProbeCast of NULL to Struct failed"), and knowing the class splits it."""
    errors, warnings, stage = [], [], ""
    stage_class = ""
    unprefixed = ""     # last line that was neither progress nor a File.uc(N) diagnostic
    first_unprefixed = ""
    aborted = False
    failures = 0
    # UCC overwrites progress with bare CRs; treat them as line breaks.
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        # Progress text and a fatal message can share a line: "Analyzing...Superclass X
        # of class Y not found".
        if line.startswith("Analyzing..."):
            line = line[len("Analyzing..."):].strip()
        if not line or "fixme:" in line or line.startswith(("---", "History:")):
            continue
        m = STAGE_RE.match(line)
        if m:
            glued = ""
            for cn in class_names:
                head = line[:m.start(1)] + cn
                if line.startswith(head) and len(line) > len(head) and \
                        m.group(1).lower() != cn.lower():
                    stage, stage_class, glued = head, cn, line[len(head):].strip()
                    break
            else:
                stage, stage_class = line, m.group(1)
            if not glued:
                continue
            line = glued
        if line in ("Compile aborted due to errors.", "Exiting due to error"):
            aborted = True
            continue
        fm = FAILURE_RE.search(line)
        if fm:
            failures = int(fm.group(1))
        m = ERROR_RE.match(line)
        if not m:
            if not SUMMARY_RE.match(line) and not line.startswith(PROGRESS) \
                    and not line.startswith("-"):
                unprefixed = line
                first_unprefixed = first_unprefixed or line
            continue
        msg = m.group("msg")
        if SUMMARY_RE.match(msg):
            continue
        d = Diag(Path(m.group("file").replace("\\", "/")).name, int(m.group("line")),
                 m.group("kind"), msg)
        (errors if d.kind == "Error" else warnings).append(d)
    # Some errors are bare lines with no File.uc(N) prefix: those raised importing
    # defaultproperties ("Foo::ImportText: Bad termination in: ...") and fatal ones
    # that end the run ("Superclass X of class Y not found", then "Exiting due to
    # error"). Line 0 means UCC gave none.
    if aborted and not errors and unprefixed:
        errors.append(Diag(f"{stage_class}.uc" if stage_class else "", 0, "Error", unprefixed))
    elif failures and not errors:
        # Counted but not shown as File.uc(N): the first stray message line, if any
        # ("Bad class definition ..."), else UCC printed no text for it at all.
        errors.append(Diag(f"{stage_class}.uc" if stage_class else "", 0, "Error",
                           first_unprefixed))
    return errors, warnings, stage


class _SandboxLock:
    """An exclusive lock on a sandbox directory, across processes (no-op where
    fcntl is missing, i.e. native Windows)."""

    def __init__(self, path: Path):
        self.file = Path(path) / ".lock"

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            self.fh = None
            return self
        self.fh = open(self.file, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fh:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()


class Sandbox:
    def __init__(self, path: Path, install: Path):
        self.path = Path(path)
        self.install = Path(install)
        self.system = self.path / "System"

    def create(self) -> "Sandbox":
        if self.path.exists():
            shutil.rmtree(self.path)
        self.system.mkdir(parents=True)
        for f in (self.install / "System").iterdir():
            low = f.name.lower()
            if low.endswith(".log"):
                continue
            if low.endswith(".ini"):
                shutil.copy2(f, self.system / f.name)
            else:
                _link(f, self.system / f.name)
        for d in CONTENT_DIRS:
            if (self.install / d).is_dir():
                _link(self.install / d, self.path / d)
        return self

    def exists(self) -> bool:
        return (self.system / "Core.u").exists()

    def _clear_packages(self, names: list[str]) -> None:
        for n in names:
            for ext in (".u", ".ucl"):
                p = self.system / (n + ext)
                if p.is_symlink() or p.exists():
                    p.unlink()
            shutil.rmtree(self.path / n, ignore_errors=True)

    def build(self, packages: dict[str, dict[str, bytes]], deps: list[str],
              timeout: float, keep_u: Path | None = None) -> Result:
        """packages: {PackageName: {"Foo.uc": source bytes}}, built in dict order.
        Holds an exclusive lock on the sandbox, so two processes never share one."""
        with _SandboxLock(self.path):
            return self._build(packages, deps, timeout, keep_u)

    def _build(self, packages, deps, timeout, keep_u) -> Result:
        names = list(packages)
        self._clear_packages(names)
        for name, files in packages.items():
            cls = self.path / name / "Classes"
            cls.mkdir(parents=True)
            for fname, src in files.items():
                (cls / fname).write_bytes(src)
        ini = self.system / "sweeney-make.ini"
        ini.write_text(make_ini(self.install, stock_packages(self.install) + deps + names),
                       encoding="latin-1", newline="")

        cmd = ["UCC.exe" if IS_WINDOWS else "./UCC.exe", "make", f"-ini={windows_path(ini)}"]
        if not IS_WINDOWS:
            cmd.insert(0, "wine")
        env = dict(os.environ, WINEDEBUG="-all")
        t0 = time.monotonic()
        # stdin is empty on purpose: for a native class UCC asks "Do you want to
        # overwrite the existing version? (Y/N)" about its C++ header, and with an
        # open stdin it waits for an answer -- which looks exactly like a hang.
        proc = subprocess.Popen(cmd, cwd=self.system, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=not IS_WINDOWS)
        try:
            out, _ = proc.communicate(timeout=timeout)
            hung = False
        except subprocess.TimeoutExpired:
            if IS_WINDOWS:
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            hung = True
        secs = time.monotonic() - t0
        text = out.decode("latin-1", "replace")
        class_names = tuple(Path(fn).stem for files in packages.values() for fn in files)
        errors, warnings, stage = parse_output(text, class_names)

        built = all((self.system / (n + ".u")).exists() for n in names)
        if hung:
            outcome = "hang"
        elif built and proc.returncode == 0 and not errors:
            outcome = "ok"
        elif errors:
            outcome = "error"
        else:
            outcome = "failed"
        if keep_u and outcome == "ok":
            keep_u.mkdir(parents=True, exist_ok=True)
            for n in names:
                shutil.copy2(self.system / (n + ".u"), keep_u / (n + ".u"))
        return Result(outcome, errors, warnings, stage, round(secs, 3), text)


class Cache:
    def __init__(self, path: Path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, json TEXT)")

    def get(self, key: str) -> Result | None:
        with self.lock:
            row = self.db.execute("SELECT json FROM results WHERE key=?", (key,)).fetchone()
        return Result.from_json(json.loads(row[0])) if row else None

    def put(self, key: str, r: Result) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?)",
                            (key, json.dumps(r.to_json())))
            self.db.commit()


class Oracle:
    """A pool of sandboxes. ask() is thread-safe; ask_many() fans out across the pool."""

    def __init__(self, n: int = 8, root: Path | None = None, install: Path | None = None,
                 timeout: float = DEFAULT_TIMEOUT, use_cache: bool = True):
        cfg = sweeney_config()
        self.root = Path(root or default_root())
        self.install = Path(install or cfg.get("install_root") or "")
        if not (self.install / "System" / "UCC.exe").exists():
            raise SystemExit(f"no System/UCC.exe under install {self.install!s}; "
                             "run sweeney setup or pass --install")
        self.ucc_id = cfg.get("ucc_id") or self._ucc_id()
        self.timeout = timeout
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = Cache(self.root / "cache.sqlite") if use_cache else None
        self.pool: queue.Queue[Sandbox] = queue.Queue()
        for i in range(n):
            sb = Sandbox(self.root / f"sb-{i:02d}", self.install)
            if not sb.exists():
                sb.create()
            self.pool.put(sb)

    def _ucc_id(self) -> str:
        st = (self.install / "System" / "UCC.exe").stat()
        return f"{st.st_size}-{int(st.st_mtime)}"

    def key(self, packages: dict[str, dict[str, bytes]], deps: list[str]) -> str:
        h = hashlib.sha256()
        h.update(f"{self.ucc_id}/parse{PARSE_VERSION}".encode())
        h.update(json.dumps(deps).encode())
        for name, files in packages.items():
            h.update(b"\0P" + name.encode())
            for fname in sorted(files):
                h.update(b"\0F" + fname.encode() + b"\0" + files[fname])
        return h.hexdigest()

    def ask(self, packages: dict[str, dict[str, bytes]] | dict[str, bytes],
            deps: list[str] | None = None, keep_u: Path | None = None,
            retry_hang: bool = True) -> Result:
        """Build and report. A bare {file: bytes} is taken as package Probe."""
        if packages and isinstance(next(iter(packages.values())), (bytes, bytearray)):
            packages = {"Probe": packages}  # type: ignore[dict-item]
        deps = list(deps or [])
        key = self.key(packages, deps)  # type: ignore[arg-type]
        if self.cache and not keep_u:
            hit = self.cache.get(key)
            if hit:
                return hit
        sb = self.pool.get()
        try:
            r = sb.build(packages, deps, self.timeout, keep_u)  # type: ignore[arg-type]
            if r.outcome == "hang" and retry_hang:
                # A loaded machine can stall a build; only a second, longer miss counts.
                r2 = sb.build(packages, deps, self.timeout * 2, keep_u)  # type: ignore[arg-type]
                r = r2 if r2.outcome != "hang" else r
        finally:
            self.pool.put(sb)
        if self.cache and r.outcome != "failed":
            self.cache.put(key, r)
        return r

    def ask_many(self, jobs: list, deps: list[str] | None = None) -> list[Result]:
        results: list[Result | None] = [None] * len(jobs)
        work: queue.Queue = queue.Queue()
        for i, j in enumerate(jobs):
            work.put((i, j))

        def worker():
            while True:
                try:
                    i, j = work.get_nowait()
                except queue.Empty:
                    return
                results[i] = self.ask(j, deps)

        threads = [threading.Thread(target=worker) for _ in range(self.pool.qsize())]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results  # type: ignore[return-value]


# ---------------------------------------------------------------- CLI

BENCH_SRC = "class {name} extends Info;\nvar int Marker{i};\ndefaultproperties\n{{\n}}\n"


# Known answers, measured on a 64-bit 3374 UCC. If these disagree, the harness is at
# fault and no other result means anything.
SELFTEST = [
    ("ok", None,
     b"class Probe extends Info;\nvar int Marker;\ndefaultproperties\n{\n}\n"),
    ("error", (3, "Missing ';' before 'function'"),
     b"class Probe extends Info;\nvar int Marker\nfunction F() {}\ndefaultproperties\n{\n}\n"),
    ("hang", None,
     b"class Probe extends Info;\n// an escaped quote \\\" that nothing closes\n"
     b"var int Marker;\ndefaultproperties\n{\n}\n"),
]


def selftest(o: Oracle) -> int:
    jobs = [{"Probe.uc": src} for _, _, src in SELFTEST]
    bad = 0
    for (want, err, _), r in zip(SELFTEST, o.ask_many(jobs)):
        got_err = (r.errors[0].line, r.errors[0].message) if r.errors else None
        ok = r.outcome == want and (err is None or got_err == err)
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  want {want:5s}  got {summarize(r)}")
    return 1 if bad else 0


def summarize(r: Result) -> str:
    s = f"{r.outcome:6s} {r.seconds:6.2f}s"
    if r.cached:
        s += " (cached)"
    for d in r.errors:
        s += f"\n  {d.file}({d.line}) : Error, {d.message}"
    for d in r.warnings:
        s += f"\n  {d.file}({d.line}) : Warning, {d.message}"
    if r.outcome == "hang" and r.last_stage:
        s += f"\n  last stage: {r.last_stage}"
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, help="sandbox directory (default ~/.sweeney/oracle)")
    ap.add_argument("--install", type=Path, help="UT2004 install (default from config)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init");  p.add_argument("--n", type=int, default=8)
    p = sub.add_parser("run")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--package", default="Probe")
    p.add_argument("--deps", default="", help="comma-separated packages to load first")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--keep-u", type=Path, help="copy the built .u here")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("bench")
    p.add_argument("--n", type=int, default=8)
    p.add_argument("--count", type=int, default=32)
    sub.add_parser("clean")
    sub.add_parser("selftest")
    a = ap.parse_args()

    root = Path(a.root or default_root())
    if a.cmd == "clean":
        for d in sorted(root.glob("sb-*")):
            shutil.rmtree(d)
        print(f"removed sandboxes under {root}")
        return 0
    if a.cmd == "init":
        cfg = sweeney_config()
        install = Path(a.install or cfg.get("install_root") or "")
        for i in range(a.n):
            Sandbox(root / f"sb-{i:02d}", install).create()
        print(f"{a.n} sandboxes under {root}")
        return 0
    if a.cmd == "run":
        o = Oracle(1, root, a.install, a.timeout, use_cache=not a.no_cache)
        files = {f.name: f.read_bytes() for f in a.files}
        deps = [d for d in a.deps.split(",") if d]
        r = o.ask({a.package: files}, deps, a.keep_u)
        print(json.dumps(r.to_json(), indent=1) if a.json else summarize(r))
        return 0 if r.outcome == "ok" else 1
    if a.cmd == "selftest":
        return selftest(Oracle(3, root, a.install, a.timeout, use_cache=False))
    if a.cmd == "bench":
        o = Oracle(a.n, root, a.install, a.timeout, use_cache=False)
        jobs = [{f"Bench{i}.uc": BENCH_SRC.format(name=f"Bench{i}", i=i).encode()}
                for i in range(a.count)]
        jobs = [{f"Bench{i}": j} for i, j in enumerate(jobs)]
        t0 = time.monotonic()
        rs = o.ask_many(jobs)
        wall = time.monotonic() - t0
        outcomes = {}
        for r in rs:
            outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
        mean = sum(r.seconds for r in rs) / len(rs)
        print(f"{len(rs)} builds on {a.n} sandboxes: {wall:.1f}s wall, "
              f"{mean:.2f}s mean per build, {len(rs) / wall * 60:.0f} builds/min")
        print("outcomes:", outcomes)
        return 0 if outcomes.get("ok") == len(rs) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
