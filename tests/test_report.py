"""report.py: the HTML must be self-contained, safe to embed arbitrary log/coaching text in, and render."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import report  # noqa: E402
import wowlog  # noqa: E402
from test_wowlog import ME, SUNFURY_4PC, aura, cast, cc, write_log  # noqa: E402

# Every per-player key the template reads; the fixture comes from wowlog itself, so drift fails here.
PLAYER_KEYS = {"name", "guid", "build", "time_alive", "died_at", "casts", "casts_per_minute", "idle", "abilities",
               "failed_casts", "spec_checks", "windows", "series", "timeline"}
HOSTILE = ["</script><script>alert(1)</script>", "</SCRIPT>", "</script ", "<!--<script>", "-->", "<script>",
           "  ", "{{COACHING}}", "{{REPORT}}", "{{TITLE}}", "&lt;&amp;"]


def real_report(name_suffix=""):
    """wowlog --json for a synthetic supported-build pull with windows, series and verdicts."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "WoWCombatLog-101026_200000.txt")
        write_log(path, [
            (1.0, aura("Arcane Surge", 365362)), (1.2, cc("SPELL_AURA_APPLIED")),
            (1.5, cast(5143, "Arcane Missiles")), (2.0, cc("SPELL_AURA_APPLIED_DOSE", 2)),
            (2.2, aura("Arcane Salvo", 1242974)),
            (2.6, f"SPELL_AURA_APPLIED_DOSE,{ME},{ME},1242974,\"Arcane Salvo\",0x40,BUFF,6"),
            (4.0, cast(44425, "Arcane Barrage")), (6.0, cast(30451, "Arcane Blast")),
            (18.0, aura("Arcane Surge", 365362).replace("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED")),
        ])
        if name_suffix:
            text = open(path).read().replace("Testmage-Realm-US", f"Testmage{name_suffix}-Realm-US")
            open(path, "w").write(text)
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["wowlog.py", path, "--json"]), contextlib.redirect_stdout(out):
            wowlog.main()
    return json.loads(out.getvalue())


def coaching(text="Keep Salvo high."):
    return {"headline": text, "habits": [{"title": text, "what": text, "instead": text, "why": text,
            "moments": [{"pull": 1, "t": 4.0, "note": text}]}], "pulls": {"1": text}, "caveats": [text]}


def embedded(page, element_id):
    m = re.search(rf'<script type="application/json" id="{element_id}">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def template():
    with open(report.TEMPLATE, encoding="utf-8") as f:
        return f.read()


class Contract(unittest.TestCase):
    def test_fixture_is_real_wowlog_output_with_every_key_the_template_reads(self):
        p = real_report()["pulls"][0]["players"][0]
        self.assertEqual(set(p), PLAYER_KEYS)
        self.assertTrue(p["build"]["supported"])
        self.assertEqual(set(p["series"]), {"salvo", "clearcasting"})
        self.assertIn("Arcane Surge", p["windows"])
        self.assertIn("matched", p["timeline"][0])


class Embedding(unittest.TestCase):
    def test_no_text_can_end_or_confuse_the_data_elements(self):
        script_tags = template().count("</script>")
        for hostile in HOSTILE:
            with self.subTest(hostile=hostile):
                r = real_report()
                r["pulls"][0]["boss"] = hostile
                r["pulls"][0]["players"][0]["timeline"][0]["spell"] = hostile
                page = report.render(r, coaching(hostile))
                self.assertEqual(page.count("</script>"), script_tags)
                self.assertNotRegex(page.split("<script>", 1)[0], "(?i)<!--")  # nothing opens a comment early
                for element in ("report-data", "coaching-data"):
                    self.assertNotRegex(re.search(rf'id="{element}">(.*?)</script>', page, re.S).group(1), "[<>&]")
                self.assertEqual(embedded(page, "report-data")["pulls"][0]["boss"], hostile)
                self.assertEqual(embedded(page, "coaching-data")["headline"], hostile)

    def test_placeholders_in_names_are_not_expanded(self):
        page = report.render(real_report(name_suffix="{{REPORT}}"), coaching("{{COACHING}}"))
        title = re.search("<title>(.*?)</title>", page, re.S).group(1)
        self.assertEqual(title, "Testmage{{REPORT}} - 2026-10-10")
        self.assertEqual(embedded(page, "coaching-data")["headline"], "{{COACHING}}")

    def test_title_is_escaped(self):
        page = report.render(real_report(name_suffix="<b>"))
        self.assertIn("<title>Testmage&lt;b&gt; - 2026-10-10</title>", page)


class SelfContained(unittest.TestCase):
    def test_template_loads_nothing_from_the_network(self):
        page = template()
        self.assertIn("default-src 'none'", page)  # Content-Security-Policy: the browser enforces it
        self.assertEqual(set(re.findall(r'https?://[^\s"\'<>)]+', page)), {"http://www.w3.org/2000/svg"})
        for pattern in (r"\bfetch\(", r"XMLHttpRequest", r"sendBeacon", r"WebSocket", r"EventSource", r"new Image",
                        r"\bimport\(", r"srcset", r"@font-face", r"@import", r"url\(", r"<(link|img|iframe|object|base|form)\b",
                        r"""(href|src|action|data|poster)\s*=\s*["']?(https?:)?//"""):
            self.assertNotRegex(page, pattern)

    def test_coaching_is_optional(self):
        page = report.render(real_report())
        self.assertIsNone(embedded(page, "coaching-data"))
        self.assertNotRegex(page, r"\{\{(TITLE|REPORT|COACHING)\}\}")


class Cli(unittest.TestCase):
    def test_reads_stdin_and_writes_the_output_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            stdin = io.StringIO(json.dumps(real_report()))
            with mock.patch.object(sys, "argv", ["report.py", "-", "-o", out]), mock.patch.object(sys, "stdin", stdin), \
                    contextlib.redirect_stdout(io.StringIO()):
                report.main()
            self.assertIn('id="report-data"', open(out, encoding="utf-8").read())


def find_browser():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if os.path.exists(mac) else None


@unittest.skipUnless(find_browser(), "no Chrome/Chromium to run the page in")
class Renders(unittest.TestCase):
    """Runs the JS renderer in headless Chrome; skipped where there is no browser."""

    def render_dom(self, r, c=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(report.render(r, c))
            done = subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--dump-dom",
                                   "--virtual-time-budget=3000", f"file://{path}#pull-1@4"],
                                  capture_output=True, text=True, timeout=60)
        return done.stdout

    def test_supported_report_renders_every_section(self):
        dom = self.render_dom(real_report(), coaching())
        self.assertIn('data-rendered="1"', dom)
        self.assertIn("<svg", dom)
        self.assertIn("Habit 1", dom)
        self.assertIn("Keep Salvo high.", dom)

    def test_hostile_text_still_renders(self):
        r = real_report()
        r["pulls"][0]["boss"] = "<!--<script>"
        dom = self.render_dom(r, coaching("</script><img src=x onerror=alert(1)>"))
        self.assertIn('data-rendered="1"', dom)
        self.assertNotIn("<img", dom)


if __name__ == "__main__":
    unittest.main()
