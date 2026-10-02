#!/usr/bin/env python3
"""Lexer coverage check: every corpus file lexes, and its tokens cover the script text.

For each file, the importer's script text is tokenized; it must raise no error (the
corpus all compiles), and the text between tokens must be only whitespace and
comments. That is the lexer's round trip: nothing is skipped, nothing invented.

    lexcheck.py            engine source + ~/.sweeney/oracle/corpus-local.txt
"""
import sys, time, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "scripts"))
sys.path.insert(0, str(HERE))
from uparse.importer import import_class
from uparse.lexer import tokenize, LexError

def strip_comments(s):
    # nested block comments, then line comments -- for checking gaps only
    out, i, depth = [], 0, 0
    while i < len(s):
        if s.startswith("/*", i): depth += 1; i += 2; continue
        if s.startswith("*/", i) and depth: depth -= 1; i += 2; continue
        if depth: i += 1; continue
        if s.startswith("//", i):
            j = s.find("\n", i); i = len(s) if j < 0 else j; continue
        out.append(s[i]); i += 1
    return "".join(out)

from score import engine_packages, local_packages
files = [f for _, fs in engine_packages() + local_packages() for f in fs]
t0 = time.time(); errs = []; gaps = []; ntok = 0; kinds = collections.Counter()
for f in files:
    text = import_class(f.read_bytes()).script_text()
    try:
        toks = tokenize(text)
    except LexError as e:
        errs.append((f, e.line, e.message)); continue
    ntok += len(toks)
    prev = 0
    for t in toks:
        kinds[t.kind] += 1
        gap = text[prev:t.start]
        if strip_comments(gap).strip(" \t\r\n"):
            gaps.append((f.name, t.line, repr(gap[:60]))); break
        prev = t.end
    if strip_comments(text[prev:]).strip(" \t\r\n"):
        gaps.append((f.name, "tail", repr(text[prev:prev+60])))
print(f"{len(files)} files, {ntok} tokens, {time.time()-t0:.1f}s; lex errors {len(errs)}, bad gaps {len(gaps)}")
print(dict(kinds))
for e in errs[:10]: print("ERR", e)
for g in gaps[:10]: print("GAP", g)
sys.exit(1 if errs or gaps else 0)
