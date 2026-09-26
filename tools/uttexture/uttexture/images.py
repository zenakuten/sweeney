"""Vendored from ut3converter/ut2/images.py -- DDS/TGA writers.

Copied rather than imported so uttexture stands alone: ut3converter lives in
the other install, and depending on it across installs is exactly the split
this project was made to end. Kept byte-identical so fixes can be diffed both
ways; the TGA orientation fix (rows bottom-up) is in both.
"""

import struct

DDS_MAGIC = b"DDS "
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000
DDPF_FOURCC = 0x4
DDSCAPS_COMPLEX = 0x8
DDSCAPS_TEXTURE = 0x1000
DDSCAPS_MIPMAP = 0x400000

FOURCC = {"PF_DXT1": b"DXT1", "PF_DXT3": b"DXT3", "PF_DXT5": b"DXT5"}
DXT_NUMBER = {"PF_DXT1": 1, "PF_DXT3": 3, "PF_DXT5": 5}


def mip_size(fmt, width, height):
    """Bytes UT2004 expects for one mip level of a DXT surface.

    Both engines pad a level below 4x4 up to 4x4 rather than storing it packed,
    which is what makes UT3's chain a straight pass-through: the importer sizes
    each level as `Max(w,4) * Max(h,4)` halved for DXT1
    (Editor/Src/UnEdFact.cpp:2610) and `check()`s that much is present, so a
    level short by a byte is an assertion failure in the editor rather than a
    bad texture.
    """
    w, h = max(width, 4), max(height, 4)
    return w * h // 2 if fmt == "PF_DXT1" else w * h


def mip_chain(fmt, width, height, mips):
    """The longest contiguous run of `mips` the importer will accept.

    `mips` is [(width, height, data), ...] largest first. Levels halve with
    `(w+1)/2` from the top, and anything whose size or dimensions disagree ends
    the chain -- a short one imports fine (dwMipMapCount just says how many),
    a wrong one asserts.

    Dimensions are compared *padded*, because both engines store the tail of a
    chain that way: UT3 records its 2x2 and 1x1 levels as 4x4, which is the size
    the importer computes for them anyway. Comparing raw would drop the last two
    levels of every texture -- the ones a surface is drawn with when it is
    furthest away and aliasing is worst.
    """
    out = []
    w, h = width, height
    for mip_width, mip_height, data in mips:
        if (max(mip_width, 4), max(mip_height, 4)) != (max(w, 4), max(h, 4)):
            break
        if len(data) != mip_size(fmt, w, h):
            break
        out.append(data)
        w, h = (w + 1) // 2, (h + 1) // 2
    return out


def write_dds(path, width, height, fmt, data, mips=None):
    """Write a DXT-compressed .dds, with its mip chain when one is given."""
    levels = mip_chain(fmt, width, height, mips) if mips else []
    if not levels:
        levels = [data]
    header = bytearray()
    header += DDS_MAGIC
    header += struct.pack("<I", 124)  # dwSize
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps = DDSCAPS_TEXTURE
    if len(levels) > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_MIPMAP | DDSCAPS_COMPLEX
    header += struct.pack("<I", flags)
    header += struct.pack("<I", height)
    header += struct.pack("<I", width)
    header += struct.pack("<I", len(levels[0]))  # dwPitchOrLinearSize
    header += struct.pack("<I", 0)  # dwDepth
    header += struct.pack("<I", len(levels) if len(levels) > 1 else 0)
    header += b"\0" * 44  # dwReserved1[11]
    # DDS_PIXELFORMAT
    header += struct.pack("<I", 32)
    header += struct.pack("<I", DDPF_FOURCC)
    header += FOURCC[fmt]
    header += struct.pack("<5I", 0, 0, 0, 0, 0)  # bit count and masks
    header += struct.pack("<I", caps)
    header += struct.pack("<4I", 0, 0, 0, 0)  # caps2-4, reserved2
    assert len(header) == 128, len(header)
    with open(path, "wb") as f:
        f.write(bytes(header))
        for level in levels:
            f.write(level)


def write_tga(path, width, height, bgra):
    """Write an uncompressed 32-bit BGRA .tga. `bgra` is top-down.

    Rows go out bottom-up, for the same reason write_bmp16 does it: the importer
    flips scanlines unconditionally, copying texture row Y from file row
    Height - Y - 1 (Editor/Src/UnEdFact.cpp:2498), and it never reads the
    descriptor byte -- so bit 5 does nothing and a top-down file imports upside
    down. Seamless textures still tile when flipped, which is what let this go
    unnoticed; it shows up on directional content, like a wall texture whose
    grimy base band renders along the top instead.
    """
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0,      # id length
        0,      # no colour map
        2,      # uncompressed true-colour
        0, 0, 0,
        0, 0,   # x, y origin
        width,
        height,
        32,     # bits per pixel
        0,      # bottom-up; the importer assumes it and ignores this byte
    )
    stride = width * 4
    with open(path, "wb") as f:
        f.write(header)
        for y in range(height - 1, -1, -1):
            f.write(bgra[y * stride:(y + 1) * stride])


def to_bgra(fmt, width, height, data):
    """Convert an uncompressed UE3 surface to BGRA bytes."""
    if fmt == "PF_A8R8G8B8":
        return data[: width * height * 4]
    if fmt == "PF_G8":
        out = bytearray(width * height * 4)
        for i in range(min(len(data), width * height)):
            g = data[i]
            out[i * 4 : i * 4 + 4] = bytes((g, g, g, 255))
        return bytes(out)
    return None


def make_placeholder(size=128, grid=16, base=(128, 128, 128), line=(104, 104, 104)):
    """A neutral grey grid, for surfaces whose UE3 material resolved to nothing.

    Flat enough to judge lighting by, gridded enough to read surface scale and
    orientation. Returned as BGRA.
    """
    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            on_line = (x % grid == 0) or (y % grid == 0)
            r, g, b = line if on_line else base
            i = (y * size + x) * 4
            out[i : i + 4] = bytes((b, g, r, 255))
    return bytes(out)


def write_placeholder(path_without_ext, size=128):
    path = path_without_ext + ".tga"
    write_tga(path, size, size, make_placeholder(size))
    return path, {"ALPHA": 0}


def write_texture(path_without_ext, width, height, fmt, data, mips=None):
    """Write `data` in the best-matching format. Returns (path, exec_options)."""
    if fmt in FOURCC:
        path = path_without_ext + ".dds"
        write_dds(path, width, height, fmt, data, mips)
        return path, {"DXT": DXT_NUMBER[fmt], "ALPHA": 1 if fmt != "PF_DXT1" else 0}
    bgra = to_bgra(fmt, width, height, data)
    if bgra is None:
        return None, None
    path = path_without_ext + ".tga"
    write_tga(path, width, height, bgra)
    return path, {"ALPHA": 1}
