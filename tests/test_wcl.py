"""wcl.py: WCL events must analyze exactly like the raw log lines they stand for. No network: a fake client."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wcl  # noqa: E402
import wowlog  # noqa: E402

ME, PAL, BOSS, ADD, PET, SNAKE = 2, 3, 24, 25, 16, 30
META = {
    "startTime": 0,
    "fights": [{"id": 7, "encounterID": 3420, "name": "Sszorak", "difficulty": 4, "keystoneLevel": None, "kill": True,
                "startTime": 10000, "endTime": 70000, "dungeonPulls": None},
               {"id": 1, "encounterID": 61877, "name": "Temple of Sethraliss", "difficulty": 10, "keystoneLevel": 12,
                "kill": True, "startTime": 0, "endTime": 900000,
                "dungeonPulls": [{"id": 2, "encounterID": 2124, "name": "Adderis and Aspix", "kill": True,
                                  "startTime": 100000, "endTime": 160000}]}],
    "masterData": {
        "actors": [
            {"id": ME, "name": "Topmage", "server": "TarrenMill", "type": "Player", "subType": "Mage", "petOwner": None, "gameID": 1},
            {"id": PAL, "name": "Pally", "server": "TarrenMill", "type": "Player", "subType": "Paladin", "petOwner": None, "gameID": 2},
            {"id": BOSS, "name": "Sszorak", "type": "NPC", "subType": "Boss", "petOwner": None, "gameID": 999},
            {"id": PET, "name": "Arcane Phoenix", "type": "Pet", "subType": "Pet", "petOwner": ME, "gameID": 223453},
            {"id": SNAKE, "name": "Snake", "type": "Pet", "subType": "Pet", "petOwner": BOSS, "gameID": 5},
        ],
        "abilities": [{"gameID": 5143, "name": "Arcane Missiles"}, {"gameID": 44425, "name": "Arcane Barrage"},
                      {"gameID": 263725, "name": "Clearcasting"}, {"gameID": 448604, "name": "Spellfire Sphere"},
                      {"gameID": 1296930, "name": "Cumulative Power"}, {"gameID": 461524, "name": "Arcane Charge"},
                      {"gameID": 456139, "name": "Meteorite"}, {"gameID": 1309786, "name": "Refraction"}],
    },
}


def ev(t, kind, src=ME, dst=ME, ability=0, **kw):
    return dict(timestamp=t, type=kind, sourceID=src, targetID=dst, abilityGameID=ability, fight=7, **kw)


SUNFURY = [ev(10000, "combatantinfo", specID=62), ev(10001, "applybuff", ability=448604),
           ev(10002, "applybuff", ability=1296930)]


class Translate(unittest.TestCase):
    def enc(self, events, seg=None):
        return wcl.encounter(META, SUNFURY + events, ME, seg or META["fights"][0], True, 4)

    def test_events_become_the_log_lines_the_analyzer_reads(self):
        enc = self.enc([
            ev(11000, "applybuff", ability=263725),
            ev(11500, "cast", dst=BOSS, ability=5143),
            ev(11600, "resourcechange", ability=461524, resourceChange=1, resourceChangeType=16, waste=0, maxResourceAmount=4),
            ev(12000, "applybuffstack", ability=263725, stack=2),
            ev(12000, "refreshbuff", ability=263725),
            ev(13000, "damage", dst=BOSS, ability=44425, amount=1000, absorbed=200, hitType=2, overkill=0),
            ev(13100, "damage", dst=BOSS, ability=5143, amount=300, tick=True),
            ev(13200, "damage", dst=BOSS, ability=5143, amount=0, hitType=10),      # immune: not a hit
            ev(13300, "damage", dst=ME, ability=1309786, amount=999),                # your own Refraction
            ev(13400, "damage", src=PET, dst=BOSS, ability=456139, amount=50),       # your pet
            ev(13500, "damage", dst=SNAKE, ability=5143, amount=70),                 # the boss's pet is a target
            ev(13600, "damage", src=PET, dst=BOSS, ability=1, amount=5),              # pet melee
            ev(14000, "damage", src=PAL, dst=BOSS, ability=44425, amount=9999),       # someone else
            ev(15000, "cast", dst=BOSS, ability=5143),
            ev(99000, "cast", dst=BOSS, ability=44425),                               # after the fight
        ])
        self.assertEqual((enc.start, enc.end, enc.id, enc.difficulty, enc.success), (10.0, 70.0, "3420", 15, True))
        g = wcl.guid(META["masterData"]["actors"][0])
        build, check = wowlog.detect_builds([enc], g)[0]
        self.assertTrue(build["supported"])
        p = wowlog.summarize(enc, g, "Topmage-TarrenMill", build, check)
        self.assertEqual(p["abilities"], {"Arcane Missiles": 2})
        self.assertEqual(p["timeline"][1]["state"]["charges"], 1)  # from the resourcechange (Arcane Charges)
        names = {round(t, 1): f[0] for t, f in enc.events}
        self.assertEqual((names[13.1], names[13.6], names[11.6]), ("SPELL_PERIODIC_DAMAGE", "SWING_DAMAGE", "SPELL_ENERGIZE"))
        self.assertEqual(p["timeline"][0]["state"]["clearcasting"], 1)
        self.assertTrue(p["timeline"][0]["matched"])  # CC up, Salvo 0: Missiles is right, same as the raw-log test
        d = p["damage"]
        self.assertEqual(d["total"], 1200 + 300 + 50 + 70 + 5)
        self.assertEqual({(s["spell"], s["pet"]): (s["amount"], s["hits"], s["crits"]) for s in d["by_spell"]}, {
            ("Arcane Barrage", None): (1200, 1, 1), ("Arcane Missiles", None): (370, 2, 0),
            ("Meteorite", "Arcane Phoenix"): (50, 1, 0), ("Melee", "Arcane Phoenix"): (5, 1, 0)})
        self.assertEqual(p["spec_checks"]["clearcasting"]["gained"], 2)
        self.assertEqual(p["spec_checks"]["clearcasting"]["wasted"], [])  # REFRESH paired with a dose isn't waste

    def test_dungeon_boss_segment_and_unknown_events(self):
        seg = META["fights"][1]["dungeonPulls"][0]
        enc = wcl.encounter(META, [ev(99000, "cast", ability=5143), ev(120000, "cast", ability=5143),
                                   ev(121000, "heal", amount=5), ev(122000, "mystery")], ME, seg, True, 10)
        self.assertEqual((enc.start, enc.end, enc.id, enc.difficulty), (100.0, 160.0, "2124", 8))
        self.assertEqual([f[0] for _, f in enc.events], ["SPELL_CAST_SUCCESS"])

    def test_flags(self):
        actors = {a["id"]: a for a in META["masterData"]["actors"]}
        self.assertEqual([wcl.flags(actors[i], actors, ME) for i in (ME, PAL, BOSS, PET, SNAKE)],
                         ["0x511", "0x512", "0xa48", "0x2111", "0xa48"])


class FakeClient:
    """Answers the queries top_players makes from canned data and counts them."""
    def __init__(self, ranks, reports, has_more=False):
        self.ranks, self.reports, self.has_more, self.calls = ranks, reports, has_more, []

    def query(self, q, ttl=None):
        self.calls.append(q)
        if "expansions" in q:
            return {"worldData": {"expansions": [{"zones": [
                {"id": 56, "name": "Mythic+ Season 2 (PTR)", "encounters": [{"id": 111877, "name": "Temple of Sethraliss"}]},
                {"id": 55, "name": "Mythic+ Season 2", "encounters": [{"id": 61877, "name": "Temple of Sethraliss"}]}]}]}}
        return {"worldData": {"encounter": {"characterRankings": {"rankings": self.ranks, "hasMorePages": self.has_more}}}}

    def report_meta(self, code):
        return self.reports[code][0]

    def events(self, code, fight_id, actor_id, start, end):
        return self.reports[code][1]


def rank(name, code, fight=7, server="Tarren Mill"):
    return {"name": name, "amount": 250000.5, "report": {"code": code, "fightID": fight},
            "server": {"name": server, "region": "EU"}}


def raid_pull():
    enc = wowlog.Encounter(0, ["ENCOUNTER_START", "3420", "Sszorak", "15", "20"])
    enc.end = 60
    return enc


SPELLSLINGER = [ev(10000, "combatantinfo", specID=62), ev(10002, "applybuff", ability=1296930),
                *[ev(11000 + i * 1000, "cast", dst=BOSS, ability=5143) for i in range(3)]]


class TopPlayers(unittest.TestCase):
    def test_keeps_only_sunfury_4pc_and_stops_at_n(self):
        c = FakeClient([rank("Topmage", "A"), rank("Topmage", "B"), rank("Topmage", "C"), rank("Topmage", "D")],
                       {"A": (META, SPELLSLINGER), "B": (META, SUNFURY), "C": (META, SUNFURY), "D": (META, SUNFURY)})
        out = wcl.top_players(c, raid_pull(), 2)
        self.assertEqual((len(out["players"]), out["checked"], out["label"]), (2, 3, "Heroic Sszorak"))
        b = out["players"][0]["benchmark"]
        self.assertEqual((b["url"], b["region"], b["wcl_dps"]), ("https://www.warcraftlogs.com/reports/B#fight=7", "EU", 250000))
        self.assertIn('difficulty: 4', c.calls[0])

    def test_candidate_cap_and_bad_entries(self):
        c = FakeClient([rank("Nobody", "A"), rank("Topmage", "A", fight=99)] + [rank("Topmage", "S")] * 20,
                       {"A": (META, SUNFURY), "S": (META, SPELLSLINGER)})
        out = wcl.top_players(c, raid_pull(), 1)
        self.assertEqual((out["players"], out["checked"]), ([], wcl.MAX_CANDIDATES))

    def test_dungeon_boss_uses_the_live_season_and_key_level(self):
        enc = wowlog.Encounter(0, ["ENCOUNTER_START", "2124", "Adderis and Aspix", "8", "5"])
        enc.end, enc.key = 60, {"dungeon": "Temple of Sethraliss", "map_id": 1877, "level": 12}
        c = FakeClient([rank("Topmage", "K", fight=1)], {"K": (META, [dict(e, timestamp=e["timestamp"] + 90000) for e in SUNFURY])})
        out = wcl.top_players(c, enc, 1)
        self.assertEqual(len(out["players"]), 1)
        self.assertIn("encounter(id: 61877)", c.calls[1])
        self.assertIn("bracket: 12", c.calls[1])
        self.assertEqual(out["label"], "Adderis and Aspix, Temple of Sethraliss +12")
        self.assertEqual(wcl.bench_key(enc), "mplus:1877:12:2124")

    def test_benchmarks_never_raise(self):
        logs = []
        with mock.patch.dict(os.environ, {"WCL_CLIENT_ID": "", "WCL_CLIENT_SECRET": ""}), \
                mock.patch.object(wcl, "HERE", tempfile.mkdtemp()):
            self.assertEqual(wcl.benchmarks([raid_pull()], 3, client=FakeClient([], {}), log=logs.append), {})
        self.assertIn("No Warcraft Logs API credentials", logs[0])

        class Broken(FakeClient):
            def query(self, q, ttl=None):
                raise wcl.WclError("rate limit")
        with mock.patch.object(wcl, "credentials", return_value=("a", "b")):
            out = wcl.benchmarks([raid_pull(), raid_pull()], 3, client=Broken([], {}), log=logs.append)
        self.assertEqual(list(out), ["raid:3420:15"])
        self.assertEqual(out["raid:3420:15"]["players"], [])


class Client(unittest.TestCase):
    def test_cache_and_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env"), "w") as f:
                f.write('# comment\nWCL_CLIENT_ID="abc"\nWCL_CLIENT_SECRET=def\n')
            with mock.patch.object(wcl, "HERE", d), mock.patch.dict(os.environ, {"WCL_CLIENT_ID": "", "WCL_CLIENT_SECRET": ""}):
                self.assertEqual(wcl.credentials(), ("abc", "def"))
            c = wcl.Client(cache_dir=d)
            c._token = "t"
            sent = []

            def fake_send(req, what):
                sent.append(req)
                return {"data": {"x": len(sent)}}
            with mock.patch.object(wcl.Client, "_send", staticmethod(fake_send)):
                self.assertEqual(c.query("{ x }"), {"x": 1})
                self.assertEqual(c.query("{ x }"), {"x": 1})           # cached forever
                self.assertEqual(c.query("{ x }", ttl=0), {"x": 2})    # expired
            self.assertEqual(len(sent), 2)


if __name__ == "__main__":
    unittest.main()
