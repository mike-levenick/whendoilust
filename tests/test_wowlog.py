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


class Windows(LogTest):
    def test_buff_and_debuff_windows_and_stack_series(self):
        touch_on_boss = lambda ev: f"{ev},{ME},{BOSS},210824,\"Touch of the Magi\",0x40,DEBUFF"
        write_log(self.path, [
            (2.0, aura("Arcane Surge", 365362)),
            (2.5, touch_on_boss("SPELL_AURA_APPLIED")),
            (3.0, cc("SPELL_AURA_APPLIED")),
            (4.0, cc("SPELL_AURA_APPLIED_DOSE", 2)),
            (4.0, cc("SPELL_AURA_REFRESH")),            # no stack change: not a new series point
            (14.5, touch_on_boss("SPELL_AURA_REMOVED")),
            (19.4, aura("Arcane Surge", 365362).replace("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED")),
            (50.0, aura("Time Warp", 80353)),            # still up when the pull ends
        ])
        p = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]
        self.assertEqual(p["windows"]["Arcane Surge"], [{"start": 2.0, "end": 19.4}])
        self.assertEqual(p["windows"]["Touch of the Magi"], [{"start": 2.5, "end": 14.5}])
        self.assertEqual(p["windows"]["Lust"], [{"start": 50.0, "end": 60.0}])
        self.assertEqual(p["series"]["clearcasting"], [[3.0, 1], [4.0, 2]])


    def test_window_edge_cases(self):
        removed = lambda line: line.replace("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED")
        boss2 = 'Creature-0-1-1-1-1-0000000002,"Training Add",0x10a48,0x80000000'
        touch = lambda ev, target=BOSS, src=ME: f"{ev},{src},{target},210824,\"Touch of the Magi\",0x40,DEBUFF"
        other_mage = 'Player-1-00000002,"Othermage-Realm-US",0x512,0x80000000'
        write_log(self.path, [
            (3.0, removed(aura("Time Warp", 80353))),                # up before the pull: only REMOVED logged
            (5.0, aura("Bloodlust", 2825)), (6.0, aura("Primal Rage", 264667)),
            (9.0, removed(aura("Bloodlust", 2825))),                 # Primal Rage is still up
            (20.0, removed(aura("Primal Rage", 264667))),
            (10.0, touch("SPELL_AURA_APPLIED")),
            (12.0, touch("SPELL_AURA_APPLIED", target=boss2)),       # re-applied before the first target's REMOVED
            (13.0, touch("SPELL_AURA_REMOVED")),
            (24.0, touch("SPELL_AURA_REMOVED", target=boss2)),
            (15.0, touch("SPELL_AURA_APPLIED", src=other_mage)),     # someone else's Touch: not ours
            (30.0, aura("Arcane Soul", 451038)), (34.0, removed(aura("Arcane Soul", 451038))),
            (36.0, removed(aura("Fury of the Aspects", 390386))),   # also up before the pull, though Lust has spans now
        ])
        p = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]
        self.assertEqual(p["windows"]["Lust"], [{"start": 0.0, "end": 3.0}, {"start": 5.0, "end": 9.0},
                                                {"start": 6.0, "end": 20.0}, {"start": 0.0, "end": 36.0}])
        self.assertEqual(p["windows"]["Touch of the Magi"], [{"start": 10.0, "end": 12.0}, {"start": 12.0, "end": 24.0}])
        self.assertEqual(p["windows"]["Arcane Soul"], [{"start": 30.0, "end": 34.0}])

    def test_nothing_after_death_is_drawn(self):
        died = f"UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-1-00000001,\"Testmage-Realm-US\",0x511,0x80000000,0"
        write_log(self.path, [
            (2.0, aura("Arcane Surge", 365362)), (3.0, cc("SPELL_AURA_APPLIED")), (20.0, died),
            (40.0, aura("Arcane Soul", 451038)), (41.0, cc("SPELL_AURA_APPLIED_DOSE", 2)),  # after a battle res
        ])
        p = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]
        self.assertEqual(p["died_at"], 20.0)
        self.assertEqual(p["windows"], {"Arcane Surge": [{"start": 2.0, "end": 20.0}]})
        self.assertEqual(p["series"]["clearcasting"], [[3.0, 1]])


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


PET = 'Creature-0-1-1-1-31216-0000000009,"Mirror Image",0x2111,0x80000000'
PHOENIX = 'Creature-0-1-1-1-223453-0000000008,"Arcane Phoenix",0x2111,0x80000000'
PAL = 'Player-1-00000003,"Paladin-Realm-US",0x512,0x80000000'


