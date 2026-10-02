"""Declarations: UCC's first pass, from tokens to the class's fields.

Produces the same shape as reflect.class_view for the compiled class, so the
scoreboard can diff it field by field: the class header, every var / enum / struct /
const / function / state, function parameters and locals, and the flags UCC sets on
each. Function bodies are skipped, except for their `local` declarations, as UCC's
first pass skips them.

It also raises UCC's declaration errors, with UCC's message and line (measured in
tests/uparse/suites/declerrors.jsonl). Errors that need other classes -- unknown
types, overriding a final function, ignoring something that isn't a function --
are left to resolve.py, which knows the token position of each field so the first
error in the file wins, as in UCC.

Facts about what UCC stores, each measured from compiled packages:
- An operator's function is named from its symbol, one word per character (`!`
  Not, `=` Equal, `+` Add, ...), then `Pre` for a preoperator, then its parameter
  types: `!=` on two names is NotEqual_NameName, unary `-` on an int Subtract_PreInt.
- A delegate adds a hidden property __<Name>__Delegate.
- `var()` with no category takes the class (or struct) name as its category.
- The replication block marks replicated vars and functions `net`, and functions
  under `reliable` also `netreliable`.
"""

from __future__ import annotations

from .lexer import Token, IDENT, INT, FLOAT, NAME, STRING, SYMBOL, RAW, OBJECT

OP_CHAR_WORDS = {
    "!": "Not", "=": "Equal", "-": "Subtract", "+": "Add", "*": "Multiply",
    "/": "Divide", "%": "Percent", "<": "Less", ">": "Greater", "~": "Complement",
    "&": "And", "|": "Or", "^": "Xor", "@": "At", "$": "Concat", "#": "Pound",
}

TYPE_KINDS = {
    "byte": "ByteProperty", "int": "IntProperty", "bool": "BoolProperty",
    "float": "FloatProperty", "string": "StrProperty", "name": "NameProperty",
    "pointer": "PointerProperty",
}
# Type words in an operator's generated name.
OP_TYPE_WORDS = {"string": "Str"}

VAR_FLAGS = {
    "const": "const", "config": "config", "globalconfig": "globalconfig",
    "localized": "localized", "transient": "transient", "native": "native",
    "editconst": "editconst", "input": "input", "travel": "travel",
    "export": "exportobject", "noexport": "noexport", "editinline": "editinline",
    "editinlineuse": "editinlineuse", "deprecated": "deprecated",
    "edfindable": "edfindable", "automated": "automated", "cache": "button",
    "editinlinenotify": "editinlinenotify", "editconstarray": "editconstarray",
    "private": None, "protected": None, "public": None,
}
FUNC_FLAGS = {
    "final": "final", "static": "static", "simulated": "simulated",
    "singular": "singular", "latent": "latent", "iterator": "iterator",
    "exec": "exec", "native": "native", "private": "private",
    "protected": "protected", "public": "public", "const": "const",
    "noexport": "noexport", "invariant": "invariant",
}
PARM_FLAGS = {"optional": "optional", "out": "out", "coerce": "coerce", "skip": "skip",
              "const": "const"}
# Every word UCC reads as a modifier somewhere. One that isn't valid where it appears
# is "Specified type modifiers not allowed here".
ALL_MODIFIERS = set(VAR_FLAGS) | set(FUNC_FLAGS) | set(PARM_FLAGS)
LOCAL_FLAGS = {"const"}
CLASS_FLAG_WORDS = {
    "abstract": "abstract", "transient": "transient", "noexport": "noexport",
    "placeable": "placeable", "perobjectconfig": "perobjectconfig",
    "nativereplication": "nativereplication", "editinlinenew": "editinlinenew",
    "collapsecategories": "collapsecategories", "exportstructs": "exportstructs",
    "safereplace": "safereplace", "hidedropdown": "hidedropdown",
    "cacheexempt": "cacheexempt",
}
CLASS_OTHER_WORDS = {"native", "within", "config", "hidecategories", "showcategories",
                     "dependson", "notplaceable", "dontcollapsecategories",
                     "noteditinlinenew", "parseconfig", "instanced", "nativeonly"}
STRUCT_FLAG_WORDS = {"native", "export", "long", "init"}
DECL_KEYWORDS = {"var": "Var", "function": "Function", "event": "Function",
                 "state": "State", "enum": "Enum", "struct": "Struct", "const": "Const",
                 "replication": "Replication", "delegate": "Function",
                 "operator": "Function", "preoperator": "Function", "postoperator": "Function"}
FUNCTION_WORDS = {"function", "event", "delegate", "operator", "preoperator", "postoperator"}
DIRECTIVES = {"exec", "alwaysexec", "forceexec", "include", "call", "alwayscall", "error",
              "linenumber"}
MAX_ARRAY = 2048
MAX_PARMS = 16
MAX_ENUM = 255


class DeclError(Exception):
    def __init__(self, message: str, line: int, pos: int | None = None):
        super().__init__(message)
        self.message, self.line, self.pos = message, line, pos


