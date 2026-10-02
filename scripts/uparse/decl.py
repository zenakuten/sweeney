"""Declarations: UCC's first pass, from tokens to the class's fields.

Produces the same shape as reflect.class_view for the compiled class, so the
scoreboard can diff it field by field: the class header, every var / enum / struct /
const / function / state, function parameters and locals, and the flags UCC sets on
each. Function bodies are skipped, except for their `local` declarations, as UCC's
first pass skips them.

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
    "reliable": None, "unreliable": None,
}
PARM_FLAGS = {"optional": "optional", "out": "out", "coerce": "coerce", "skip": "skip",
              "const": "const"}
CLASS_FLAG_WORDS = {
    "abstract": "abstract", "transient": "transient", "noexport": "noexport",
    "placeable": "placeable", "perobjectconfig": "perobjectconfig",
    "nativereplication": "nativereplication", "editinlinenew": "editinlinenew",
    "collapsecategories": "collapsecategories", "exportstructs": "exportstructs",
    "safereplace": "safereplace", "hidedropdown": "hidedropdown",
    "cacheexempt": "cacheexempt",
}
STRUCT_FLAG_WORDS = {"native": "native", "export": "export", "long": "long", "init": "init"}


class DeclError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.message, self.line = message, line


class Cursor:
    def __init__(self, tokens: list[Token]):
        self.t = tokens
        self.i = 0

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            last = self.t[-1].line if self.t else 1
            raise DeclError("Unexpected end of file", last)
        self.i += 1
        return tok

    def at(self, text: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.kind in (IDENT, SYMBOL) and tok.text.lower() == text

    def accept(self, text: str) -> bool:
        if self.at(text):
            self.i += 1
            return True
        return False

    def expect(self, text: str, where: str = "") -> Token:
        tok = self.peek()
        if not self.at(text):
            got = tok.text if tok else "end of file"
            raise DeclError(f"Missing '{text}'{where} before '{got}'", tok.line if tok else 0)
        return self.next()

    def ident(self) -> Token:
        tok = self.next()
        if tok.kind != IDENT:
            raise DeclError(f"Missing identifier, got '{tok.text}'", tok.line)
        return tok

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


class DeclParser:
    def __init__(self, tokens: list[Token], class_name_hint: str = ""):
        self.c = Cursor(tokens)
        self.cls = class_name_hint
        self.fields: list[dict] = []
        self.header: dict = {}
        self.replicated: dict[str, bool] = {}    # name -> reliable

    # ------------------------------------------------------------ top level

    def parse(self) -> dict:
        c = self.c
        while c.peek() is not None:
            mark = len(self.fields)
            self._parse_one()
            for f in self.fields[mark:]:
                f["_pos"] = c.i            # source order: what UCC had parsed by then
        self._apply_replication()
        return {"name": self.header.get("name", self.cls), "super": self.header.get("super"),
                "class_flags": self.header.get("class_flags", []),
                "config": self.header.get("config"), "within": self.header.get("within"),
                "fields": self.fields}

    def _parse_one(self) -> None:
        c = self.c
        if True:
            if c.at("#"):
                self._directive()
            elif c.at("class") and not self.header:
                self._class_header()
            elif c.at("var"):
                self._var(self.fields, owner=self.cls)
            elif c.at("enum"):
                self.fields.append(self._enum())
                c.accept(";")
            elif c.at("struct"):
                self.fields.append(self._struct())
                c.accept(";")
            elif c.at("const"):
                self.fields.append(self._const())
            elif c.at("replication"):
                self._replication()
            elif c.at(";"):
                c.next()
            elif self._state_ahead():
                self.fields.append(self._state())
            else:
                self.fields.extend(self._function())

    def _directive(self) -> None:
        c = self.c
        c.next()                                  # '#'
        c.next()                                  # exec / include / ...
        if c.peek() is not None and c.peek().kind == RAW:
            c.next()

    def _class_header(self) -> None:
        c = self.c
        c.expect("class")
        name = c.ident().text
        self.cls = name
        flags: list[str] = []
        sup, within, config = None, None, None
        if c.accept("extends"):
            sup = self._dotted()
        while not c.at(";"):
            word = c.next()
            w = word.text.lower()
            if w == "within":
                within = self._dotted()
            elif w == "config":
                # Names the ini file only; the class flag means "has config vars".
                if c.at("("):
                    inner = c.skip_group("(", ")")
                    config = inner[0].text if inner else None
            elif w in CLASS_FLAG_WORDS:
                flags.append(CLASS_FLAG_WORDS[w])
            elif w == "instanced":
                flags += ["editinlinenew", "instanced"]
            elif w == "native":
                flags.append("_native")
            elif w in ("notplaceable", "noteditinlinenew", "dontcollapsecategories"):
                flags.append("_" + w)
            elif w in ("native", "hidecategories", "showcategories", "dependson",
                       "notplaceable", "dontcollapsecategories", "noteditinlinenew",
                       "hidedropdown", "cacheexempt", "parseconfig", "instanced",
                       "nativeonly", "localized") or True:
                if c.at("("):
                    c.skip_group("(", ")")
        c.expect(";")
        self.header = {"name": name, "super": sup, "class_flags": flags,
                       "within": within, "config": config}

    def _dotted(self) -> str:
        parts = [self.c.ident().text]
        while self.c.at(".") and self.c.peek(1) is not None and self.c.peek(1).kind == IDENT:
            self.c.next()
            parts.append(self.c.ident().text)
        return ".".join(parts)

    # ------------------------------------------------------------ types

    def _type(self, owner_fields: list[dict]) -> dict:
        """A type: returns {"kind", plus type details}. Inline enum/struct declarations
        are appended to owner_fields, as UCC adds them to the enclosing scope."""
        c = self.c
        tok = c.next()
        w = tok.text.lower()
        if w in TYPE_KINDS:
            return {"kind": TYPE_KINDS[w], "word": tok.text}
        if w == "class":
            meta = None
            if c.accept("<"):
                meta = self._dotted()
                c.expect(">")
            return {"kind": "ClassProperty", "word": "class", "meta_class": meta or "Object"}
        if w == "array":
            c.expect("<")
            inner = self._type(owner_fields)
            if c.at(">>"):                         # array<class<X>> lexes '>>'
                c.next()
            else:
                c.expect(">")
            return {"kind": "ArrayProperty", "word": "array", "inner_type": inner}
        if w == "enum":
            c.i -= 1
            e = self._enum()
            owner_fields.append(e)
            return {"kind": "ByteProperty", "word": e["name"], "enum": e["name"]}
        if w == "struct":
            c.i -= 1
            s = self._struct()
            owner_fields.append(s)
            return {"kind": "StructProperty", "word": s["name"], "type": s["name"]}
        if w == "delegate":
            c.expect("<")
            fn = self._dotted()
            c.expect(">")
            return {"kind": "DelegateProperty", "word": "delegate", "type": fn}
        if tok.kind != IDENT:
            raise DeclError(f"Unrecognized type '{tok.text}'", tok.line)
        name = tok.text
        while c.at(".") and c.peek(1) is not None and c.peek(1).kind == IDENT:
            c.next()
            name += "." + c.next().text
        # Object, struct or enum: which one needs the type's declaration (P3).
        return {"kind": "UnresolvedProperty", "word": name, "type": name}

    # ------------------------------------------------------------ var

    def _var(self, out: list[dict], owner: str) -> None:
        c = self.c
        c.expect("var")
        flags: list[str] = []
        category = "None"
        if c.at("("):
            inner = c.skip_group("(", ")")
            flags.append("edit")
            category = inner[0].text if inner else owner
        while c.peek() is not None and c.peek().kind == IDENT and c.peek().text.lower() in VAR_FLAGS:
            f = VAR_FLAGS[c.next().text.lower()]
            if f:
                flags.append(f)
        if "globalconfig" in flags and "config" not in flags:
            flags.append("config")
        if "automated" in flags:
            # Measured: automated implies these (1,275 fields in the engine source).
            flags += [x for x in ("edit", "editinlinenotify") if x not in flags]
            if category == "None":
                category = owner
        if "editinlineuse" in flags and "editinline" not in flags:
            flags.append("editinline")
        typ = self._type(out)
        while True:
            name_tok = c.ident()
            dim = self._array_dim()
            out.append(self._property(name_tok.text, typ, flags, category, dim))
            if not c.accept(","):
                break
        c.expect(";")

    def _array_dim(self) -> int:
        c = self.c
        if not c.at("["):
            return 1
        inner = c.skip_group("[", "]")
        if len(inner) == 1 and inner[0].kind == INT:
            return inner[0].value
        return {"const": [t.text for t in inner]}           # resolved later

    def _property(self, name: str, typ: dict, flags: list[str], category: str, dim) -> dict:
        kind = typ["kind"]
        flags = list(flags)
        if kind in ("StrProperty", "ArrayProperty", "DelegateProperty") and "native" not in flags:
            flags.append("needctorlink")
        if "automated" in flags and "needctorlink" not in flags:
            flags.append("needctorlink")
        if kind == "PointerProperty":
            # Measured: a pointer is stored native and transient.
            flags = [x for x in flags if x != "const"]
            flags += [x for x in ("native", "transient") if x not in flags]
        f = _field(name, kind, flags=flags, array_dim=dim, category=category)
        for k in ("enum", "type", "meta_class"):
            if k in typ:
                f[k] = typ[k]
        if kind == "ClassProperty":
            f["type"] = "Class"
        if kind == "ArrayProperty":
            f["inner_type"] = typ["inner_type"]
        f["_word"] = typ["word"]
        return f

    # ------------------------------------------------------------ enum, struct, const

    def _enum(self) -> dict:
        c = self.c
        c.expect("enum")
        name = c.ident().text
        values = [t.text for t in c.skip_group("{", "}") if t.kind == IDENT]
        return _field(name, "Enum", values=values)

    def _struct(self) -> dict:
        c = self.c
        c.expect("struct")
        flags = []
        while c.peek() is not None and c.peek().kind == IDENT and \
                c.peek().text.lower() in STRUCT_FLAG_WORDS and \
                c.peek(1) is not None and c.peek(1).kind == IDENT:
            flags.append(STRUCT_FLAG_WORDS[c.next().text.lower()])
        name = c.ident().text
        sup = None
        if c.accept("extends"):
            sup = self._dotted()
        c.expect("{")
        members: list[dict] = []
        while not c.at("}"):
            if c.at("var"):
                self._var(members, owner=name)
            elif c.at("enum"):
                members.append(self._enum())
                c.accept(";")
            elif c.at("struct"):
                members.append(self._struct())
                c.accept(";")
            elif c.at("const"):
                members.append(self._const())
            elif c.at(";"):
                c.next()
            elif c.at("structcpptext") or c.at("cpptext") or c.at("cppstruct"):
                c.next()
                c.skip_group("{", "}")
            else:
                tok = c.next()
                raise DeclError(f"Unexpected '{tok.text}' in struct", tok.line)
        c.expect("}")
        return _field(name, "Struct", struct_flags=flags, super=sup, fields=members)

    def _const(self) -> dict:
        c = self.c
        c.expect("const")
        name = c.ident().text
        eq = c.expect("=")
        start = c.i
        while not c.at(";"):
            c.next()
        semi = c.next()
        toks = c.t[start:c.i - 1]
        # UCC stores the raw text after '=' up to ';'.
        return _field(name, "Const", value=None, _const_span=(eq.end, semi.start),
                      _const_tokens=[t.text for t in toks])

    # ------------------------------------------------------------ replication

    def _replication(self) -> None:
        c = self.c
        c.expect("replication")
        c.expect("{")
        while not c.at("}"):
            reliable = True
            if c.accept("reliable"):
                reliable = True
            elif c.accept("unreliable"):
                reliable = False
            else:
                tok = c.next()
                raise DeclError(f"Missing 'reliable' or 'unreliable', got '{tok.text}'", tok.line)
            c.expect("if")
            c.skip_group("(", ")")
            while True:
                n = c.ident().text
                self.replicated[n.lower()] = reliable
                if not c.accept(","):
                    break
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
        while self.c.peek(k) is not None and self.c.peek(k).kind == IDENT and \
                self.c.peek(k).text.lower() in ("auto", "simulated"):
            k += 1
        return self.c.at("state", k)

    def _state(self) -> dict:
        c = self.c
        flags = []
        while not c.at("state"):
            flags.append(c.next().text.lower())
        c.expect("state")
        if c.at("("):
            c.skip_group("(", ")")
            flags.append("editable")
        name = c.ident().text
        sup = None
        if c.accept("extends"):
            sup = c.ident().text
        c.expect("{")
        fields: list[dict] = []
        while not c.at("}"):
            if c.at("ignores"):
                # Each ignored function becomes an empty stub in the state.
                c.next()
                while True:
                    stub = _field(c.ident().text, "Function", flags=["public"])
                    stub["native"], stub["precedence"], stub["fields"] = 0, 0, []
                    stub["_ignored"] = True
                    fields.append(stub)
                    if not c.accept(","):
                        break
                c.expect(";")
            elif c.at(";"):
                c.next()
            elif self._label_ahead():
                # State code: labels and statements to the end of the state.
                self._skip_state_code()
                break
            else:
                fields.extend(self._function())
        c.expect("}")
        sflags = [f for f in ("editable", "auto", "simulated") if f in flags]
        return _field(name, "State", state_flags=sflags, super=sup, fields=fields)

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
                return
            if tok.kind == SYMBOL and tok.text == "{":
                depth += 1
            elif tok.kind == SYMBOL and tok.text == "}":
                if depth == 0:
                    return
                depth -= 1
            c.next()

    # ------------------------------------------------------------ functions

    def _function(self) -> list[dict]:
        c = self.c
        flags: list[str] = []
        native_index = 0
        kind_word = None
        precedence = 0
        while True:
            tok = c.peek()
            if tok is None:
                raise DeclError("Unexpected end of file", c.t[-1].line)
            w = tok.text.lower() if tok.kind == IDENT else None
            if w in ("function", "event", "delegate", "operator", "preoperator", "postoperator"):
                kind_word = w
                c.next()
                if w == "operator" and c.at("("):
                    inner = c.skip_group("(", ")")
                    precedence = inner[0].value if inner and inner[0].kind == INT else 0
                break
            if w in FUNC_FLAGS:
                c.next()
                if w == "native" and c.at("("):
                    inner = c.skip_group("(", ")")
                    native_index = inner[0].value if inner and inner[0].kind == INT else 0
                if FUNC_FLAGS[w]:
                    flags.append(FUNC_FLAGS[w])
                continue
            raise DeclError(f"Unexpected '{tok.text}'", tok.line)

        # Modifiers may also follow the keyword: `function static int F()`.
        while c.peek() is not None and c.peek().kind == IDENT and \
                c.peek().text.lower() in FUNC_FLAGS and \
                not (c.peek(1) is not None and c.peek(1).kind == SYMBOL and c.peek(1).text == "("):
            w = c.next().text.lower()
            if w == "native" and c.at("("):
                inner = c.skip_group("(", ")")
                native_index = inner[0].value if inner and inner[0].kind == INT else 0
            if FUNC_FLAGS[w]:
                flags.append(FUNC_FLAGS[w])

        extra: list[dict] = []
        # Return type, unless the next tokens are `Name (`.
        ret = None
        if not (c.peek(1) is not None and c.peek(1).kind == SYMBOL and c.peek(1).text == "("):
            ret = self._type(extra)
        name_tok = c.next()
        name = name_tok.text
        parms: list[dict] = []
        c.expect("(")
        while not c.at(")"):
            pflags = ["parm"]
            while c.peek() is not None and c.peek().kind == IDENT and \
                    c.peek().text.lower() in PARM_FLAGS:
                pflags.append(PARM_FLAGS[c.next().text.lower()])
            ptype = self._type(extra)
            pname = c.ident().text
            dim = self._array_dim()
            parms.append(self._property(pname, ptype, pflags, "None", dim))
            if not c.accept(","):
                break
        c.expect(")")
        while c.peek() is not None and c.peek().kind == IDENT and c.peek().text.lower() in ("const",):
            c.next()
            flags.append("const")

        locals_: list[dict] = []
        defined = False
        if c.at("{"):
            defined = True
            locals_ = self._body_locals(extra)
        else:
            c.expect(";")

        if kind_word in ("operator", "preoperator", "postoperator"):
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
        out = [fn] + extra
        if kind_word == "delegate":
            out.append(_field(f"__{name}__Delegate", "DelegateProperty",
                              flags=["needctorlink"], array_dim=1, category="None",
                              type=name))
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

    def _body_locals(self, extra: list[dict]) -> list[dict]:
        """Skip a body, collecting `local` declarations at statement starts."""
        c = self.c
        c.expect("{")
        depth = 1
        out: list[dict] = []
        at_statement_start = True
        after_code = False          # a local after a statement is compiled differently
        while depth:
            tok = c.peek()
            if tok is None:
                raise DeclError("Unexpected end of file in function body", c.t[-1].line)
            if at_statement_start and tok.kind == IDENT and tok.text.lower() == "local" and depth == 1:
                c.next()
                typ = self._type(extra)
                while True:
                    n = c.ident().text
                    dim = self._array_dim()
                    prop = self._property(n, typ, [], "None", dim)
                    if after_code:
                        # Measured: locals declared after a statement (UCC allows it)
                        # get no needctorlink (UTComp_ScoreBoardCTF.DrawMapTitle).
                        prop["flags"] = [x for x in prop.get("flags", []) if x != "needctorlink"]
                        if not prop["flags"]:
                            prop.pop("flags")
                    out.append(prop)
                    if not c.accept(","):
                        break
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
        return out


def parse_declarations(tokens: list[Token], class_name_hint: str = "") -> dict:
    return DeclParser(tokens, class_name_hint).parse()
