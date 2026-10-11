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


def aura(name, spell_id=1):
    return f"SPELL_AURA_APPLIED,{ME},{ME},{spell_id},\"{name}\",0x40,BUFF"


# Arcane spec ID, then the buffs that mark Sunfury and the Season 2 4-piece.
SUNFURY_4PC = [(0.1, "COMBATANT_INFO,Player-1-00000001,1,271,424,39398,4239,0,62,[(1,2,1)],(0,0,0,0),[(1,2,(),(),())]"),
               (0.2, aura("Spellfire Sphere", 448604)), (0.3, aura("Cumulative Power", 1296930))]


def combatant(spec_id):
    return [(0.1, f"COMBATANT_INFO,Player-1-00000001,1,271,424,39398,4239,0,{spec_id},[(1,2,1)],(0,0,0,0),[]")]


def write_log(path, body, build=SUNFURY_4PC):
    """body: [(seconds after 20:00:00, event text)]; wraps it in one 60s boss encounter."""
    lines = [(0, 'ENCOUNTER_START,9999,"Training Dummy",15,20,1')] + build + body + [(60, 'ENCOUNTER_END,9999,"Training Dummy",15,20,1,60000')]
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


OTHER = 'Player-1-00000002,"Othermage-Realm-US",0x512,0x80000000'
SOUL_4PC = combatant(62) + [(0.2, aura("Arcane Soul", 451038)), (0.3, aura("Cumulative Power", 1296930))]
MISSILES = [(1.000, cc("SPELL_AURA_APPLIED")), (1.500, cast(5143, "Arcane Missiles"))]
THREE_MISSILES = [(t, cast(5143, "Arcane Missiles")) for t in (1.5, 5.0, 9.0)]


def two_pulls(path, first, second):
    """Two 60s boss pulls; each argument is [(seconds into the pull, event text)]."""
    with open(path, "w") as f:
        for minute, (boss, body) in enumerate((("Boss One", first), ("Boss Two", second))):
            lines = [(0, f'ENCOUNTER_START,{9990 + minute},"{boss}",15,20,1')] + body + \
                    [(59, f'ENCOUNTER_END,{9990 + minute},"{boss}",15,20,1,59000')]
            for t, text in lines:
                f.write(f"10/10/2026 20:{minute:02d}:{t:06.3f}-5  {text}\n")