class Cursor:
    def __init__(self, tokens: list[Token], eof_line: int):
        self.t = tokens
        self.i = 0
        self.eof_line = eof_line
        self.blocks: list[str] = []      # 'Function', 'State': for end-of-script errors

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def eof(self) -> DeclError:
        block = self.blocks[-1] if self.blocks else "Class"
        return DeclError(f"Unexpected end of script in '{block}' block", self.eof_line,
                         len(self.t))

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise self.eof()
        self.i += 1
        return tok

    def at(self, text: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.kind in (IDENT, SYMBOL) and tok.text.lower() == text

    def at_word(self, k: int = 0) -> str | None:
        tok = self.peek(k)
        return tok.text.lower() if tok is not None and tok.kind == IDENT else None

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def error(self, message: str, tok: Token | None = None) -> DeclError:
        tok = tok or self.peek()
        if tok is None:
            return DeclError(message, self.eof_line, len(self.t))
        return DeclError(message, tok.line, self.t.index(tok) if tok in self.t else self.i)

    def missing(self, text: str, where: str = "") -> DeclError:
        """UCC's "Missing ';' before 'X'" (or "Missing ')' in 'X'")."""
        tok = self.peek()
        if tok is None:
            return self.eof()
        if where:
            return self.error(f"Missing '{text}' in '{where}'")
        return self.error(f"Missing '{text}' before '{tok.text}'")

    def expect(self, text: str, where: str = "") -> Token:
        if not self.at(text):
            raise self.missing(text, where)
        return self.next()

    def ident(self, what: str = "identifier") -> Token:
        tok = self.peek()
        if tok is None:
            raise self.eof()
        if tok.kind != IDENT:
            raise self.error(f"Missing {what}")
        return self.next()

    def skip_group(self, open_: str, close: str) -> list[Token]:
        """From an opening bracket to its match; returns the tokens inside."""
        self.expect(open_)
        depth, start = 1, self.i
        while True:
            tok = self.next()
            if tok.kind == SYMBOL and tok.text == open_:
                depth += 1
            elif tok.kind == SYMBOL and tok.text == close:
                depth -= 1
                if depth == 0:
                    return self.t[start:self.i - 1]


def _field(name: str, kind: str, **kw) -> dict:
    f = {"name": name, "kind": kind}
    f.update({k: v for k, v in kw.items() if v not in (None, [], "")})
    return f


def _mark(f: dict, tok: Token, pos: int) -> dict:
    f["_line"], f["_tpos"] = tok.line, pos
    return f


class DeclParser:
    def __init__(self, tokens: list[Token], class_name_hint: str = "", eof_line: int = 1):
        self.c = Cursor(tokens, eof_line)
        self.cls = class_name_hint
        self.fields: list[dict] = []
        self.header: dict = {}
        self.replicated: dict[str, bool] = {}    # name -> reliable
        self.replicated_at: list[tuple[str, int, int]] = []   # (name, line, pos)
        self.rep_conditions: list[tuple[int, int]] = []
        self.rep_statements: list[dict] = []
        self.error: DeclError | None = None
        self.missing_includes: set = set()

    # ------------------------------------------------------------ top level

    def parse(self) -> dict:
        c = self.c
        try:
            while c.peek() is not None:
                mark = len(self.fields)
                self._parse_one()
                for f in self.fields[mark:]:
                    f["_pos"] = c.i            # source order: what UCC had parsed by then
        except DeclError as e:
            self.error = e
        self._apply_replication()
        return {"name": self.header.get("name", self.cls), "super": self.header.get("super"),
                "class_flags": self.header.get("class_flags", []),
                "config": self.header.get("config"), "within": self.header.get("within"),
                "fields": self.fields, "_replicated_at": self.replicated_at,
                "_rep_conditions": self.rep_conditions,
                "_rep_statements": self.rep_statements,
                "_header_line": self.header.get("_line")}

    def _parse_one(self) -> None:
        c = self.c
        w = c.at_word()
        if c.at("#"):
            self._directive()
            return
        if not self.header:
            if w == "class":
                self._class_header()
                return
            if w in DECL_KEYWORDS:
                raise c.error(f"'{DECL_KEYWORDS[w]}' is not allowed before the Class definition")
        if w == "var":
            self._var(self.fields, owner=self.cls)
        elif w == "local":
            raise c.error("Local variables are only allowed in functions")
        elif w == "enum":
            self._check_dup(self._enum(), "Enum", "enum")
            c.expect(";")
        elif w == "struct":
            self._check_dup(self._struct(), "Struct", "struct")
            c.expect(";")
        elif w == "const":
            k = self._const()
            if any(g["kind"] == "Const" and g["name"].lower() == k["name"].lower() for g in self.fields):
                k["_duplicate"] = True       # UCC crashes on this (resolve.py words it)
            self.fields.append(k)
        elif w == "replication":
            self._replication()
        elif c.at(";"):
            raise c.error("Unexpected ';'")
        elif self._state_ahead():
            st = self._state()
            for f in self.fields:
                if f["kind"] == "State" and f["name"].lower() == st["name"].lower():
                    raise DeclError(f"Duplicate state '{st['name']}'", st["_line"], st["_tpos"])
            self.fields.append(st)
        else:
            self.fields.extend(self._function(in_state=False))

    def _check_dup(self, f: dict, kind: str, word: str) -> None:
        for g in self.fields:
            if g["kind"] == kind and g["name"].lower() == f["name"].lower():
                raise DeclError(f"{word}: '{f['name']}' already defined here", f["_line"], f["_tpos"])
        self.fields.append(f)

    def _directive(self) -> None:
        c = self.c
        hash_tok = c.next()                       # '#'
        tok = c.peek()
        if tok is None or tok.kind != IDENT:
            raise c.error("Missing compiler directive after '#'")
        c.next()
        w = tok.text.lower()
        if w == "error":
            raise c.error("#Error directive encountered", tok)
        if w not in DIRECTIVES:
            # Measured: reported on the line after the '#'.
            raise DeclError(f"Unrecognized compiler directive {tok.text}", hash_tok.line + 1, c.i - 1)
        if c.peek() is not None and c.peek().kind == RAW:
            raw = c.next()
            if w == "include" and raw.text.strip().lower() in self.missing_includes:
                raise DeclError(f"include file {raw.text.strip()} not found", raw.line, c.i - 1)

    def _class_header(self) -> None:
        c = self.c
        kw = c.next()                             # class
        name = c.ident("class name").text
        self.cls = name
        flags: list[str] = []
        sup, within, config = None, None, None
        native = False
        noexport_tok = None
        if c.accept("extends"):
            sup = self._dotted()
        while not c.at(";"):
            word = c.peek()
            if word is None:
                raise c.eof()
            w = word.text.lower() if word.kind == IDENT else None
            if w is None or (w not in CLASS_FLAG_WORDS and w not in CLASS_OTHER_WORDS
                             and w != "localized"):
                raise c.missing(";", "Class")
            c.next()
            if w == "localized":
                raise c.error("Class 'localized' keyword is no longer required", word)
            if w == "within":
                if c.peek() is None or c.peek().kind != IDENT:
                    raise c.error("'within': Missing class name")
                within = self._dotted()
            elif w == "config":
                # Names the ini file only; the class flag means "has config vars".
                if c.at("("):
                    c.next()
                    if c.at(")"):
                        raise c.error("config: Missing configuration name", word)
                    config = c.next().text
                    if not c.accept(")"):
                        raise DeclError("Missing ')' in config", word.line, c.i)
            elif w in ("hidecategories", "showcategories", "dependson"):
                if c.at("("):
                    inner = c.skip_group("(", ")")
                    if not inner and w != "dependson":
                        label = "HideCategories" if w == "hidecategories" else "ShowCategories"
                        raise c.error(f"{label}: Expected category name", word)
            elif w in CLASS_FLAG_WORDS:
                flags.append(CLASS_FLAG_WORDS[w])
                if w == "noexport":
                    noexport_tok = word
            elif w == "instanced":
                flags += ["editinlinenew", "instanced"]
            elif w == "native":
                flags.append("_native")
                native = True
            elif w in ("notplaceable", "noteditinlinenew", "dontcollapsecategories"):
                flags.append("_" + w)
            if c.at("("):
                c.skip_group("(", ")")
        if noexport_tok is not None and not native:
            raise c.error("'noexport': Only valid for native classes", noexport_tok)
        c.expect(";")
        self.header = {"name": name, "super": sup, "class_flags": flags,
                       "within": within, "config": config, "_line": kw.line}

    def _dotted(self) -> str:
        parts = [self.c.ident().text]
        while self.c.at(".") and self.c.peek(1) is not None and self.c.peek(1).kind == IDENT:
            self.c.next()
            parts.append(self.c.ident().text)
        return ".".join(parts)

    # ------------------------------------------------------------ types

    def _type(self, owner_fields: list[dict], thing: str = "Variable declaration",
              allow_inline: bool = True) -> dict:
        """A type: returns {"kind", plus type details}. Inline enum/struct declarations
        are appended to owner_fields, as UCC adds them to the enclosing scope."""
        c = self.c
        tok = c.peek()
        if tok is None:
            raise c.eof()
        if tok.kind != IDENT:
            raise c.error(f"{thing}: Missing variable type")
        pos = c.i
        c.next()
        w = tok.text.lower()
        base = {"_line": tok.line, "_tpos": pos}
        if w in TYPE_KINDS:
            if w == "string" and c.at("["):
                c.skip_group("[", "]")             # obsolete string size: accepted
            return {**base, "kind": TYPE_KINDS[w], "word": tok.text}
        if w == "class":
            meta = None
            if c.accept("<"):
                if c.at(">"):
                    raise c.error("'class': Missing class limitor")
                meta_tok = c.peek()
                meta = self._dotted()
                base["_meta_line"], base["_meta_tpos"] = meta_tok.line, self.c.t.index(meta_tok)
                c.expect(">")
            return {**base, "kind": "ClassProperty", "word": "class", "meta_class": meta or "Object"}
        if w == "array":
            c.expect("<")
            if c.at_word() == "array":
                raise c.error("Arrays within arrays not supported")
            inner = self._type(owner_fields, thing)
            if c.at(">>"):                         # array<class<X>> lexes '>>'
                c.next()
            else:
                c.expect(">")
            return {**base, "kind": "ArrayProperty", "word": "array", "inner_type": inner}
        if w == "map":
            raise c.error("Map are not supported in UnrealScript yet", tok)
        if w == "enum":
            if not allow_inline:
                raise c.error("Enums can only be declared in class or struct scope", tok)
            c.i -= 1
            e = self._enum()
            owner_fields.append(e)
            return {**base, "kind": "ByteProperty", "word": e["name"], "enum": e["name"]}
        if w == "struct":
            if not allow_inline:
                raise c.error("Enums can only be declared in class or struct scope", tok)
            c.i -= 1
            s = self._struct()
            owner_fields.append(s)
            return {**base, "kind": "StructProperty", "word": s["name"], "type": s["name"]}
        if w == "delegate":
            c.expect("<")
            fn = self._dotted()
            c.expect(">")
            return {**base, "kind": "DelegateProperty", "word": "delegate", "type": fn}
        name = tok.text
        while c.at(".") and c.peek(1) is not None and c.peek(1).kind == IDENT:
            c.next()
            name += "." + c.next().text
        # Object, struct or enum: which one needs the type's declaration (resolve.py).
        return {**base, "kind": "UnresolvedProperty", "word": name, "type": name}

    # ------------------------------------------------------------ var

    def _var(self, out: list[dict], owner: str, in_struct: bool = False) -> None:
        c = self.c
        c.next()                                   # var
        flags: list[str] = []
        category = "None"
        if c.at("("):
            c.next()
            cat = None
            if c.peek() is not None and c.peek().kind == IDENT:
                cat = c.next().text
            if not c.accept(")"):
                raise c.error("Missing ')' after editable category")
            flags.append("edit")
            category = cat or owner
        while True:
            w = c.at_word()
            if w in VAR_FLAGS:
                f = VAR_FLAGS[c.next().text.lower()]
                if f and f not in flags:
                    flags.append(f)
            elif w in ALL_MODIFIERS and not (c.peek(1) is not None and c.peek(1).kind == SYMBOL):
                raise c.error("Specified type modifiers not allowed here")
            else:
                break
        explicit = set(flags)
        if "globalconfig" in flags and "config" not in flags:
            flags.append("config")
        if "automated" in flags:
            # Measured: automated implies these (1,275 fields in the engine source).
            flags += [x for x in ("edit", "editinlinenotify") if x not in flags]
            if category == "None":
                category = owner
        if ("editinlineuse" in flags or "editinlinenotify" in flags) and "editinline" not in flags:
            flags.append("editinline")
        if "editinline" in explicit and "exportobject" in explicit and "needctorlink" not in flags:
            # Measured on one case (Actor.KParams, `export editinline`): both
            # keywords spelled out add needctorlink.
            flags.append("needctorlink")
        typ = self._type(out)
        self._declare_names(out, typ, flags, category, "Variable declaration")
        if in_struct and not c.at(";"):
            raise c.missing(";", "struct")
        c.expect(";")

    def _declare_names(self, out: list[dict], typ: dict, flags: list[str], category: str,
                       thing: str) -> None:
        c = self.c
        while True:
            tok = c.peek()
            if tok is None:
                raise c.eof()
            if tok.kind != IDENT:
                raise c.error("Missing variable name")
            pos = c.i
            c.next()
            for g in out:
                if g["name"].lower() == tok.text.lower() and g["kind"].endswith("Property"):
                    raise DeclError(f"{thing}: '{tok.text}' already defined", tok.line, pos)
            dim = self._array_dim(tok.text, thing)
            if typ["kind"] == "BoolProperty" and dim != 1:
                raise c.error("Bool arrays are not allowed")
            out.append(_mark(self._property(tok.text, typ, flags, category, dim), tok, pos))
            if not c.accept(","):
                break

    def _array_dim(self, name: str = "", thing: str = "Variable declaration"):
        c = self.c
        if c.at("("):
            raise c.error("Use [] for arrays, not ()")
        if not c.at("["):
            return 1
        c.next()
        if c.accept("]"):
            return 1
        tok = c.next()
        if tok.kind == SYMBOL and tok.text == "-" and c.peek() is not None and c.peek().kind == INT:
            tok = c.next()
            dim = -tok.value
        elif tok.kind == INT:
            dim = tok.value
        elif tok.kind == IDENT:
            dim = {"const": [tok.text], "_line": tok.line}     # resolved later
        else:
            dim = 0
        if not c.accept("]"):
            raise c.error(f"{thing} {name}: Missing ']'")
        if isinstance(dim, int) and not (1 <= dim <= MAX_ARRAY):
            raise c.error(f"{thing} {name}: Illegal array size {dim}", tok)
        if isinstance(dim, dict):
            dim["_thing"] = f"{thing} {name}"
        return dim

    def _property(self, name: str, typ: dict, flags: list[str], category: str, dim) -> dict:
        kind = typ["kind"]
        flags = list(flags)
        if kind in ("StrProperty", "ArrayProperty", "DelegateProperty") and "native" not in flags:
            flags.append("needctorlink")
        if "automated" in flags and "needctorlink" not in flags:
            flags.append("needctorlink")
        if kind == "PointerProperty" and "transient" not in flags:
            # Measured (3369 packages): a pointer is always stored transient.
            flags.append("transient")
        flags = list(dict.fromkeys(flags))
        f = _field(name, kind, flags=flags, array_dim=dim, category=category)
        for k in ("enum", "type", "meta_class"):
            if k in typ:
                f[k] = typ[k]
        if kind == "ClassProperty":
            f["type"] = "Class"
        if kind == "ArrayProperty":
            f["inner_type"] = typ["inner_type"]
        f["_word"] = typ["word"]
        f["_type"] = typ
        return f

    # ------------------------------------------------------------ enum, struct, const

    def _enum(self) -> dict:
        c = self.c
        c.next()                                   # enum
        tok = c.peek()
        if tok is None or tok.kind != IDENT:
            raise c.error("Missing enumeration name")
        pos = c.i
        c.next()
        c.expect("{")
        values: list[str] = []
        while not c.at("}"):
            v = c.peek()
            if v is None:
                raise c.eof()
            if v.kind != IDENT:
                raise c.missing("}")
            c.next()
            if v.text.lower() in (x.lower() for x in values):
                raise c.error(f"Duplicate enumeration tag {v.text}", v)
            values.append(v.text)
            if len(values) > MAX_ENUM:
                raise c.error(f"Exceeded maximum of {MAX_ENUM} enumerators", v)
            if not c.accept(","):
                break
        c.expect("}")
        if not values:
            raise c.error("Enumeration must contain at least one enumerator", tok)
        return _mark(_field(tok.text, "Enum", values=values), tok, pos)

    def _struct(self) -> dict:
        c = self.c
        c.next()                                   # struct
        flags = []
        while c.at_word() in STRUCT_FLAG_WORDS and c.peek(1) is not None and c.peek(1).kind == IDENT:
            flags.append(c.next().text.lower())
        tok = c.peek()
        if tok is None or tok.kind != IDENT:
            raise c.error("Missing struct name")
        pos = c.i
        name = c.next().text
        sup = None
        sup_tok = None
        if c.accept("extends"):
            sup_tok = c.peek()
            sup = self._dotted()
        c.expect("{")
        members: list[dict] = []
        while not c.at("}"):
            w = c.at_word()
            if c.peek() is None:
                raise c.eof()
            if w == "var":
                self._var(members, owner=name, in_struct=True)
            elif w == "enum":
                members.append(self._enum())
                c.expect(";")
            elif w == "struct":
                members.append(self._struct())
                c.expect(";")
            elif w == "const":
                members.append(self._const())
            elif c.at(";"):
                c.next()
            elif w in ("structcpptext", "cpptext", "cppstruct"):
                c.next()
                c.skip_group("{", "}")
            else:
                raise c.error(f"'struct': Expecting 'Var', got '{c.peek().text}'")
        c.expect("}")
        f = _field(name, "Struct", struct_flags=flags, super=sup, fields=members)
        if sup_tok is not None:
            f["_super_line"] = sup_tok.line
        return _mark(f, tok, pos)

    def _const(self) -> dict:
        c = self.c
        c.next()                                   # const
        tok = c.peek()
        if tok is None or tok.kind != IDENT:
            raise c.error("Missing constant name")
        pos = c.i
        c.next()
        if not c.at("="):
            raise c.error("Missing '=' in 'const'")
        eq = c.next()
        val = c.peek()
        if val is None or (val.kind == SYMBOL and val.text == ";"):
            raise c.error(f"const {tok.text}: Value is not constant")
        c.next()
        if val.kind == SYMBOL and val.text == "-" and c.peek() is not None and \
                c.peek().kind in (INT, FLOAT):
            c.next()
        semi = c.expect(";")
        f = _field(tok.text, "Const", value=None, _const_span=(eq.end, semi.start),
                   _const_tokens=[t.text for t in c.t[c.t.index(eq) + 1:c.i - 1]])
        return _mark(f, tok, pos)

    # ------------------------------------------------------------ replication

    def _replication(self) -> None:
        c = self.c
        c.next()                                   # replication
        c.expect("{")
        while not c.at("}"):
            if c.accept("reliable"):
                reliable = True
            elif c.accept("unreliable"):
                reliable = False
            else:
                raise c.error("Missing 'Reliable' or 'Unreliable'")
            c.expect("if")
            open_at = c.i
            c.skip_group("(", ")")
            self.rep_conditions.append((open_at + 1, c.i))   # after '(' .. through ')'
            names = []
            while True:
                tok = c.ident("variable name")
                self.replicated[tok.text.lower()] = reliable
                self.replicated_at.append((tok.text, tok.line, c.i - 1))
                names.append((tok.text, tok.line, c.i - 1))
                if not c.accept(","):
                    break
            self.rep_statements.append({"cond": (open_at + 1, c.i - len(names) * 2 + 1),
                                        "names": names, "reliable": reliable})
            c.expect(";")
        c.expect("}")

    def _apply_replication(self) -> None:
        for f in self.fields:
            rel = self.replicated.get(f["name"].lower())
            if rel is None:
                continue
            flags = f.setdefault("flags", [])
            if f["kind"] == "Function":
                flags.append("net")
                if rel:
                    flags.append("netreliable")
            else:
                flags.append("net")

    # ------------------------------------------------------------ states

    def _state_ahead(self) -> bool:
        k = 0
        while self.c.at_word(k) in ("auto", "simulated"):
            k += 1
        return self.c.at("state", k)

    def _state(self) -> dict:
        c = self.c
        flags = []
        while not c.at("state"):
            flags.append(c.next().text.lower())
        c.next()                                   # state
        if c.at("("):
            c.skip_group("(", ")")
            flags.append("editable")
        tok = c.peek()
        if tok is None or tok.kind != IDENT:
            raise c.error("Missing state name")
        pos = c.i
        name = c.next().text
        sup, sup_tok = None, None
        if c.accept("extends"):
            sup_tok = c.peek()
            if sup_tok is None or sup_tok.kind != IDENT:
                raise c.error("Missing parent state name")
            sup = c.next().text
        c.expect("{")
        c.blocks.append("State")
        fields: list[dict] = []
        state_code = None
        while not c.at("}"):
            w = c.at_word()
            if c.peek() is None:
                raise c.eof()
            if w == "ignores":
                # Each ignored function becomes an empty stub in the state.
                c.next()
                while True:
                    t = c.peek()
                    if t is None:
                        raise c.eof()
                    if t.kind != IDENT:
                        raise c.error(f"'Ignores': '{t.text}' is not a function")
                    c.next()
                    stub = _field(t.text, "Function", flags=["public"])
                    stub["native"], stub["precedence"], stub["fields"] = 0, 0, []
                    stub["_ignored"] = True
                    fields.append(_mark(stub, t, c.i - 1))
                    if not c.accept(","):
                        break
                c.expect(";")
            elif c.at(";"):
                c.next()
            elif w in ("var", "replication", "state", "struct", "enum", "const"):
                raise c.error(f"'{DECL_KEYWORDS[w]}' is not allowed here")
            elif self._label_ahead():
                # State code: labels and statements to the end of the state.
                code_start = c.i
                self._skip_state_code()
                state_code = (code_start, c.i)
                break
            else:
                fns = self._function(in_state=True)
                fn = fns[0]
                if any(g["kind"] == "Function" and not g.get("_ignored") and
                       g["name"].lower() == fn["name"].lower() for g in fields):
                    fn["_conflict_state"] = name
                fields.extend(fns)
        c.expect("}")
        c.blocks.pop()
        sflags = [f for f in ("editable", "auto", "simulated") if f in flags]
        st = _field(name, "State", state_flags=sflags, super=sup, fields=fields)
        if state_code is not None:
            st["_code"] = state_code
        if sup_tok is not None:
            st["_super_line"], st["_super_tpos"] = sup_tok.line, c.t.index(sup_tok)
        return _mark(st, tok, pos)

    def _label_ahead(self) -> bool:
        tok, nxt = self.c.peek(), self.c.peek(1)
        return tok is not None and tok.kind == IDENT and nxt is not None and \
            nxt.kind == SYMBOL and nxt.text == ":"

    def _skip_state_code(self) -> None:
        c = self.c
        depth = 0
        while True:
            tok = c.peek()
            if tok is None:
                raise c.eof()
            if tok.kind == SYMBOL and tok.text == "{":
                depth += 1
            elif tok.kind == SYMBOL and tok.text == "}":
                if depth == 0:
                    return
                depth -= 1
            c.next()

    # ------------------------------------------------------------ functions

    def _function(self, in_state: bool) -> list[dict]:
        c = self.c
        flags: list[str] = []
        native_index = 0
        numbered_native = False
        kind_word = None
        precedence = 0
        start_tok = c.peek()
        while True:
            tok = c.peek()
            if tok is None:
                raise c.eof()
            w = tok.text.lower() if tok.kind == IDENT else None
            if w in FUNCTION_WORDS:
                kind_word = w
                c.next()
                if w == "operator":
                    if not c.at("("):
                        raise c.error("Missing '(' and precedence after 'Operator'")
                    inner = c.skip_group("(", ")")
                    precedence = inner[0].value if inner and inner[0].kind == INT else 0
                break
            if w in FUNC_FLAGS:
                c.next()
                if w == "native" and c.at("("):
                    inner = c.skip_group("(", ")")
                    native_index = inner[0].value if inner and inner[0].kind == INT else 0
                    numbered_native = True
                if FUNC_FLAGS[w] and FUNC_FLAGS[w] not in flags:
                    flags.append(FUNC_FLAGS[w])
                continue
            if flags:
                raise c.error("Missing 'function'")
            raise c.error(f"Unexpected '{tok.text}'")

        # Modifiers may also follow the keyword: `function static int F()`.
        while c.at_word() in FUNC_FLAGS and \
                not (c.peek(1) is not None and c.peek(1).kind == SYMBOL and c.peek(1).text == "("):
            w = c.next().text.lower()
            if w == "native" and c.at("("):
                inner = c.skip_group("(", ")")
                native_index = inner[0].value if inner and inner[0].kind == INT else 0
                numbered_native = True
            if FUNC_FLAGS[w] and FUNC_FLAGS[w] not in flags:
                flags.append(FUNC_FLAGS[w])

        is_op = kind_word in ("operator", "preoperator", "postoperator")
        extra: list[dict] = []
        ret = None
        if c.at("("):
            raise c.error("Missing function name")
        if not (c.peek(1) is not None and c.peek(1).kind == SYMBOL and c.peek(1).text == "("):
            ret = self._type(extra, "Function return type")
            # An unknown return type: UCC reads the word as the function's name and
            # then fails on what follows ("Bad function definition"); resolve.py checks.
            ret["_bad_def"] = f"Bad {kind_word} definition"
        name_tok = c.peek()
        if name_tok is None:
            raise c.eof()
        name_pos = c.i
        c.next()
        name = name_tok.text
        parms: list[dict] = []
        if not c.at("("):
            raise c.error(f"Bad {kind_word} definition", name_tok)
        c.expect("(")
        saw_optional = False
        while not c.at(")"):
            if c.peek() is None:
                raise c.eof()
            pflags = ["parm"]
            while c.at_word() in PARM_FLAGS:
                pflags.append(PARM_FLAGS[c.next().text.lower()])
            if "optional" in pflags:
                saw_optional = True
            elif saw_optional:
                raise c.error("After an optional parameters, all other parmeters must be optional")
            ptype = self._type(extra, "Function parameter", allow_inline=False)
            if ptype["kind"] == "BoolProperty" and "out" in pflags:
                raise c.error("Booleans may not be out parameters")
            ptok = c.peek()
            if ptok is None:
                raise c.eof()
            if ptok.kind != IDENT:
                raise c.error("Missing variable name")
            ppos = c.i
            c.next()
            if any(p["name"].lower() == ptok.text.lower() for p in parms):
                raise DeclError(f"Function parameter: '{ptok.text}' already defined", ptok.line, ppos)
            dim = self._array_dim(ptok.text, "Function parameter")
            parms.append(_mark(self._property(ptok.text, ptype, pflags, "None", dim), ptok, ppos))
            if len(parms) > MAX_PARMS:
                raise c.error(f"'{name}': too many parameters", ptok)
            if not c.accept(","):
                if not c.at(")"):
                    raise c.error("Missing ')' in parameter list")
                break
        c.expect(")")
        while c.at_word() == "const":
            c.next()
            flags.append("const")

        if is_op:
            if "final" not in flags:
                raise c.error("Operators must be declared as 'Final'", name_tok)
            want = 2 if kind_word == "operator" else 1
            if len(parms) != want or (ret is None and kind_word == "operator"):
                raise c.error(f"{kind_word} must have {want} parameters", name_tok)
        if "native" not in flags:
            if "iterator" in flags:
                raise c.error("Only native functions may use 'Iterator'", name_tok)
            if "latent" in flags:
                raise c.error("Only native functions may use 'Latent'", name_tok)
        if numbered_native and "final" not in flags and native_index:
            raise c.error("Numbered native functions must be final", name_tok)
        if in_state and "static" in flags:
            raise c.error("Static functions cannot exist in a state", start_tok)

        locals_: list[dict] = []
        defined = False
        body_span = None
        # The override checks run once '{' is read, or after the last token before ';'.
        check_line = c.peek().line if c.at("{") else c.t[c.i - 1].line
        if c.at("{"):
            if "native" in flags:
                raise c.error("Native functions may only be declared, not defined")
            defined = True
            c.blocks.append("Function")
            body_start = c.i + 1
            locals_ = self._body_locals(extra, parms)
            body_span = (body_start, c.i - 1)       # tokens between the braces
            c.blocks.pop()
        else:
            c.expect(";")

        if is_op:
            flags.append("operator")
            if kind_word == "preoperator":
                flags.append("preoperator")
            name = self._operator_name(name, kind_word, parms)
        if kind_word == "event":
            flags.append("event")
        if kind_word == "delegate":
            flags.append("delegate")
        if defined:
            flags.append("defined")
        if not ({"private", "protected"} & set(flags)):
            flags.append("public")

        fields = list(parms)
        if ret is not None:
            fields.append(self._property("ReturnValue", ret, ["parm", "out", "return"], "None", 1))
        fields.extend(locals_)
        fn = _field(name, "Function", flags=flags, fields=fields)
        fn["native"], fn["precedence"] = native_index, precedence
        fn["_kind_word"] = kind_word
        fn["_ret"] = ret
        fn["_friendly"] = name_tok.text      # what an operator is written as
        fn["_check_line"] = check_line
        if body_span is not None:
            fn["_body"] = body_span
        _mark(fn, name_tok, name_pos)
        out = [fn] + extra
        if kind_word == "delegate":
            out.append(_field(f"__{name}__Delegate", "DelegateProperty",
                              flags=["needctorlink"], array_dim=1, category="None",
                              type=name))
        if not in_state:
            for g in self.fields:
                if g["kind"] == "Function" and g["name"].lower() == fn["name"].lower():
                    fn["_conflict"] = True
        return out

    def _operator_name(self, symbol: str, kind_word: str, parms: list[dict]) -> str:
        if symbol and symbol[0].isalpha():
            base = symbol
        else:
            base = "".join(OP_CHAR_WORDS.get(ch, ch) for ch in symbol)
        types = []
        for p in parms:
            w = p.get("_word", "")
            if p["kind"] == "ArrayProperty":
                # Measured: an array parameter is named by its element type
                # (StreamPlaylistEditor's += on array<string> is AddEqual_StrStr).
                w = (p.get("inner_type") or {}).get("word", w)
            w = w.split(".")[-1]          # GameInfo.KeyValuePair -> KeyValuePair
            types.append(OP_TYPE_WORDS.get(w.lower(), w[:1].upper() + w[1:]))
        pre = "Pre" if kind_word == "preoperator" else ""
        return f"{base}_{pre}{''.join(types)}"

    def _body_locals(self, extra: list[dict], parms: list[dict] | None = None) -> list[dict]:
        """Skip a body, collecting `local` declarations at statement starts."""
        c = self.c
        c.expect("{")
        depth = 1
        out: list[dict] = list(parms or [])      # params first: a local can't reuse one
        nparms = len(out)
        at_statement_start = True
        after_code = False          # a local after a statement is compiled differently
        while depth:
            tok = c.peek()
            if tok is None:
                if after_code:
                    # Pass 1 pops the function at its first statement and skips the
                    # rest with SkipStatements, which names the enclosing block.
                    outer = c.blocks[-2] if len(c.blocks) > 1 else "Class"
                    raise DeclError(f"Unexpected end of file at end of {outer}", c.eof_line, len(c.t))
                raise c.eof()
            w = tok.text.lower() if tok.kind == IDENT else None
            if at_statement_start and depth == 1 and w == "var":
                raise c.error("Instance variables are only allowed at class scope (use 'local'?)")
            if at_statement_start and w == "local" and depth == 1:
                c.next()
                while c.at_word() in ALL_MODIFIERS and c.at_word() not in LOCAL_FLAGS and \
                        not (c.peek(1) is not None and c.peek(1).kind == SYMBOL):
                    raise c.error("Specified type modifiers not allowed here")
                typ = self._type(extra, "Variable declaration", allow_inline=False)
                mark = len(out)
                self._declare_names(out, typ, [], "None", "Variable declaration")
                if after_code:
                    # Measured: locals declared after a statement (UCC allows it)
                    # get no needctorlink (UTComp_ScoreBoardCTF.DrawMapTitle).
                    for prop in out[mark:]:
                        prop["flags"] = [x for x in prop.get("flags", []) if x != "needctorlink"]
                        if not prop["flags"]:
                            prop.pop("flags")
                c.expect(";")
                at_statement_start = True
                continue
            if at_statement_start and depth == 1:
                after_code = True
            c.next()
            if tok.kind == SYMBOL and tok.text == "{":
                depth += 1
            elif tok.kind == SYMBOL and tok.text == "}":
                depth -= 1
            at_statement_start = tok.kind == SYMBOL and tok.text in (";", "{", "}")
        return out[nparms:]


def parse_declarations(tokens: list[Token], class_name_hint: str = "",
                       eof_line: int = 1, missing_includes: set | None = None) -> dict:
    """The class's declarations. A syntax error stops the parse; the partial view is
    returned with the error under "_error" (a DeclError), so deferred checks on the
    fields before it can still run."""
    p = DeclParser(tokens, class_name_hint, eof_line)
    p.missing_includes = missing_includes or set()
    view = p.parse()
    view["_error"] = p.error
    return view