def hit(src, dst, name, amount, absorbed=0, crit=False, event="SPELL_DAMAGE", overkill=-1):
    """A damage line laid out like a real 12.x advanced log (42 fields)."""
    return (f'{event},{src},{dst},1,"{name}",0x40,Creature-0-1-1-1-1-0000000001,0000000000000000,'
            f'100,100,0,0,1470,0,0,0,1,0,0,0,0.00,0.00,1038,0.0,90,'
            f'{amount},{amount},{overkill},64,0,0,{absorbed},{"1" if crit else "nil"},nil,nil,ST')


GUARDIAN = 'Creature-0-1-1-1-99999-0000000007,"Spark",0x2111,0x80000000'
LOOP_A = 'Creature-0-1-1-1-99998-0000000005,"A",0xa48,0x80000000'
LOOP_B = 'Creature-0-1-1-1-99997-0000000006,"B",0xa48,0x80000000'


def swing(src, dst, amount, crit=False):
    """A melee line as 12.x logs it: no spell fields and no trailing ST/AOE (38 fields)."""
    return (f'SWING_DAMAGE,{src},{dst},Creature-0-1-1-1-1-0000000001,0000000000000000,'
            f'100,100,0,0,1470,0,0,0,1,0,0,0,0.00,0.00,1038,0.0,90,'
            f'{amount},{amount},-1,1,0,0,0,{"1" if crit else "nil"},nil,nil')


class Damage(LogTest):
    def write(self, body, before=()):
        """Like write_log, with lines before ENCOUNTER_START (e.g. a pre-pull summon)."""
        write_log(self.path, body)
        lines = open(self.path).read().splitlines()
        pre = [f"10/10/2026 19:59:{59 - i:06.3f}-5  {text}" for i, text in enumerate(before)]
        open(self.path, "w").write("\n".join(pre + lines) + "\n")

    def test_player_and_pet_damage_is_counted_once(self):
        self.write([
            (2.0, hit(ME, BOSS, "Arcane Barrage", 1000, absorbed=200, crit=True)),
            (2.5, hit(ME, BOSS, "Arcane Missiles", 300, event="SPELL_PERIODIC_DAMAGE")),
            (2.6, hit(ME, BOSS, "Arcane Missiles", 900, overkill=900)),   # all overkill: nothing done
            (3.2, f'SPELL_MISSED,{ME},{BOSS},1,"Arcane Barrage",0x40,ABSORB,nil,500,500,nil,ST'),
            (3.3, f'SPELL_MISSED,{ME},{BOSS},1,"Arcane Missiles",0x40,IMMUNE,nil,AOE'),
            (4.0, hit(ME, ME, "Refraction", 9999)),                       # damage to yourself
            (4.1, hit(PAL, BOSS, "Judgment", 9999)),                      # someone else
            (5.0, hit(PET, BOSS, "Frostbolt", 50)),                       # summoned before the pull
            (6.0, f"SPELL_SUMMON,{ME},{PHOENIX},448659,\"Arcane Phoenix\",0x44"),
            (6.5, hit(PHOENIX, BOSS, "Arcane Barrage", 70)),               # same spell name as yours
            (7.0, "SPELL_DAMAGE,Player-1-00000001,trunc"),                # a line still being written
        ], before=[f"SPELL_SUMMON,{ME},{PET},321686,\"Mirror Image\",0x1"])
        d = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]["damage"]
        self.assertEqual(d["total"], 1200 + 300 + 500 + 50 + 70)
        self.assertEqual(d["dps"], round(2120 / 60))
        self.assertEqual(d["by_spell"], [
            {"spell": "Arcane Barrage", "pet": None, "amount": 1700, "hits": 2, "crits": 1},
            {"spell": "Arcane Missiles", "pet": None, "amount": 300, "hits": 2, "crits": 0},
            {"spell": "Arcane Barrage", "pet": "Arcane Phoenix", "amount": 70, "hits": 1, "crits": 0},
            {"spell": "Frostbolt", "pet": "Mirror Image", "amount": 50, "hits": 1, "crits": 0},
        ])
        self.assertEqual(len(d["per_second"]), 61)
        self.assertEqual(d["per_second"][2:7], [1500, 500, 0, 50, 70])
        self.assertIn("Damage: 2.1K total, 35 DPS  (Arcane Barrage 80%", self.run_cli())

    def test_melee_summon_chains_and_short_lines(self):
        self.write([
            (2.0, swing(PET, BOSS, 400, crit=True)),
            (2.1, f"SWING_MISSED,{PET},{BOSS},ABSORB,nil,25,25,nil"),
            (3.0, f"SPELL_SUMMON,{PET},{GUARDIAN},1,\"Spark\",0x1"),     # the pet's own summon
            (3.1, hit(GUARDIAN, BOSS, "Zap", 10)),
            (4.0, f"SPELL_SUMMON,{GUARDIAN},{GUARDIAN},1,\"Again\",0x1"),  # doesn't change who owns it
            (4.1, hit(GUARDIAN, BOSS, "Zap", 10)),
            (4.2, f"SPELL_SUMMON,{LOOP_A},{LOOP_B},1,\"B\",0x1"),
            (4.3, f"SPELL_SUMMON,{LOOP_B},{LOOP_A},1,\"A\",0x1"),         # a cycle must not hang
            (4.4, hit(LOOP_A, BOSS, "Not mine", 10)),
            (5.0, f'SPELL_DAMAGE,{ME},{BOSS},1,"Arcane Blast",0x40,5000,5000,-1,64,0,0,0,nil,nil,nil,ST'),  # no advanced logging
        ], before=[f"SPELL_SUMMON,{ME},{PET},321686,\"Mirror Image\",0x1"])
        d = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]["damage"]
        self.assertEqual(d["by_spell"], [
            {"spell": "Melee", "pet": "Mirror Image", "amount": 425, "hits": 2, "crits": 1},
            {"spell": "Zap", "pet": "Spark", "amount": 20, "hits": 2, "crits": 0},
        ])

    def test_no_damage(self):
        self.write([(1.5, cast(5143, "Arcane Missiles"))])
        d = json.loads(self.run_cli("--json"))["pulls"][0]["players"][0]["damage"]
        self.assertEqual((d["total"], d["dps"], d["by_spell"], sum(d["per_second"])), (0, 0, [], 0))
        self.assertNotIn("Damage:", self.run_cli())


