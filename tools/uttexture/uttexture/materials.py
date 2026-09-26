"""Vendored from ut3converter/ut2/materials.py -- UE2 material graph emitter.

Copied rather than imported so uttexture stands alone: ut3converter lives in
the other install, and depending on it across installs is exactly the split
this project was made to end. Kept byte-identical so fixes can be diffed both
ways; the TGA orientation fix (rows bottom-up) is in both.
"""

class MaterialSet:
    """UE2 material objects to define in the generated package."""

    # The array that keeps them from being dropped at save time.
    KEEP_ALIVE = "GeneratedMaterials"

    def __init__(self, package_name, tag=""):
        self.package_name = package_name
        # Shares TextureSet's per-package tag. Materials sit in the package
        # root while textures sit in a group, so the two can never collide by
        # path -- but the ASE importer binds `*BITMAP` by leaf name across every
        # loaded UMaterial (Editor/Src/UnStaticMesh.cpp:680), and a Shader is a
        # UMaterial. Two converted maps in one `ucc make` would otherwise fight
        # over a name exactly as their textures once did.
        self.tag = tag
        self.definitions = {}   # name -> (class name, [(property, value), ...])
        self.order = []         # names, in the order they must be written
        self._by_body = {}      # definition body -> name, so identical graphs share

    def add(self, kind, base_name, properties):
        """Register one material object, returning its object name.

        `properties` is [(name, already-formatted UnrealScript value)]. An
        identical definition returns the existing name rather than a duplicate:
        UT3 maps reuse one material across hundreds of surfaces.
        """
        body = (kind, tuple(properties))
        if body in self._by_body:
            return self._by_body[body]
        name = self._unique(base_name, kind)
        self.definitions[name] = (kind, list(properties))
        self.order.append(name)
        self._by_body[body] = name
        return name

    _SUFFIX = {"FinalBlend": "FB", "Shader": "SH", "TexPanner": "PN",
               "Combiner": "CB", "ConstantColor": "CC", "TexScaler": "TS",
               "TexOscillator": "TO", "ColorModifier": "CM"}

    # An FName is 64 characters (Core/Inc/UnName.h:16). Past that the name is
    # truncated on import while the reference in the t3d keeps its full length,
    # so the two stop matching -- and for a material it is worse than that,
    # because `ucc make` resolves these at compile time and the build fails
    # outright. DM-Deck reached 68 with
    # `M_UN_Volumetrics_Lightbeam_Cheap_02_FloodlightsCold_Flattened_c305CM`.
    MAX_NAME = 64

    def _unique(self, base, kind):
        # Callers name a material after the texture under it, and that name
        # already carries the tag -- appending a second one just eats into the
        # budget.
        if self.tag and base.endswith("_" + self.tag):
            base = base[:-(len(self.tag) + 1)]
        suffix = "%s%s" % (self.tag, self._SUFFIX.get(kind, "MT"))
        stem = self._fit(base, suffix, "")
        if stem not in self.definitions:
            return stem
        n = 2
        while True:
            candidate = self._fit(base, suffix, "_%d" % n)
            if candidate not in self.definitions:
                return candidate
            n += 1

    def _fit(self, base, suffix, counter):
        """`base_suffixcounter`, with base trimmed so the whole fits an FName."""
        room = self.MAX_NAME - len(suffix) - len(counter) - 1
        return "%s_%s%s" % (base[:max(1, room)], suffix, counter)

    def path(self, name, kind=None):
        """`Class'Package.Name'`, the form a t3d or an actor property wants."""
        if not name or name not in self.definitions:
            return None
        kind = kind or self.definitions[name][0]
        return "%s'%s.%s'" % (kind, self.package_name, name)

    def bare_path(self, name):
        """`Package.Name`, for a t3d polygon, which names no class."""
        if not name or name not in self.definitions:
            return None
        return "%s.%s" % (self.package_name, name)

    def __len__(self):
        return len(self.definitions)

    def emit(self):
        """The defaultproperties body: the blocks, then the KeepAlive array."""
        if not self.order:
            return []
        lines = []
        for name in self.order:
            kind, properties = self.definitions[name]
            lines.append("     Begin Object Class=%s Name=%s" % (kind, name))
            for key, value in properties:
                lines.append("         %s=%s" % (key, value))
            lines.append("     End Object")
            lines.append("")
        lines.append("     // Without these the objects above are built and then")
        lines.append("     // dropped: SavePackage writes only what is referenced.")
        for i, name in enumerate(self.order):
            lines.append("     %s(%d)=Material'%s.%s'"
                         % (self.KEEP_ALIVE, i, self.package_name, name))
        return lines

    def declaration(self):
        """The `var` line the generated class needs for KEEP_ALIVE."""
        return "var array<Material> %s;" % self.KEEP_ALIVE
