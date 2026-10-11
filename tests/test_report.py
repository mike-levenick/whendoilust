"""report.py: the HTML must be self-contained and safe to embed arbitrary report text in."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import report  # noqa: E402


def minimal_report(boss="Training Dummy"):
    player = {"name": "Testmage-Realm-US", "build": {"supported": False, "unsupported_reason": "not_arcane",
              "spec_id": 63, "spec_name": "Fire", "hero_tree": None, "set_4pc": False},
              "time_alive": 60, "died_at": None, "casts": 1, "casts_per_minute": 1.0,
              "idle": {"threshold": 2.0, "total": 0, "gaps": []}, "abilities": {"Fireball": 1},
              "failed_casts": [], "spec_checks": None, "windows": {}, "series": {},
              "timeline": [{"t": 1.0, "spell": "Fireball", "state": {}}]}
    return {"schema": 1, "log": "WoWCombatLog-101026_200000.txt", "log_started": "2026-10-10T20:00:00",
            "pulls": [{"number": 1, "boss": boss, "encounter_id": 1, "difficulty": "Heroic", "result": "kill",
                       "start": "2026-10-10T20:00:00", "duration": 60, "label": f"{boss} (Heroic) - KILL, 60s",
                       "players": [player]}]}


def embedded(page, element_id):
    m = re.search(rf'<script type="application/json" id="{element_id}">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


class Render(unittest.TestCase):
    def test_report_text_cannot_end_the_script_element(self):
        hostile = "</script><script>alert(1)</script>"
        page = report.render(minimal_report(boss=hostile), {"headline": hostile})
        self.assertNotIn(hostile, page)
        self.assertEqual(embedded(page, "report-data")["pulls"][0]["boss"], hostile)
        self.assertEqual(embedded(page, "coaching-data")["headline"], hostile)

    def test_page_is_self_contained(self):
        page = report.render(minimal_report())
        # The only URL allowed is the SVG namespace identifier, which is never fetched.
        urls = set(re.findall(r'https?://[^\s"\'<>)]+', page))
        self.assertEqual(urls, {"http://www.w3.org/2000/svg"})
        self.assertNotRegex(page, r'<(link|img|iframe)\b|\bsrc=|@import|url\(')

    def test_coaching_is_optional(self):
        page = report.render(minimal_report())
        self.assertIsNone(embedded(page, "coaching-data"))
        self.assertNotIn("{{", page)

    def test_title_is_escaped(self):
        r = minimal_report()
        r["pulls"][0]["players"][0]["name"] = "<b>Mage</b>-Realm-US"
        page = report.render(r)
        self.assertIn("<title>&lt;b&gt;Mage&lt;/b&gt; - 2026-10-10</title>", page)


if __name__ == "__main__":
    unittest.main()
