"""Where things are, for a toolchain that no longer lives inside the install.

uttexture grew up inside a UT2004 install and found everything by walking up
from its own directory. Bundled with Sweeney it sits outside, so the install
root, ut3converter and umodel all have to be looked up instead.

`~/.sweeney/config.json` is written by `scripts/setup.sh` and is the single
source of truth. An environment variable overrides it, for a one-off run
against a second install.
"""

import json
import os

CONFIG = os.path.join(os.path.expanduser("~"), ".sweeney", "config.json")


def _config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def install_root():
    """The UT2004 install the toolchain is operating on."""
    env = os.environ.get("UT2004_INSTALL")
    if env:
        return env
    root = _config().get("install_root")
    if not root:
        raise SystemExit(
            "no UT2004 install known: run Sweeney's scripts/setup.sh, or set "
            "UT2004_INSTALL")
    return root


def work_root():
    """Where per-map project data lives: config, extracted PNGs, upscales.

    It is gigabytes per map and must not land inside Sweeney's own checkout, so
    it defaults to a directory in the UT2004 install rather than beside the
    code. `paths.texture_work` in the config overrides it.
    """
    env = os.environ.get("SWEENEY_TEXTURE_WORK")
    if env:
        return env
    configured = _config().get("paths", {}).get("texture_work")
    if configured:
        return configured
    return os.path.join(install_root(), "texture-rebuild")


def ut3converter():
    """ut3converter's checkout, or None. Public, and several tools read it."""
    return os.environ.get("UT3CONVERTER") or _config().get("tools", {}).get(
        "ut3converter") or None


def ut3converter_tools():
    root = ut3converter()
    if not root:
        return None
    return os.path.join(root, "tools")


def umodel():
    """umodel, which every texture and mesh export goes through.

    Checked here rather than trusted: when it is missing, umodel exports
    nothing and the pipeline carries on to report "0 textures" and build an
    empty package. Failing at the first call is far cheaper than a clean build
    of nothing.
    """
    import shutil
    found = (os.environ.get("UMODEL")
             or _config().get("tools", {}).get("umodel")
             or shutil.which("umodel"))
    if not found:
        raise SystemExit(
            "umodel not found. It exports the textures and meshes, so nothing\n"
            "can be rebuilt without it. Put it on PATH, set tools.umodel in\n"
            "%s, or set the UMODEL environment variable." % CONFIG)
    if not os.path.exists(found) and not shutil.which(found):
        raise SystemExit("umodel not found at %r (from tools.umodel or UMODEL)"
                         % found)
    return found


def umodel_cmd():
    """umodel as an argv prefix, with wine only where it is actually needed.

    Gildor ships a Windows build and a Linux one; the Linux build is 32-bit
    against libpng12 and will not start on a current distro, so the Windows
    build through wine is the usual choice off Windows. On Windows, and for a
    native binary anywhere, wine would break it.
    """
    path = umodel()
    windows = os.name == "nt" or "MINGW" in os.environ.get("MSYSTEM", "")
    if path.lower().endswith(".exe") and not windows:
        return ["wine", path]
    return [path]
