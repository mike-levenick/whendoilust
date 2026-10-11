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


class CoachingCheck(unittest.TestCase):
    def test_grounded_coaching_passes(self):
        self.assertEqual(report.check_coaching(real_report(), coaching()), [])
        self.assertEqual(report.check_coaching(real_report(), None), [])

    def test_ungrounded_coaching_is_flagged(self):
        bad = coaching()
        bad["habits"][0]["moments"] = [{"pull": 1, "t": 30.0}, {"pull": 9, "t": 4.0}, {"pull": 1}]
        bad["habits"] *= 4
        bad["pulls"] = {"1": "ok", "7": "no such pull"}
        problems = report.check_coaching(real_report(), bad)
        self.assertIn("4 habits; the report shows the top 3 only", problems)
        self.assertIn("habit 1: nothing in pull #1 within 0.05s of t=30.0", problems)
        self.assertIn("habit 1: no pull #9 in this report", problems)
        self.assertIn("habit 1: nothing in pull #1 within 0.05s of t=None", problems)
        self.assertIn("pull note for #7, which isn't in this report", problems)

    def test_tolerance_boundary_and_types(self):
        # The fixture's Barrage is at exactly 4.0s, and nothing else is within 0.6s of it.
        cases = ((4.0, True), (4.05, True), (3.95, True), (4.06, False), (4.5, False), (True, False), ("4", False),
                 (float("nan"), False), (10 ** 400, False))
        for t, ok in cases:
            with self.subTest(t=t):
                c = coaching()
                c["habits"][0]["moments"] = [{"pull": 1, "t": t}]
                self.assertEqual(report.check_coaching(real_report(), c) == [], ok)

    def test_moments_may_cite_any_time_the_report_contains(self):
        r = real_report()
        p = r["pulls"][0]["players"][0]
        p["spec_checks"]["clearcasting"]["wasted"].append({"t": 33.3, "in_soul": False})  # a proc, not a cast
        for t in (33.3, p["windows"]["Arcane Surge"][0]["start"], p["idle"]["gaps"][-1]["t"]):
            with self.subTest(t=t):
                c = coaching()
                c["habits"][0]["moments"] = [{"pull": 1, "t": t}]
                self.assertEqual(report.check_coaching(r, c), [])

    def test_moments_are_checked_against_the_named_player_and_their_build(self):
        r = real_report()
        other = json.loads(json.dumps(r["pulls"][0]["players"][0]))
        other["name"], other["timeline"] = "Zaffira-Realm-US", [dict(other["timeline"][0], t=50.0)]
        r["pulls"][0]["players"].append(other)
        c = coaching()
        c["habits"][0]["moments"] = [{"pull": 1, "t": 50.0, "player": "Zaffira"}]
        self.assertEqual(report.check_coaching(r, c), [])
        c["habits"][0]["moments"] = [{"pull": 1, "t": 50.0}]  # not one of the main player's times
        self.assertEqual(report.check_coaching(r, c), ["habit 1: nothing in pull #1 within 0.05s of t=50.0"])
        c["habits"][0]["moments"] = [{"pull": 1, "t": 4.0, "player": "Nobody"}]
        self.assertEqual(report.check_coaching(r, c), ["habit 1: no player 'Nobody' in pull #1"])
        r["pulls"][0]["players"][0]["build"].update(supported=False, unsupported_reason="no_4pc")
        c["habits"][0]["moments"] = [{"pull": 1, "t": 4.0}]
        self.assertIn("habit 1: pull #1 has no spec coaching for Testmage-Realm-US (no_4pc)",
                      report.check_coaching(r, c))

    def test_habits_for_an_unsupported_build_are_flagged(self):
        r = real_report()
        r["pulls"][0]["players"][0]["build"]["supported"] = False
        self.assertIn("habits written for a build without spec coaching (build.supported is false in every pull)",
                      report.check_coaching(r, coaching()))

    def test_malformed_coaching_is_reported_not_raised(self):
        shapes = {
            "not an object": [1], "habits a string": {"habits": "abc"}, "habit null": {"habits": [None]},
            "moments of strings": {"habits": [{"moments": ["x"]}]}, "moments a string": {"habits": [{"moments": "x"}]},
            "pull a list": {"habits": [{"moments": [{"pull": [1], "t": 4.0}]}]},
            "pull a bool": {"habits": [{"moments": [{"pull": True, "t": 4.0}]}]},
            "pulls a list": {"pulls": [1]}, "pulls a string": {"pulls": "ab"},
            "unicode digit key": {"pulls": {"\u00b2": "x"}}, "zero-padded key": {"pulls": {"01": "x"}},
            "headline a list": {"headline": ["x"]},
            "caveats a string": {"caveats": "x"},
        }
        for name, c in shapes.items():
            with self.subTest(name):
                problems = report.check_coaching(real_report(), c)
                self.assertTrue(problems)
                self.assertTrue(all(isinstance(p, str) for p in problems))
        r = real_report()
        r["pulls"][0]["players"] = []
        self.assertEqual(report.check_coaching(r, {"habits": [{"moments": [{"pull": 1, "t": 4.0}]}]})[0],
                         "habits written for a build without spec coaching (build.supported is false in every pull)")

    def run_main(self, c, *flags):
        with tempfile.TemporaryDirectory() as d:
            rp, cp, html = os.path.join(d, "r.json"), os.path.join(d, "c.json"), os.path.join(d, "r.html")
            with open(rp, "w") as f:
                json.dump(real_report(), f)
            with open(cp, "w") as f:
                json.dump(c, f)
            argv = ["report.py", rp, "--coaching", cp, "-o", html, *flags]
            code = 0
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()) as err, \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    report.main()
                except SystemExit as e:
                    code = e.code
            return code, err.getvalue(), os.path.exists(html)

    def test_strict_fails_without_writing_and_lenient_warns_and_writes(self):
        bad = coaching()
        bad["habits"][0]["moments"] = [{"pull": 1, "t": 30.0}]
        code, err, wrote = self.run_main(bad, "--strict")
        self.assertEqual((code, wrote), (1, False))
        self.assertIn("coaching: habit 1: nothing in pull #1 within 0.05s of t=30.0", err)
        code, err, wrote = self.run_main(bad)
        self.assertEqual((code, wrote), (0, True))
        self.assertIn("coaching: habit 1", err)
        self.assertEqual(self.run_main(coaching(), "--strict"), (0, "", True))