class Benchmark(LogTest):
    def test_key_context_and_benchmark_output(self):
        write_log(self.path, [(1.000, cc("SPELL_AURA_APPLIED")), (1.500, cast(5143, "Arcane Missiles"))])
        lines = open(self.path).read().splitlines()
        key = '10/10/2026 19:59:00.000-5  CHALLENGE_MODE_START,"Temple of Sethraliss",1877,250,12,[9,10,147]'
        open(self.path, "w").write("\n".join([key] + lines) + "\n")
        mine = json.loads(self.run_cli("--json"))
        self.assertEqual(mine["pulls"][0]["key"], {"dungeon": "Temple of Sethraliss", "map_id": 1877, "level": 12})
        self.assertEqual(mine["benchmarks"], {})
        self.assertNotIn("benchmark", mine["pulls"][0])
        top = dict(mine["pulls"][0]["players"][0], name="Topmage-TarrenMill",
                   benchmark={"url": "https://www.warcraftlogs.com/reports/X#fight=1", "region": "EU", "server": "Tarren Mill",
                              "wcl_dps": 1, "key_level": 12})
        fake = mock.MagicMock()
        fake.bench_key.return_value = "mplus:1877:12:9999"
        fake.benchmarks.return_value = {"mplus:1877:12:9999": {"label": "Training Dummy, Temple of Sethraliss +12",
                                                              "players": [top], "checked": 1}}
        with mock.patch.dict(sys.modules, {"wcl": fake}):
            report = json.loads(self.run_cli("--json", "--benchmark", "2"))
            text = self.run_cli("--benchmark")
        self.assertEqual(fake.benchmarks.call_args_list[0].args[1], 2)
        self.assertEqual(fake.benchmarks.call_args_list[1].args[1], 3)  # the default
        self.assertEqual(report["pulls"][0]["benchmark"], "mplus:1877:12:9999")
        self.assertEqual(report["benchmarks"]["mplus:1877:12:9999"]["players"][0]["name"], "Topmage-TarrenMill")
        self.assertIn("vs the top 1 Arcane Sunfury on Warcraft Logs (Training Dummy, Temple of Sethraliss +12)", text)
        self.assertIn("#1 Topmage-TarrenMill (EU)", text)
        fake.benchmarks.return_value = {"mplus:1877:12:9999": {"label": "x", "players": [], "checked": 4}}
        with mock.patch.dict(sys.modules, {"wcl": fake}):
            self.assertIsNone(json.loads(self.run_cli("--json", "--benchmark"))["pulls"][0]["benchmark"])


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
