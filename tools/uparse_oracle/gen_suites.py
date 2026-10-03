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
  execs    object literals and defaults against what #exec lines make (execs.py).
"""

from __future__ import annotations

import argparse
import json
import random
import struct
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


def _tga(w: int, h: int) -> bytes:
    """An uncompressed 24-bit TGA."""
    head = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0]) + struct.pack("<HH", w, h) + bytes([24, 0])
    return head + bytes([0x40, 0x80, 0xC0]) * (w * h)


def _wav() -> bytes:
    """A tenth of a second of 8-bit mono silence."""
    data = bytes([128]) * 2205
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 22050, 1, 8)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data"
            + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gen_execs(n: int, rng: random.Random) -> list[dict]:
    """Object literals and defaults that find (or miss) what #exec lines made:
    TEXTURE IMPORT, AUDIO IMPORT, the SOUND non-command, OBJ LOAD with and without
    PACKAGE=. Fixed cases; `n` and `rng` are unused. Files are written into the
    package directory, where UCC runs #exec from. Never a texture that isn't a power
    of two: UCC shows a modal dialog for it and waits for a click."""
    tex = {"Textures/a.tga": _tga(16, 16).decode("latin-1")}
    snd = {"Sounds/s.wav": _wav().decode("latin-1")}
    imp = "#exec TEXTURE IMPORT NAME=Foo FILE=Textures\\a.tga GROUP=G MIPS=Off"
    aud = "#exec AUDIO IMPORT FILE=Sounds\\s.wav NAME=Beep GROUP=Snd"
    into = "#exec OBJ LOAD FILE=XGameShaders.utx PACKAGE=Probe"

    def cls(name, execs="", body="O = None;", decls="", defaults=""):
        return (f"class {name} extends Object;\n{execs}\n{decls}"
                f"function F()\n{{\n    local Object O;\n    {body}\n}}\n"
                f"defaultproperties\n{{\n{defaults}}}\n")

    cases = [
        ("tex", cls("Probe", imp, "O = Texture'Foo';"), tex),
        ("tex-qualified", cls("Probe", imp, "O = Texture'Probe.G.Foo';"), tex),
        ("tex-group", cls("Probe", imp, "O = Texture'G.Foo';"), tex),
        ("tex-as-material", cls("Probe", imp, "O = Material'Foo';"), tex),
        ("tex-missing", cls("Probe", imp, "O = Texture'Fooo';"), tex),
        ("tex-wrong-class", cls("Probe", imp, "O = Sound'Foo';"), tex),
        ("tex-default-name", cls("Probe", "#exec TEXTURE IMPORT FILE=Textures\\a.tga",
                                 "O = Texture'a';"), tex),
        ("tex-other-class", cls("Probe", "", "O = Texture'Foo';"),
         {**tex, "Zed.uc": cls("Zed", imp)}),
        ("snd", cls("Probe", aud, "O = Sound'Probe.Snd.Beep';"), snd),
        ("snd-missing", cls("Probe", aud, "O = Sound'Boop';"), snd),
        ("sound-not-a-command", cls("Probe", "#exec SOUND IMPORT NAME=Thaw FILE=Sounds\\s.wav",
                                    "O = Sound'Thaw';"), snd),
        ("sound-not-a-command-builds", cls("Probe", "#exec SOUND IMPORT NAME=Thaw "
                                           "FILE=Sounds\\s.wav"), snd),
        ("load-into", cls("Probe", into, "O = TexPanner'Probe.BRShaders.BombIconRP';"), {}),
        ("load-into-missing", cls("Probe", into, "O = TexPanner'Probe.BRShaders.BombIconXX';"), {}),
        ("load-into-wrong-class", cls("Probe", into, "O = Texture'Probe.BRShaders.BombIconRP';"), {}),
        ("load-whole", cls("Probe", "#exec OBJ LOAD FILE=XGameShaders.utx",
                           "O = TexPanner'XGameShaders.BRShaders.BombIconRP';"), {}),
        ("load-whole-missing", cls("Probe", "#exec OBJ LOAD FILE=XGameShaders.utx",
                                   "O = TexPanner'XGameShaders.BRShaders.BombIconXX';"), {}),
        ("dp-snd", cls("Probe", aud, decls="var Sound S;\n",
                       defaults="    S=Sound'Probe.Snd.Beep'\n"), snd),
        ("dp-snd-missing", cls("Probe", aud, decls="var Sound S;\n",
                               defaults="    S=Sound'Probe.Snd.Boop'\n"), snd),
        ("dp-tex", cls("Probe", imp, decls="var Texture T;\n",
                       defaults="    T=Texture'Probe.G.Foo'\n"), tex),
        # An #exec in an #include file runs like any other.
        ("include-tex", cls("Probe", "#include Classes\\Inc.uci", "O = Texture'Foo';"),
         {**tex, "Classes/Inc.uci": imp + "\n"}),
        ("include-dp-tex", cls("Probe", "#include Classes\\Inc.uci", decls="var Texture T;\n",
                               defaults="    T=Texture'Probe.G.Foo'\n"),
         {**tex, "Classes/Inc.uci": imp + "\n"}),
        ("dp-no-such-group", cls("Probe", imp, decls="var Texture T;\n",
                                 defaults="    T=Texture'Probe.H.Foo'\n"), tex),
    ]
    return [{"id": cid, "src": src, **({"files": files} if files else {})}
            for cid, src, files in cases]


GENERATORS = {"quotes": gen_quotes, "execs": gen_execs}


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
