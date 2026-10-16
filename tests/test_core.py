from __future__ import annotations

import unittest
from pc_booster import state, sentinel, cleaner_pro, specs, tweaks, localization


class TestPCBoosterCore(unittest.TestCase):
    def test_state_defaults(self):
        s = state.get_settings()
        self.assertIn("whitelist", s)
        self.assertGreater(len(s["whitelist"]), 0)
        self.assertTrue(s.get("auto_pilot_enabled"))

    def test_localization(self):
        en_title = localization.tr("app_title", "en")
        ar_title = localization.tr("app_title", "ar")
        self.assertEqual(en_title, "PC Booster Pro")
        self.assertIn("PC Booster", ar_title)

    def test_specs(self):
        sp = specs.get_full_system_specs()
        self.assertIn("cpu", sp)
        self.assertIn("memory", sp)
        self.assertIn("gpus", sp)
        self.assertIn("drives", sp)
        self.assertIn("os", sp)

    def test_tweaks_states(self):
        tw = tweaks.current_states()
        self.assertGreaterEqual(len(tw), 5)

    def test_cleaner_categories(self):
        cats = cleaner_pro.scan_clean_categories()
        self.assertGreaterEqual(len(cats), 5)
        names = [c.name for c in cats]
        self.assertIn("User Temporary Files", names)

    def test_sentinel_manual_boost(self):
        sen = sentinel.AutoPilotSentinel()
        sen.start()
        stats = sen.get_stats()
        self.assertTrue(stats["enabled"])
        res = sen.manual_quick_boost()
        self.assertIn("ram_freed_bytes", res)
        self.assertIn("apps_trimmed", res)
        sen.stop()


if __name__ == "__main__":
    unittest.main()
