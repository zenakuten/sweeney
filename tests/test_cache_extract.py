"""Run with python -m unittest discover -s tests -p 'test_cache_extract.py'."""

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cache_extract.py"
spec = importlib.util.spec_from_file_location("cache_extract", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def package(imports=()):
    names = ["Core", "Package"] + list(imports)
    name_data = b"".join(bytes([len(name) + 1]) + name.encode("ascii") +
                         b"\0" + struct.pack("<I", 0x70010) for name in names)
    import_data = b"".join(bytes([0, 1]) + struct.pack("<i", 0) + bytes([i + 2])
                           for i in range(len(imports)))
    header = struct.pack("<IHH7I", 0x9E2A83C1, 128, 29, 0,
                         len(names), 36, 0, 36, len(imports), 36 + len(name_data))
    return header + name_data + import_data


class CacheExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "client" / "Cache"
        self.install = self.root / "dev"
        self.cache.mkdir(parents=True)
        (self.install / "System").mkdir(parents=True)
        (self.install / "System" / "Engine.u").write_bytes(package())
        self.index = self.cache / "cache.ini"
        self.index.write_text("[Cache]\n", encoding="ascii")
        self.counter = 0

    def cached(self, name, deps=(), data=None):
        self.counter += 1
        guid = "%032X-1" % self.counter
        path = self.cache / (guid + ".uxx")
        path.write_bytes(package(deps) if data is None else data)
        with self.index.open("a", encoding="ascii") as stream:
            stream.write(guid + "=" + name + "\n")
        return path

    def run_extract(self, name="Map", dry_run=False):
        return tool.extract(name, self.cache, self.install, dry_run)

    def test_recursive_cycle_and_installed_dependency(self):
        source = self.cached("Map.ut2", ["Bridge", "Mod"])
        bridge = self.install / "System" / "Bridge.u"
        bridge.write_bytes(package(["Tex"]))
        self.cached("Mod.u", ["Map", "Mesh"])
        self.cached("Tex.utx", ["Sound"])
        self.cached("Mesh.usx", ["Anim"])
        self.cached("Sound.uax", ["Music"])
        self.cached("Anim.ukx")
        self.cached("Music.umx")
        copied, present, count = self.run_extract("mAp.UT2")
        self.assertEqual(count, 8)
        self.assertEqual(len(copied), 7)
        self.assertEqual(present, [bridge])
        for filename, folder in [
                ("Map.ut2", "Maps"), ("Mod.u", "System"), ("Tex.utx", "Textures"),
                ("Mesh.usx", "StaticMeshes"), ("Anim.ukx", "Animations"),
                ("Sound.uax", "Sounds"), ("Music.umx", "Music")]:
            self.assertTrue((self.install / folder / filename).is_file())
        self.assertEqual(source.read_bytes(), (self.install / "Maps" / "Map.ut2").read_bytes())
        self.assertTrue(source.exists())
        self.assertEqual(self.run_extract()[0], [])

    def test_dry_run_writes_nothing(self):
        self.cached("Map.ut2", ["Tex"])
        self.cached("Tex.utx")
        copied, _, _ = self.run_extract(dry_run=True)
        self.assertEqual(len(copied), 2)
        self.assertFalse((self.install / "Maps").exists())
        self.assertFalse((self.install / "Textures").exists())

    def test_missing_dependency_prevents_all_copies(self):
        self.cached("Map.ut2", ["Absent"])
        with self.assertRaisesRegex(ValueError, "not found in cache"):
            self.run_extract()
        self.assertFalse((self.install / "Maps").exists())

    def test_missing_cache_payload(self):
        self.cached("Map.ut2").unlink()
        with self.assertRaisesRegex(ValueError, "not found in cache"):
            self.run_extract()

    def test_ambiguous_cache_versions(self):
        self.cached("Map.ut2")
        self.cached("Map.ut2")
        with self.assertRaisesRegex(ValueError, "multiple cached versions"):
            self.run_extract()

    def test_conflict_does_not_overwrite(self):
        self.cached("Map.ut2")
        maps = self.install / "Maps"
        maps.mkdir()
        target = maps / "map.UT2"
        target.write_bytes(package(["Other"]))
        original = target.read_bytes()
        with self.assertRaisesRegex(ValueError, "differs from cache"):
            self.run_extract()
        self.assertEqual(target.read_bytes(), original)

    def test_identical_root_still_extracts_dependencies(self):
        source = self.cached("Map.ut2", ["Tex"])
        self.cached("Tex.utx")
        maps = self.install / "Maps"
        maps.mkdir()
        (maps / "MAP.ut2").write_bytes(source.read_bytes())
        copied, _, _ = self.run_extract()
        self.assertEqual(copied, [self.install / "Textures" / "Tex.utx"])

    def test_unsafe_cache_filename(self):
        with self.index.open("a") as stream:
            stream.write("%032X-1=../Map.ut2\n" % 1)
        with self.assertRaisesRegex(ValueError, "not a path"):
            self.run_extract()

    def test_unsafe_identifier(self):
        self.index.write_text("[Cache]\n../outside=Map.ut2\n")
        with self.assertRaisesRegex(ValueError, "invalid cache identifier"):
            self.run_extract()

    def test_malformed_package(self):
        self.cached("Map.ut2", data=b"broken")
        with self.assertRaisesRegex(ValueError, "malformed package"):
            self.run_extract()
        self.assertFalse((self.install / "Maps").exists())

    def test_ogg_is_not_parsed_as_package(self):
        self.index.write_text("[Cache]\n93E3E1C500300BE6_ogg=Song.ogg\n")
        (self.cache / "93E3E1C500300BE6_ogg.uxx").write_bytes(b"OggS")
        copied, _, count = self.run_extract("Song")
        self.assertEqual(count, 1)
        self.assertEqual(copied, [self.install / "Music" / "Song.ogg"])

    def test_extension_disambiguates_package_types(self):
        self.cached("Map.ut2")
        self.cached("Map.utx")
        copied, _, _ = self.run_extract("Map.ut2")
        self.assertEqual(copied, [self.install / "Maps" / "Map.ut2"])

    def test_shared_basename_installed_content_types(self):
        self.cached("Map.ut2", ["Shared"])
        for extension, folder, dep in [
                (".utx", "Textures", "TexDep"), (".usx", "StaticMeshes", "MeshDep")]:
            directory = self.install / folder
            directory.mkdir()
            (directory / ("Shared" + extension)).write_bytes(package([dep]))
            self.cached(dep + ".u")
        copied, _, count = self.run_extract()
        self.assertEqual(count, 5)
        self.assertEqual(len(copied), 3)

    def test_shared_basename_cached_content_types(self):
        self.cached("Map.ut2", ["Shared"])
        self.cached("Shared.utx")
        self.cached("Shared.usx")
        copied, _, count = self.run_extract()
        self.assertEqual(count, 3)
        self.assertEqual(len(copied), 3)


if __name__ == "__main__":
    unittest.main()