class Cli(unittest.TestCase):
    def test_text_matches_wowlog_for_the_same_json(self):
        r = real_report()
        with tempfile.TemporaryDirectory() as d:
            rp = os.path.join(d, "r.json")
            with open(rp, "w") as f:
                json.dump(r, f)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["report.py", rp, "--text"]), contextlib.redirect_stdout(out):
                report.main()
        expected = io.StringIO()
        with contextlib.redirect_stdout(expected):
            for pull in r["pulls"]:
                for p in pull["players"]:
                    wowlog.print_report(pull["label"], p)
        self.assertEqual(out.getvalue(), f"Log: {r['log']}\n" + expected.getvalue())
        self.assertIn("Matched the SimC priority", out.getvalue())

    def test_unreadable_input_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.json")
            for content, message in (("{not json", "Couldn't read the report file"), ("[1]", "isn't a wowlog.py --json")):
                with self.subTest(content=content):
                    with open(bad, "w") as f:
                        f.write(content)
                    with mock.patch.object(sys, "argv", ["report.py", bad]), self.assertRaises(SystemExit) as err:
                        report.main()
                    self.assertIn(message, str(err.exception))

    def test_text_for_an_empty_selection_says_so(self):
        r = dict(real_report(), pulls=[])
        with tempfile.TemporaryDirectory() as d:
            rp = os.path.join(d, "r.json")
            with open(rp, "w") as f:
                json.dump(r, f)
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["report.py", rp, "--text"]), contextlib.redirect_stdout(out):
                report.main()
        self.assertIn("No boss encounters in this log yet.", out.getvalue())

    def test_skill_only_uses_flags_the_scripts_accept(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        skill = open(os.path.join(root, ".claude", "skills", "coach", "SKILL.md"), encoding="utf-8").read()
        accepted = set()
        for script in ("wowlog.py", "report.py"):
            accepted |= set(re.findall(r'add_argument\((?:"-\w", )?"(--[a-z-]+)"', open(os.path.join(root, script)).read()))
        used = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", skill)) - {"--show-toplevel"}
        self.assertTrue(used)
        self.assertEqual(used - accepted, set())
        self.assertTrue(os.path.exists(os.path.join(root, "docs", "coaching.md")))


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