class BuildGate(LogTest):
    def player(self, build, body=MISSILES):
        write_log(self.path, body, build=build)
        return json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]

    def test_supported_build_gets_spec_checks(self):
        for name, build in (("Spellfire Sphere", SUNFURY_4PC), ("Arcane Soul", SOUL_4PC)):
            with self.subTest(name):
                p = self.player(build)
                self.assertEqual(p["build"], {"spec_id": 62, "spec_name": "Arcane", "hero_tree": "Sunfury",
                                              "set_4pc": True, "supported": True, "unsupported_reason": None})
                self.assertIsNotNone(p["spec_checks"])
                self.assertTrue(p["timeline"][0]["matched"])
                self.assertIn("  Build: Arcane Sunfury, Season 2 4-piece\n", self.run_cli())

    def test_other_builds_get_generic_stats_only(self):
        sphere, cp = (0.2, aura("Spellfire Sphere", 448604)), (0.3, aura("Cumulative Power", 1296930))
        cases = (
            ("no 4-piece", combatant(62) + [sphere], "no_4pc", "Arcane Sunfury, no Season 2 4-piece"),
            ("no Sunfury buff", combatant(62) + [cp], "sunfury_not_detected", "Arcane, Sunfury not detected"),
            ("Fire", combatant(63) + [sphere, cp], "not_arcane", "Fire"),
            ("no COMBATANT_INFO", [sphere, cp], "spec_unknown", "spec unknown"),
            ("unrecognized spec ID", combatant(9999) + [sphere, cp], "spec_unknown", "spec unknown"),
        )
        for name, build, reason, text in cases:
            with self.subTest(name):
                p = self.player(build)
                self.assertEqual((p["build"]["supported"], p["build"]["unsupported_reason"]), (False, reason))
                self.assertIsNone(p["spec_checks"])
                self.assertNotIn("expected", p["timeline"][0])  # no priority verdicts
                self.assertEqual(p["abilities"], {"Arcane Missiles": 1})  # generic stats still there
                self.assertIn(f"  Build: {text} - spec coaching only supports Arcane Sunfury with the Season 2 4-piece",
                              self.run_cli())

    def test_signature_buffs_in_an_earlier_pull_count_for_a_later_one(self):
        two_pulls(self.path, SUNFURY_4PC, combatant(62) + [(1.0, cc("SPELL_AURA_APPLIED"))])  # pull 2: no casts
        pulls = json.loads(self.run_cli("--json"))["pulls"]
        self.assertEqual([p["players"][0]["build"]["supported"] for p in pulls], [True, True])

    def test_each_pull_is_judged_on_its_own_build(self):
        # Pull 2: three Missiles channels and no Cumulative Power, so the 4-piece was swapped off.
        two_pulls(self.path, SUNFURY_4PC + MISSILES, combatant(62) + THREE_MISSILES)
        builds = [p["players"][0]["build"] for p in json.loads(self.run_cli("--json"))["pulls"]]
        self.assertEqual([b["unsupported_reason"] for b in builds], [None, "no_4pc"])
        # Respec: pull 2 reports its own spec, and pull 1 keeps Arcane.
        two_pulls(self.path, SUNFURY_4PC + MISSILES, combatant(64) + MISSILES)
        builds = [p["players"][0]["build"] for p in json.loads(self.run_cli("--json"))["pulls"]]
        self.assertEqual([b["spec_name"] for b in builds], ["Arcane", "Frost Mage"])
        self.assertEqual([b["supported"] for b in builds], [True, False])

    def test_weak_or_missing_signals_fall_back_to_other_pulls(self):
        cp_refresh = f"SPELL_AURA_REFRESH,{ME},{ME},1296930,\"Cumulative Power\",0x40,BUFF"
        cases = (
            # One cancelled channel and no Cumulative Power is no proof the set was swapped off.
            ("one Missiles, no Cumulative Power", SUNFURY_4PC, combatant(62) + MISSILES, [True, True]),
            # Cumulative Power already up at the pull start only logs REFRESH/DOSE.
            ("Cumulative Power refresh only", combatant(62) + [(0.2, aura("Spellfire Sphere", 448604))],
             combatant(62) + [(0.2, aura("Spellfire Sphere", 448604)), (1.4, cp_refresh)] + THREE_MISSILES,
             [True, True]),
            # A pull before the first COMBATANT_INFO takes the first spec seen.
            ("no COMBATANT_INFO in pull 1", [(0.2, aura("Spellfire Sphere", 448604))] + MISSILES, SUNFURY_4PC,
             [True, True]),
        )
        for name, first, second, supported in cases:
            with self.subTest(name):
                two_pulls(self.path, first, second)
                builds = [p["players"][0]["build"] for p in json.loads(self.run_cli("--json"))["pulls"]]
                self.assertEqual([b["supported"] for b in builds], supported)

    def test_combatant_info_lines_dont_confuse_player_or_analysis(self):
        other = f"COMBATANT_INFO,Player-1-00000002,1,1,1,1,1,0,63,[(1,2,1)],(0,0,0,0),[]"
        aura_on_other = f"SPELL_AURA_APPLIED,{OTHER},{OTHER},1296930,\"Cumulative Power\",0x40,BUFF"
        with open(self.path, "w") as f:
            # A COMBATANT_INFO before any encounter, a truncated one, and another player's buffs and spec.
            f.write(f"10/10/2026 19:59:59.000-5  {other}\n10/10/2026 19:59:59.500-5  COMBATANT_INFO\n")
        body = [(0.05, other), (0.06, aura_on_other)] + MISSILES
        with open(self.path, "a") as f:
            for t, text in [(0, 'ENCOUNTER_START,9999,"Training Dummy",15,20,1')] + SUNFURY_4PC + body + \
                           [(60, 'ENCOUNTER_END,9999,"Training Dummy",15,20,1,60000')]:
                f.write(f"10/10/2026 20:{int(t // 60):02d}:{t % 60:06.3f}-5  {text}\n")
        p = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]
        self.assertEqual(p["name"], "Testmage-Realm-US")
        self.assertTrue(p["build"]["supported"])
        self.assertEqual(p["abilities"], {"Arcane Missiles": 1})
        other_p = json.loads(self.run_cli("--json", "--player", "Othermage"))["pulls"][0]["players"][0]
        self.assertEqual((other_p["build"]["spec_name"], other_p["build"]["unsupported_reason"]), ("Fire", "not_arcane"))


