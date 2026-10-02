"""The compiler's tokenizer: script text into tokens, the way UCC reads it.

Runs on the importer's script text (importer.Imported.script_text()), which is what
UCC's compiler sees, so token lines are the line numbers UCC prints in errors.

Context-free on purpose. UCC's tokenizer takes hints from the parser -- a sign
before a digit starts a number only where an operand is expected, `vect(...)`
becomes a constant, `Type'Name'` an object constant -- and those are left to the
parser. What this layer does reproduce, each pinned by a probe in tests/uparse:

- Block comments nest, and are removed character by character wherever a character
  is read outside a string. So a comment inside a token joins it: `a/**/b` is `ab`.
- `//` comments only start between tokens.
- `*/` outside a comment is an error, even mid-expression (`a*/b`).
- A string runs to the closing quote or the end of the line; a backslash takes the
  next character literally (no \\n translation); an unclosed string is an error.
- A name is letters, digits, underscores and spaces between single quotes.
- A number is a digit followed by any of 0-9 . X A-F (any case). It is a float if it
  holds a '.', hex if it holds an 'X', else an int read by atoi -- so `1e5` is 1.
- Two-character symbols are a fixed list, plus `>>>`.
- `#exec` and `#include` read the rest of the line raw, up to `//` or `/*`.
- `Type'Pkg.Group.Name'` is an object literal. UCC decides that by looking the type
  up as a class; here, a quoted run right after an identifier that is not a valid
  name (it has a '.' or '-') is read as an object path, and anything else stays a
  name for the parser to pair with the identifier (`class'Foo'`, but `case 'Foo':`).
"""

from __future__ import annotations

import dataclasses

NAME_SIZE = 64
MAX_STRING = 1024

PAIRS = {"<<", ">>", "!=", "<=", ">=", "++", "--", "+=", "-=", "*=", "/=", "&&", "||",
         "^^", "==", "**", "~=", "@=", "$="}
RAW_DIRECTIVES = {"exec", "alwaysexec", "forceexec", "include"}
# Statement words a name literal follows. After these a quoted run is a name, never
# an object path, so 'Foo-Bar' fails as UCC fails it (probe lex-name-illegal-char).
NAME_CONTEXT = {"return", "case", "goto"}

IDENT, INT, FLOAT, NAME, STRING, SYMBOL, RAW, OBJECT = (
    "ident", "int", "float", "name", "string", "symbol", "raw", "object")


@dataclasses.dataclass
class Token:
    kind: str
    text: str           # identifier, symbol, or the literal as written (comments removed)
    value: object       # int / float / str value of a literal; the text otherwise
    line: int           # script-text line UCC reports for this token
    start: int          # offset of the token's first character in the script text
    end: int            # offset just past it


class LexError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.message, self.line = message, line


def _is_alpha(c: str) -> bool:
    return ("A" <= c <= "Z") or ("a" <= c <= "z") or c == "_"


def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"


