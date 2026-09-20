#!/usr/bin/env python3
"""Identify a UT2004 install from its System/ directory.

Prints one line: version|ucc|bits|built|commit|size|mtime

    version   3374, 3369, "retail", or "unknown"
    ucc       UCC.exe | native | none
    bits      64 | 32 | ? | -
    built     build date from System/Build.ini, or 0000-00-00 00.00
    commit    build commit from Build.ini, if present
    size      UCC.exe size in bytes, if present
    mtime     UCC.exe mtime, if present

Exits 1 if the path is not a UT2004 install.

This exists so setup.sh does not need file(1) or a GNU stat, neither of which
is present in Git Bash -- which is how most Windows users would run it. The
architecture is read from the executable header directly, which is more
reliable than parsing file(1)'s prose anyway.
"""

import datetime
import re
import sys
import struct
from pathlib import Path


def arch(path: Path) -> str:
    """'64', '32' or '?' from a PE or ELF header."""
    try:
        with path.open("rb") as f:
            head = f.read(2)
            if head == b"MZ":
                # PE: e_lfanew at 0x3c -> "PE\0\0" -> COFF machine at +4
                f.seek(0x3C)
                off = struct.unpack("<I", f.read(4))[0]
                f.seek(off)
                if f.read(4) != b"PE\0\0":
                    return "?"
                machine = struct.unpack("<H", f.read(2))[0]
                return {0x014C: "32", 0x8664: "64", 0xAA64: "64"}.get(machine, "?")
            if head == b"\x7fE":
                f.seek(4)
                return {1: "32", 2: "64"}.get(f.read(1)[0], "?")
    except Exception:
        pass
    return "?"


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    system = root / "System"
    if not (system / "Engine.u").is_file():
        return 1

    version, built, commit = "unknown", "0000-00-00 00.00", ""
    build_ini = system / "Build.ini"
    if build_ini.is_file():
        try:
            text = build_ini.read_text(encoding="latin-1", errors="replace")
            label = next((l[6:].strip() for l in text.splitlines()
                          if l.lower().startswith("label=")), "")
            # UT2004_v3374_[2026-07-18_18.30]_d1b145e1  /  UT2004_Build_[2005-11-23_16.22]
            m = re.search(r"_v(\d{4})_", label)
            version = m.group(1) if m else ("retail" if label.startswith("UT2004_Build_") else "unknown")
            m = re.search(r"\[([\d-]+)_([\d.]+)\]", label)
            if m:
                built = f"{m.group(1)} {m.group(2)}"
            m = re.search(r"\]_(.+)$", label)
            if m:
                commit = m.group(1)
        except Exception:
            pass

    ucc, bits, size, mtime = "none", "-", "", ""
    exe = system / "UCC.exe"
    # A native binary may be ucc-bin, or UCC with ucc-bin symlinked to it.
    native = next((p for p in (system / "ucc-bin", system / "UCC") if p.is_file()), None)
    if exe.is_file():
        ucc, bits = "UCC.exe", arch(exe)
        st = exe.stat()
        size = str(st.st_size)
        mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    elif native is not None:
        ucc, bits = "native", arch(native)

    print("|".join([version, ucc, bits, built, commit, size, mtime]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
