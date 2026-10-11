"""Tests against small synthetic logs. Real logs are hundreds of MB and never committed."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wowlog  # noqa: E402

ME = 'Player-1-00000001,"Testmage-Realm-US",0x511,0x80000000'
BOSS = 'Creature-0-1-1-1-1-0000000001,"Training Dummy",0x10a48,0x80000000'


def cc(event, stacks=None):
    line = f"{event},{ME},{ME},263725,\"Clearcasting\",0x1,BUFF"
    return line + (f",{stacks}" if stacks is not None else "")


def cast(spell_id, name):
    return f"SPELL_CAST_SUCCESS,{ME},{BOSS},{spell_id},\"{name}\",0x40,Player-1-00000001,0000000000000000,78"


def write_log(path, body):
    """body: [(seconds after 20:00:00, event text)]; wraps it in one 60s boss encounter."""
    lines = [(0, 'ENCOUNTER_START,9999,"Training Dummy",15,20,1')] + body + [(60, 'ENCOUNTER_END,9999,"Training Dummy",15,20,1,60000')]
    with open(path, "w") as f:
        for t, text in lines:
            f.write(f"10/10/2026 20:{int(t // 60):02d}:{t % 60:06.3f}-5  {text}\n")


class LogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "WoWCombatLog-101026_200000.txt")

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["wowlog.py", self.path, *args]), contextlib.redirect_stdout(out):
            wowlog.main()
        return out.getvalue()


class ClearcastingWaste(LogTest):
    def test_only_a_lone_refresh_at_cap_is_waste(self):
        # Mirrors the real log: WoW logs a REFRESH at the same timestamp as every stack change.
        write_log(self.path, [
            (1.000, cc("SPELL_AURA_APPLIED")),
            (2.000, cc("SPELL_AURA_APPLIED_DOSE", 2)),
            (3.000, cc("SPELL_AURA_APPLIED_DOSE", 3)),
            (3.000, cc("SPELL_AURA_REFRESH")),           # paired with the gain to 3: not waste
            (4.000, cc("SPELL_AURA_REFRESH")),           # gained at 3 stacks: wasted
            (5.000, cast(5143, "Arcane Missiles")),
            (5.000, cc("SPELL_AURA_REMOVED_DOSE", 2)),
            (5.000, cc("SPELL_AURA_REFRESH")),           # paired with the spend: not waste
            (6.000, cc("SPELL_AURA_REFRESH")),           # at 2 stacks: not waste
        ])
        report = json.loads(self.run_cli("--json"))
        spec = report["pulls"][0]["players"][0]["spec_checks"]
        self.assertEqual([w["t"] for w in spec["clearcasting"]["wasted"]], [4.0])
        self.assertEqual(spec["clearcasting"]["gained"], 4)


class Output(LogTest):
    def test_text_and_json_describe_the_same_pull(self):
        write_log(self.path, [(1.000, cc("SPELL_AURA_APPLIED")), (1.500, cast(5143, "Arcane Missiles"))])
        report = json.loads(self.run_cli("--json"))
        self.assertEqual(report["schema"], 1)
        pull = report["pulls"][0]
        self.assertEqual((pull["boss"], pull["result"], pull["difficulty"]), ("Training Dummy", "kill", "Heroic"))
        player = pull["players"][0]
        self.assertEqual(player["name"], "Testmage-Realm-US")
        self.assertEqual(player["timeline"][0]["state"]["clearcasting"], 1)
        self.assertTrue(player["timeline"][0]["matched"])  # CC up, Salvo 0 -> Missiles is correct
        text = self.run_cli()
        self.assertIn("=== Training Dummy (Heroic) - KILL, 60s  [Testmage]", text)
        self.assertIn("Matched the SimC priority on 1/1 rotational casts (100%)", text)


class NewestLog(unittest.TestCase):
    def test_archived_logs_are_found_and_ordered_by_filename_date(self):
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, "warcraftlogsarchive")
            os.mkdir(archive)
            older = os.path.join(archive, "Archive-WoWCombatLog-100926_210706.txt")
            newer = os.path.join(archive, "Archive-WoWCombatLog-101026_192927.txt")
            for p in (newer, older):  # archiving resets mtime, so the older log is touched last
                open(p, "w").close()
            os.utime(older, (2e9, 2e9))
            with mock.patch.object(wowlog, "LOG_DIR", d):
                self.assertEqual(wowlog.newest_log(), newer)


if __name__ == "__main__":
    unittest.main()