class Lexer:
    """UCC's character reader, with its one-step unget."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.prev_pos = 0
        self.prev_line = 1

    def _at(self, i: int) -> str:
        return self.text[i] if i < len(self.text) else "\0"

    def get_char(self, literal: bool = False) -> str:
        comments = 0
        self.prev_pos, self.prev_line = self.pos, self.line
        while True:
            c = self._at(self.pos)
            self.pos += 1
            if c == "\n":
                self.line += 1
            elif not literal and c == "/" and self._at(self.pos) == "*":
                comments += 1
                self.pos += 1
                continue
            elif not literal and c == "*" and self._at(self.pos) == "/":
                comments -= 1
                if comments < 0:
                    raise LexError("Unexpected '*/' outside of comment", self.line)
                self.pos += 1
                continue
            if comments > 0:
                if c == "\0":
                    raise LexError("End of script encountered inside comment", self.line)
                continue
            return c

    def unget(self) -> None:
        self.pos, self.line = self.prev_pos, self.prev_line

    def peek(self) -> str:
        return self._at(self.pos)

    def leading_char(self) -> str:
        while True:
            c = self.get_char()
            while c in " \t\r\n":
                c = self.get_char()
            if c == "/" and self._at(self.pos) == "/":
                while c not in "\r\n\0":
                    c = self.get_char(True)
                continue
            return c

    def token(self) -> Token | None:
        c = self.leading_char()
        if c == "\0":
            self.unget()
            return None
        start, line = self.prev_pos, self.line
        # The comment skipping may have run past comments before c; the token starts
        # at c itself.
        start = self.pos - 1
        if _is_alpha(c):
            chars = []
            while True:
                chars.append(c)
                if len(chars) > NAME_SIZE:
                    raise LexError(f"Identifer length exceeds maximum of {NAME_SIZE}", self.line)
                c = self.get_char()
                if not (_is_alpha(c) or _is_digit(c)):
                    break
            self.unget()
            s = "".join(chars)
            return Token(IDENT, s, s, line, start, self.pos)
        if _is_digit(c):
            chars, is_float, is_hex = [], False, False
            while True:
                if c == ".":
                    is_float = True
                if c == "X":
                    is_hex = True
                chars.append(c)
                if len(chars) >= NAME_SIZE:
                    raise LexError(f"Number length exceeds maximum of {NAME_SIZE} ", self.line)
                c = self.get_char().upper()
                if not (_is_digit(c) or c in ".X" or "A" <= c <= "F"):
                    break
            self.unget()
            s = "".join(chars)
            if is_float:
                return Token(FLOAT, s, _atof(s), line, start, self.pos)
            return Token(INT, s, _strtoi(s) if is_hex else _atoi(s), line, start, self.pos)
        if c == "'":
            chars = []
            c = self.get_char()
            while _is_alpha(c) or _is_digit(c) or c == " ":
                chars.append(c)
                if len(chars) > NAME_SIZE:
                    raise LexError(f"Name length exceeds maximum of {NAME_SIZE}", self.line)
                c = self.get_char()
            if c != "'":
                raise LexError("Illegal character in name", self.line)
            s = "".join(chars)
            return Token(NAME, s, s, line, start, self.pos)
        if c == '"':
            chars = []
            c = self.get_char(True)
            while c != '"' and c not in "\r\n\0":
                if c == "\\":
                    c = self.get_char(True)
                    if c in "\r\n\0":
                        break
                chars.append(c)
                if len(chars) >= MAX_STRING:
                    raise LexError(f"String constant exceeds maximum of {MAX_STRING} characters",
                                   self.line)
                c = self.get_char(True)
            if c != '"':
                raise LexError("Unterminated string constant", self.line)
            s = "".join(chars)
            return Token(STRING, s, s, line, start, self.pos)
        # Symbol.
        d = self.get_char()
        if c + d in PAIRS:
            s = c + d
            if s == ">>":
                if self.get_char() == ">":
                    s = ">>>"
                else:
                    self.unget()
        else:
            self.unget()
            s = c
        return Token(SYMBOL, s, s, line, start, self.pos)

    def state(self):
        return self.pos, self.line, self.prev_pos, self.prev_line

    def restore(self, s) -> None:
        self.pos, self.line, self.prev_pos, self.prev_line = s

    def object_path(self, type_name: str) -> Token:
        """After an identifier: 'Pkg.Group.Name', parts with dashes allowed."""
        c = self.leading_char()
        line, start = self.line, self.pos - 1
        assert c == "'"
        parts = []
        while True:
            c = self.leading_char()
            if not _is_alpha(c):
                raise LexError(f"Missing {type_name} name", self.line)
            chars = [c]
            while True:
                c = self.get_char()
                if not (_is_alpha(c) or _is_digit(c) or c == "-"):
                    break
                chars.append(c)
            self.unget()
            parts.append("".join(chars))
            c = self.leading_char()
            if c != ".":
                break
        if c != "'":
            raise LexError(f"Missing single quote after {type_name} name", self.line)
        path = ".".join(parts)
        return Token(OBJECT, path, path, line, start, self.pos)

    def raw_line(self) -> Token | None:
        """The rest of the line after a raw directive, trailing blanks trimmed."""
        c = self.leading_char()
        line, start = self.line, self.pos - 1
        chars = []
        while c not in "\r\n\0":
            if c == "/" and self.peek() in "/*":
                break
            chars.append(c)
            c = self.get_char(True)
        self.unget()
        s = "".join(chars).rstrip(" \t")
        return Token(RAW, s, s, line, start, self.pos) if s else None


def tokenize(text: str) -> list[Token]:
    """Every token in the script text. Raises LexError at the first bad one."""
    lx = Lexer(text)
    out: list[Token] = []
    while True:
        saved = lx.state()
        try:
            t = lx.token()
        except LexError as e:
            if not (e.message == "Illegal character in name" and out and out[-1].kind == IDENT
                    and out[-1].text.lower() not in NAME_CONTEXT):
                raise
            lx.restore(saved)
            t = lx.object_path(out[-1].text)
        if t is None:
            return out
        out.append(t)
        if (t.kind == IDENT and len(out) >= 2 and out[-2].kind == SYMBOL
                and out[-2].text == "#" and t.text.lower() in RAW_DIRECTIVES):
            raw = lx.raw_line()
            if raw:
                out.append(raw)


def _atoi(s: str) -> int:
    """C atoi: leading digits only."""
    n = 0
    for ch in s:
        if not _is_digit(ch):
            break
        n = n * 10 + ord(ch) - 48
    return _int32(n)


def _strtoi(s: str) -> int:
    """strtol(s, end, 0) for something holding an X: 0x hex, else decimal/octal."""
    t = s.lower()
    try:
        if t.startswith("0x"):
            digits = ""
            for ch in t[2:]:
                if ch in "0123456789abcdef":
                    digits += ch
                else:
                    break
            return _int32(int(digits, 16)) if digits else 0
    except ValueError:
        pass
    return _atoi(s)


def _atof(s: str) -> float:
    """C atof: the longest leading float, so '1.5F' is 1.5 and '1.5E3' is 1500."""
    import re
    m = re.match(r"\d*\.?\d*(?:[eE][+-]?\d+)?", s)
    try:
        return float(m.group(0)) if m and m.group(0) not in ("", ".") else 0.0
    except ValueError:
        return 0.0


def _int32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


def tokenize_partial(text: str) -> tuple[list[Token], LexError | None]:
    """Tokens up to the first lexer error, and that error (or None). The parser runs
    on what came before: UCC reports whichever problem it reaches first."""
    lx = Lexer(text)
    out: list[Token] = []
    while True:
        saved = lx.state()
        try:
            t = lx.token()
        except LexError as e:
            if not (e.message == "Illegal character in name" and out and out[-1].kind == IDENT
                    and out[-1].text.lower() not in NAME_CONTEXT):
                return out, e
            lx.restore(saved)
            try:
                t = lx.object_path(out[-1].text)
            except LexError as e2:
                return out, e2
        if t is None:
            return out, None
        out.append(t)
        if (t.kind == IDENT and len(out) >= 2 and out[-2].kind == SYMBOL
                and out[-2].text == "#" and t.text.lower() in RAW_DIRECTIVES):
            try:
                raw = lx.raw_line()
            except LexError as e:
                return out, e
            if raw:
                out.append(raw)
