#!/usr/bin/env python3
"""Recover a cached UT2004 package and its transitive package dependencies."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "uttexture"))
from uttexture.ue2 import Package


FOLDERS = {
    ".ut2": "Maps",
    ".u": "System",
    ".utx": "Textures",
    ".usx": "StaticMeshes",
    ".ukx": "Animations",
    ".uax": "Sounds",
    ".umx": "Music",
    ".ogg": "Music",
}


def package_name(value):
    if not value or value in (".", "..") or any(c in value for c in "/\\:"):
        raise ValueError("expected a package name, not a path: %r" % value)
    suffix = Path(value).suffix.lower()
    if suffix and suffix not in FOLDERS:
        raise ValueError("unsupported package extension: " + suffix)
    return Path(value).stem.casefold() if suffix else value.casefold()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.digest()


def dependencies(path, filename):
    if Path(filename).suffix.lower() == ".ogg":
        return []
    # Retain the instance so even a partially parsed package can be closed.
    pkg = Package.__new__(Package)
    try:
        pkg.__init__(path)
        return sorted({entry["name"] for entry in pkg.imports
                       if entry["class"] == "Package" and entry["outer"] == 0})
    except (IndexError, struct.error) as error:
        raise ValueError("malformed package %s: %s" % (path, error)) from error
    finally:
        if hasattr(pkg, "data"):
            pkg.data.close()
        if hasattr(pkg, "_fh"):
            pkg._fh.close()


def cache_index(cache):
    index = {}
    data = (cache / "cache.ini").read_bytes()
    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "latin-1"
    lines = data.decode(encoding).splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith((";", "#", "[")) or "=" not in line:
            continue
        guid, name = (part.strip() for part in line.split("=", 1))
        if Path(name).suffix.lower() not in FOLDERS:
            continue
        key = package_name(name)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", guid):
            raise ValueError("invalid cache identifier for " + name)
        source = cache / (guid + ".uxx")
        index.setdefault(key, set()).add((source, name))
    return index


def installed_index(install):
    index = {}
    for extension, folder in FOLDERS.items():
        directory = install / folder
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() == extension:
                index.setdefault(path.stem.casefold(), []).append(path)
    return index


def select_cached(index, key, requested=None):
    matches = [(path, name) for path, name in index.get(key, ())
               if path.is_file() and
               (requested is None or name.casefold() == requested.casefold())]
    if not matches:
        raise ValueError("package not found in cache: " + (requested or key))
    if len(matches) != 1:
        names = ", ".join(sorted(name + " [" + path.stem + "]"
                                 for path, name in matches))
        raise ValueError("multiple cached versions of %s: %s" % (key, names))
    return matches[0]


def dependency_candidates(cached, installed, key):
    existing = installed.get(key, [])
    if existing:
        by_extension = {}
        for path in existing:
            extension = path.suffix.casefold()
            if extension in by_extension:
                raise ValueError("ambiguous installed dependency: " + key)
            by_extension[extension] = path
        return [(path, path.name, False) for path in existing]
    names = {name for path, name in cached.get(key, ()) if path.is_file()}
    if not names:
        raise ValueError("package not found in cache: " + key)
    return [(*select_cached(cached, key, name), True) for name in sorted(names)]


def extract(name, cache, install, dry_run=False):
    """Preflight the whole closure before copying; never overwrite an install."""
    cache, install = Path(cache).resolve(), Path(install).resolve()
    if not (install / "System" / "Engine.u").is_file():
        raise ValueError("not a UT2004 install: " + str(install))
    key = package_name(name)
    cached, installed = cache_index(cache), installed_index(install)
    requested = name if Path(name).suffix else None
    source, filename = select_cached(cached, key, requested)
    pending = [(source, filename, True)]
    seen, copies, present = set(), {}, set()
    while pending:
        source, filename, from_cache = pending.pop()
        filekey = filename.casefold()
        key = package_name(filename)
        if filekey in seen:
            continue
        seen.add(filekey)
        if from_cache:
            existing = [path for path in installed.get(key, [])
                        if path.suffix.casefold() == Path(filename).suffix.casefold()]
            if existing:
                if len(existing) != 1 or existing[0].name.casefold() != filename.casefold():
                    raise ValueError("conflicting installed package: " + filename)
                if digest(source) != digest(existing[0]):
                    raise ValueError("installed package differs from cache: " + str(existing[0]))
                present.add(existing[0])
            else:
                dest = install / FOLDERS[Path(filename).suffix.lower()] / filename
                copies[filekey] = (source, dest)
        else:
            present.add(source)
        for dep in dependencies(source, filename):
            depkey = package_name(dep)
            # Content can share a basename across types, such as .utx and .usx.
            if any(package_name(item[1]) == depkey for item in pending):
                continue
            if any(Path(item).stem.casefold() == depkey for item in seen):
                continue
            pending.extend(dependency_candidates(cached, installed, depkey))

    for source, dest in sorted(copies.values(), key=lambda pair: str(pair[1])):
        if dry_run:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as target:
            try:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, target)
            except OSError:
                target.close()
                dest.unlink()
                raise
        if digest(source) != digest(dest):
            dest.unlink()
            raise ValueError("copy verification failed: " + str(dest))
    return sorted(dest for _, dest in copies.values()), sorted(present), len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="exact package name, with or without extension")
    parser.add_argument("--cache", type=Path, help="source Cache directory")
    parser.add_argument("--install", type=Path, help="destination UT2004 install")
    parser.add_argument("--dry-run", action="store_true", help="resolve without copying")
    args = parser.parse_args()
    try:
        config = {}
        if args.cache is None or args.install is None:
            config_path = Path.home() / ".sweeney" / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
        install = args.install or os.environ.get("UT2004_INSTALL") or config.get("install_root")
        client = config.get("client_install_root") or install
        cache = args.cache or (Path(client) / "Cache" if client else None)
        if not install or not cache:
            raise ValueError("run Sweeney setup or supply --install and --cache")
        print("Cache: " + str(cache))
        print("Destination: " + str(install))
        copied, present, count = extract(args.package, cache, install, args.dry_run)
        for path in copied:
            print(("Would copy: " if args.dry_run else "Copied: ") + str(path))
        print("%d packages resolved; %d %s; %d already installed" %
              (count, len(copied), "planned" if args.dry_run else "copied", len(present)))
    except (OSError, ValueError) as error:
        print("cache-extract: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