class NewestLog(unittest.TestCase):
    def setUp(self):
        # Hermetic: a WOWLOG_DIR on the developer's or CI machine must not leak in.
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def folder_with_log(self, name, log="WoWCombatLog-101026_192927.txt"):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        path = os.path.join(d, log)
        open(path, "w").close()
        return d, path

    def test_archived_logs_are_found_and_ordered_by_filename_date(self):
        d = self.tmp.name
        archive = os.path.join(d, "warcraftlogsarchive")
        os.mkdir(archive)
        older = os.path.join(archive, "Archive-WoWCombatLog-100926_210706.txt")
        newer = os.path.join(archive, "Archive-WoWCombatLog-101026_192927.txt")
        for p in (newer, older):  # archiving resets mtime, so the older log is touched last
            open(p, "w").close()
        os.utime(older, (2e9, 2e9))
        with mock.patch.object(wowlog, "LOG_DIRS", [os.path.join(d, "missing"), d]):
            self.assertEqual(wowlog.newest_log(), newer)

    def test_flag_beats_env_beats_defaults(self):
        flag_dir, flag_log = self.folder_with_log("flag")
        env_dir, env_log = self.folder_with_log("env")
        default_dir, _ = self.folder_with_log("default")
        with mock.patch.object(wowlog, "LOG_DIRS", [default_dir]):
            os.environ["WOWLOG_DIR"] = env_dir
            self.assertEqual(wowlog.newest_log(flag_dir), flag_log)
            self.assertEqual(wowlog.newest_log(), env_log)

    def test_cli_log_dir_flag_is_used(self):
        flag_dir, flag_log = self.folder_with_log("flag")
        write_log(flag_log, [(1.000, cc("SPELL_AURA_APPLIED"))])
        with mock.patch.object(wowlog, "newest_log", return_value=flag_log) as newest, \
                mock.patch.object(sys, "argv", ["wowlog.py", "--log-dir", flag_dir, "--list"]), \
                contextlib.redirect_stdout(io.StringIO()):
            wowlog.main()
        newest.assert_called_once_with(flag_dir)

    def test_folder_names_are_not_glob_patterns(self):
        d, log = self.folder_with_log("WoW [PTR]")
        self.assertEqual(wowlog.newest_log(d), log)

    def test_no_logs_lists_every_folder_searched(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.mkdir(empty)
        missing = os.path.join(self.tmp.name, "missing")
        with mock.patch.object(wowlog, "LOG_DIRS", [empty, missing]), self.assertRaises(SystemExit) as err:
            wowlog.newest_log()
        msg = str(err.exception)
        self.assertIn(f"{empty}\n", msg)
        self.assertIn(f"{missing} (does not exist)", msg)
        self.assertIn("--log-dir", msg)
        self.assertIn("WOWLOG_DIR", msg)


if __name__ == "__main__":
    unittest.main()
