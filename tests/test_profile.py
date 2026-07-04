#!/usr/bin/env python3
"""Tests for the device-profile system, using the packaged H60A6 profile.

Requires PyYAML (the profile loader dep). Run:  python3 tests/test_profile.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from govee_ble_local import messages, profile  # noqa: E402
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

    def test_protocol_defaults_absent_from_yaml(self):
        # device.yaml has no protocol: section -> Protocol() defaults, i.e.
        # today's original (only) behavior, byte-for-byte unchanged.
        self.assertEqual(self.profile.protocol, messages.Protocol())

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


class TestH6006Profile(unittest.TestCase):
    """H6006 - legacy plaintext bulb, the second real (not just decoded)
    device this library supports. See PROTOCOL.md §12."""

    def setUp(self):
        self.profile = profile.load_by_sku("H6006")
        self.assertIsNotNone(self.profile, "packaged H6006 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H6006")
        # Confirmed real advertised names from capture data.
        self.assertTrue(self.profile.matches_local_name("ihoment_H6006_0EEB"))
        self.assertTrue(self.profile.matches_local_name("ihoment_H6006_60AF"))
        self.assertFalse(self.profile.matches_local_name("GVH60A67457"))  # different generation's scheme
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities(self):
        cap = self.profile.capabilities
        self.assertTrue(cap.rgb)
        self.assertTrue(cap.brightness)
        self.assertEqual(cap.color_temp, (2700, 6500))
        self.assertEqual(cap.zones, ())
        self.assertEqual(cap.segments, 0)
        self.assertTrue(cap.scenes)

    def test_protocol(self):
        self.assertEqual(self.profile.protocol, messages.Protocol("none", "h6006", "none"))

    def test_scene_catalog_loaded(self):
        self.assertEqual(len(self.profile.scenes), 59)
        self.assertTrue(all(isinstance(s.code, int) for s in self.profile.scenes))

    def test_notes_loaded(self):
        self.assertIsNotNone(self.profile.notes)
        self.assertIn("plaintext", self.profile.notes.lower())


class TestH61A8Profile(unittest.TestCase):
    """H61A8 - 20-segment LED rope, the third real device, and the one that
    proves encryption/color-scheme/status-scheme are independent axes (it's
    plaintext like H6006 but shares H60A6's color-command layout). See
    PROTOCOL.md §13-§14."""

    def setUp(self):
        self.profile = profile.load_by_sku("H61A8")
        self.assertIsNotNone(self.profile, "packaged H61A8 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H61A8")
        self.assertTrue(self.profile.matches_local_name("Govee_H61A8_631F"))
        self.assertFalse(self.profile.matches_local_name("ihoment_H6006_0EEB"))
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities(self):
        cap = self.profile.capabilities
        self.assertTrue(cap.rgb)
        self.assertTrue(cap.brightness)
        self.assertIsNone(cap.color_temp)  # no confirmed capability on this SKU
        self.assertEqual(cap.zones, ())  # confirmed global (not zone) power
        self.assertEqual(cap.segments, 20)
        self.assertTrue(cap.scenes)

    def test_protocol(self):
        self.assertEqual(self.profile.protocol, messages.Protocol("handshake_only", "h60a6", "segment_fields"))

    def test_scene_catalog_loaded(self):
        self.assertEqual(len(self.profile.scenes), 149)
        self.assertTrue(all(isinstance(s.code, int) for s in self.profile.scenes))

    def test_notes_loaded(self):
        self.assertIsNotNone(self.profile.notes)
        self.assertIn("vestigial", self.profile.notes.lower())


class TestH6052Profile(unittest.TestCase):
    """H6052 - plain RGBWW bulb, H6006 color scheme. Initially
    mischaracterized as a segmented H60A6-color-scheme device due to a
    device-grouping bug (fixed); see PROTOCOL.md §13.1/§15.2."""

    def setUp(self):
        self.profile = profile.load_by_sku("H6052")
        self.assertIsNotNone(self.profile, "packaged H6052 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H6052")
        self.assertTrue(self.profile.matches_local_name("Govee_H6052_3477"))
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities(self):
        cap = self.profile.capabilities
        self.assertTrue(cap.rgb)
        self.assertEqual(cap.color_temp, (2000, 9000))  # unusually wide range, confirmed live
        self.assertEqual(cap.zones, ())
        self.assertEqual(cap.segments, 0)

    def test_protocol(self):
        self.assertEqual(self.profile.protocol, messages.Protocol("none", "h6006", "none"))

    def test_scene_catalog_loaded(self):
        self.assertEqual(len(self.profile.scenes), 43)


class TestH6008Profile(unittest.TestCase):
    """H6008 - plain RGBWW bulb, a fourth distinct protocol combination:
    real handshake ("vestigial", like H61A8) paired with H6006's color
    scheme rather than H60A6's. See PROTOCOL.md §15.1."""

    def setUp(self):
        self.profile = profile.load_by_sku("H6008")
        self.assertIsNotNone(self.profile, "packaged H6008 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H6008")
        self.assertTrue(self.profile.matches_local_name("GVH60082691"))
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities(self):
        cap = self.profile.capabilities
        self.assertTrue(cap.rgb)
        self.assertEqual(cap.color_temp, (2700, 6500))
        self.assertEqual(cap.zones, ())
        self.assertEqual(cap.segments, 0)

    def test_protocol(self):
        self.assertEqual(self.profile.protocol, messages.Protocol("handshake_only", "h6006", "none"))

    def test_scene_catalog_loaded(self):
        self.assertEqual(len(self.profile.scenes), 59)


class TestH5083Profile(unittest.TestCase):
    """H5083 - Govee's smart plug family: on/off only, and the first device
    needing a power_scheme other than 'binary'. See PROTOCOL.md §15.3."""

    def setUp(self):
        self.profile = profile.load_by_sku("H5083")
        self.assertIsNotNone(self.profile, "packaged H5083 profile should load")

    def test_identity_and_matching(self):
        self.assertEqual(self.profile.sku, "H5083")
        self.assertTrue(self.profile.matches_local_name("ihoment_H5083_A2D1"))
        self.assertFalse(self.profile.matches_local_name(None))

    def test_capabilities_are_minimal(self):
        cap = self.profile.capabilities
        self.assertFalse(cap.brightness)
        self.assertFalse(cap.rgb)
        self.assertIsNone(cap.color_temp)
        self.assertEqual(cap.zones, ())
        self.assertEqual(cap.segments, 0)
        self.assertFalse(cap.scenes)

    def test_protocol_uses_plug_relay_power_scheme(self) -> None:
        self.assertEqual(
            self.profile.protocol,
            messages.Protocol("handshake_only", "h6006", "none", "plug_relay"),
        )

    def test_no_scene_catalog(self):
        self.assertEqual(self.profile.scenes, ())


class TestSceneModel(unittest.TestCase):
    def test_scene_id_is_little_endian(self):
        # code 19074 = 0x4A82 -> (0x82, 0x4A)
        self.assertEqual(Scene("Aurora", 19074).scene_id, (0x82, 0x4A))


class TestRegistry(unittest.TestCase):
    def test_available_skus_includes_all_packaged_devices(self):
        skus = profile.available_skus()
        for sku in ("h60a6", "h6006", "h61a8", "h6052", "h6008", "h5083"):
            self.assertIn(sku, skus)

    def test_match_local_name(self):
        prof = profile.match_local_name("GVH60A6D075")
        self.assertIsNotNone(prof)
        self.assertEqual(prof.sku, "H60A6")

    def test_match_local_name_h6006(self):
        prof = profile.match_local_name("ihoment_H6006_0EEB")
        self.assertIsNotNone(prof)
        self.assertEqual(prof.sku, "H6006")

    def test_match_local_name_h61a8(self):
        prof = profile.match_local_name("Govee_H61A8_631F")
        self.assertIsNotNone(prof)
        self.assertEqual(prof.sku, "H61A8")

    def test_match_local_name_h5083(self):
        prof = profile.match_local_name("ihoment_H5083_A2D1")
        self.assertIsNotNone(prof)
        self.assertEqual(prof.sku, "H5083")

    def test_unknown_sku_returns_none(self):
        self.assertIsNone(profile.load_by_sku("NOPE99"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
