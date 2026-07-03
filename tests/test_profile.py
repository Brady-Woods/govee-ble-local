#!/usr/bin/env python3
"""Tests for the device-profile system, using the packaged H60A6 profile.

Requires PyYAML (the profile loader dep). Run:  python3 tests/test_profile.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import profile  # noqa: E402
from govee_ble_local.profile import Scene  # noqa: E402


class TestH60A6Profile(unittest.TestCase):
    def setUp(self):
        self.profile = profile.load_by_sku("H60A6")
        self.assertIsNotNone(self.profile, "packaged H60A6 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H60A6")
        self.assertTrue(self.profile.matches_local_name("GVH60A67457"))
        self.assertFalse(self.profile.matches_local_name("GVH5179ABCD"))
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities(self):
        cap = self.profile.capabilities
        self.assertTrue(cap.rgb)
        self.assertTrue(cap.brightness)
        self.assertEqual(cap.color_temp, (2700, 6500))
        self.assertEqual(cap.zones, ("upper", "lower"))
        self.assertEqual(cap.segments, 12)
        self.assertTrue(cap.scenes)

    def test_scene_catalog_loaded(self):
        self.assertEqual(len(self.profile.scenes), 84)
        # every scene has a code; most (but not all — some simple built-ins
        # have none) carry an upload param
        self.assertTrue(all(isinstance(s.code, int) for s in self.profile.scenes))
        self.assertGreaterEqual(sum(1 for s in self.profile.scenes if s.param), 70)

    def test_broken_scenes_flagged(self):
        broken = {s.name for s in self.profile.scenes if not s.working}
        self.assertEqual(
            broken,
            {"Aurora", "Dandelion", "Desert", "Fall", "Green Wheat Field",
             "Volcano", "Ocean", "Winter"},
        )

    def test_selectable_excludes_broken_and_is_sorted(self):
        selectable = self.profile.selectable_scenes()
        names = [s.name for s in selectable]
        self.assertNotIn("Aurora", names)
        self.assertNotIn("Ocean", names)
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertEqual(len(selectable), 84 - 8)

    def test_scene_lookup_case_insensitive(self):
        s = self.profile.scene_by_name("sUnRiSe")
        self.assertIsNotNone(s)
        self.assertEqual(s.name, "Sunrise")

    def test_notes_loaded(self):
        self.assertIsNotNone(self.profile.notes)
        self.assertIn("Govee Ceiling Light Pro", self.profile.notes)


class TestSceneModel(unittest.TestCase):
    def test_scene_id_is_little_endian(self):
        # code 19074 = 0x4A82 -> (0x82, 0x4A)
        self.assertEqual(Scene("Aurora", 19074).scene_id, (0x82, 0x4A))


class TestRegistry(unittest.TestCase):
    def test_available_skus_includes_h60a6(self):
        self.assertIn("h60a6", profile.available_skus())

    def test_match_local_name(self):
        prof = profile.match_local_name("GVH60A6D075")
        self.assertIsNotNone(prof)
        self.assertEqual(prof.sku, "H60A6")

    def test_unknown_sku_returns_none(self):
        self.assertIsNone(profile.load_by_sku("NOPE99"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
