#!/usr/bin/env python3
"""Warcraft Logs v2 API: fetch a fight's events and turn them into the same Encounter objects wowlog.py builds
from a raw combat log, so a top player's pull goes through exactly the same analysis as yours. Stdlib only.

Credentials: WCL_CLIENT_ID and WCL_CLIENT_SECRET from the environment, or a .env file next to this script
(create a client at https://www.warcraftlogs.com/api/clients). Only public reports are readable.
Responses are cached under $WHENDOILUST_CACHE (default ~/.cache/whendoilust/wcl); the API allows a few hundred
points an hour, and one fight costs about two.
"""
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import wowlog

TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"
HERE = os.path.dirname(os.path.abspath(__file__))
RANKINGS_TTL = 24 * 3600  # rankings move; report events don't

# WCL difficulty -> the combat log's DIFFICULTY codes (raid Normal/Heroic/Mythic, Mythic+)
DIFFICULTY = {3: 14, 4: 15, 5: 16, 10: 8}
MELEE = 1  # WCL's ability ID for auto attacks

AURA_EVENTS = {
    "applybuff": ("SPELL_AURA_APPLIED", "BUFF"), "applydebuff": ("SPELL_AURA_APPLIED", "DEBUFF"),
    "applybuffstack": ("SPELL_AURA_APPLIED_DOSE", "BUFF"), "applydebuffstack": ("SPELL_AURA_APPLIED_DOSE", "DEBUFF"),
    "removebuffstack": ("SPELL_AURA_REMOVED_DOSE", "BUFF"), "removedebuffstack": ("SPELL_AURA_REMOVED_DOSE", "DEBUFF"),
    "refreshbuff": ("SPELL_AURA_REFRESH", "BUFF"), "refreshdebuff": ("SPELL_AURA_REFRESH", "DEBUFF"),
    "removebuff": ("SPELL_AURA_REMOVED", "BUFF"), "removedebuff": ("SPELL_AURA_REMOVED", "DEBUFF"),
}


class WclError(Exception):
    pass


class RateLimited(WclError):
    """Stop the whole benchmark: every further request would fail too."""


MAX_EVENT_PAGES = 50       # 10,000 events a page; a boss fight is a few pages at most
MAX_RANKING_PAGES = 5      # 100 parses a page


def write_private(path, data):
    """Write JSON atomically, readable only by you (the cache holds an access token)."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def credentials():
    cid, secret = os.environ.get("WCL_CLIENT_ID"), os.environ.get("WCL_CLIENT_SECRET")
    path = os.path.join(HERE, ".env")
    if not (cid and secret) and os.path.exists(path):
        env = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and not key.startswith("#"):
                    env[key.strip()] = value.strip().strip("'\"")
        cid, secret = cid or env.get("WCL_CLIENT_ID"), secret or env.get("WCL_CLIENT_SECRET")
    if not (cid and secret):
        raise WclError("No Warcraft Logs API credentials: set WCL_CLIENT_ID and WCL_CLIENT_SECRET (or put them in "
                       f"{path}). Create a client at https://www.warcraftlogs.com/api/clients")
    return cid, secret


class Client:
    def __init__(self, cache_dir=None):
        self.cache = cache_dir or os.environ.get("WHENDOILUST_CACHE") or \
            os.path.join(os.path.expanduser("~"), ".cache", "whendoilust", "wcl")
        os.makedirs(self.cache, mode=0o700, exist_ok=True)
        self._token = None

    def token(self):
        if self._token:
            return self._token
        cid, secret = credentials()
        # The token is cached keyed by client ID, so changing credentials fetches a new one.
        path = os.path.join(self.cache, "token-" + hashlib.sha256(cid.encode()).hexdigest()[:16])
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved["expires"] > time.time() + 3600:
                self._token = saved["token"]
                return self._token
        except (OSError, ValueError, KeyError, TypeError):
            pass
        auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        req = urllib.request.Request(TOKEN_URL, data=b"grant_type=client_credentials",
                                     headers={"Authorization": "Basic " + auth})
        data = self._send(req, "sign in to Warcraft Logs")
        try:
            self._token = data["access_token"]
            expires = time.time() + float(data.get("expires_in", 3600))
        except (KeyError, TypeError, ValueError):
            raise WclError("Couldn't sign in to Warcraft Logs: no access token in the response")
        write_private(path, {"token": self._token, "expires": expires})
        return self._token

    @staticmethod
    def _send(req, what):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited("Warcraft Logs rate limit reached; try again in an hour (results so far are cached)")
            raise WclError(f"Couldn't {what}: HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WclError(f"Couldn't {what}: {e}")
        except ValueError:
            raise WclError(f"Couldn't {what}: the response wasn't JSON")

    def query(self, query, ttl=None):
        """Run a GraphQL query, from the cache when possible. ttl=None caches forever."""
        key = hashlib.sha256(query.encode()).hexdigest()
        path = os.path.join(self.cache, key + ".json")
        try:
            if ttl is None or time.time() - os.path.getmtime(path) < ttl:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError):
            pass
        req = urllib.request.Request(API_URL, data=json.dumps({"query": query}).encode(),
                                     headers={"Authorization": "Bearer " + self.token(),
                                              "Content-Type": "application/json"})
        data = self._send(req, "query Warcraft Logs")
        if not isinstance(data, dict):
            raise WclError("Warcraft Logs: unexpected response")
        if data.get("errors"):
            raise WclError("Warcraft Logs: " + "; ".join(str(e.get("message")) for e in data["errors"]
                                                         if isinstance(e, dict)))
        data = data.get("data")
        if not isinstance(data, dict):  # never cache an empty answer
            raise WclError("Warcraft Logs: empty response")
        write_private(path, data)
        return data

    # ---------- the queries the benchmark needs ----------

    def report_meta(self, code):
        q = (f'{{ reportData {{ report(code: {json.dumps(code)}) {{ startTime '
             f'fights {{ id encounterID name difficulty keystoneLevel kill startTime endTime '
             f'dungeonPulls {{ id encounterID name kill startTime endTime }} }} '
             f'masterData {{ actors {{ id name server type subType petOwner gameID }} abilities {{ gameID name }} }} }} }} }}')
        return self.query(q)["reportData"]["report"]

    def events(self, code, fight_id, actor_id, start, end):
        """Every event involving the actor (as source or target, plus their pets) between start and end (ms)."""
        out, cursor, end = [], int(start), int(end)
        for _ in range(MAX_EVENT_PAGES):
            q = (f'{{ reportData {{ report(code: {json.dumps(code)}) {{ events(fightIDs: [{int(fight_id)}], '
                 f'startTime: {cursor}, endTime: {end}, sourceID: {int(actor_id)}, includeResources: true, '
                 f'limit: 10000) {{ nextPageTimestamp data }} }} }} }}')
            page = self.query(q)["reportData"]["report"]["events"]
            out += page["data"]
            nxt = page.get("nextPageTimestamp")
            if nxt is None:
                return out
            if int(nxt) <= cursor:
                raise WclError("Warcraft Logs returned the same page of events twice")
            cursor = int(nxt)
        raise WclError(f"more than {MAX_EVENT_PAGES} pages of events for one fight")


# ---------- translation ----------

def guid(actor, instance=None):
    if actor is None:
        return "0000000000000000"
    if actor["type"] == "Player":
        return f"Player-wcl-{actor['id']}"
    # Pets keep one GUID across instances (so the owner map is per pet); NPCs need the instance, since adds
    # of the same kind are separate targets (Touch of the Magi spans are per target).
    inst = 0 if actor["type"] == "Pet" else int(instance or 0)
    return f"Creature-0-0-0-0-{actor.get('gameID') or 0}-{actor['id']:08d}{inst:02d}"


def flags(actor, actors, me):
    """Unit flags as the combat log writes them; only 'mine' (0x511, 0x2111) and the friendly bit 0x10 matter.
    A pet is friendly when a player owns it; an enemy's pet (an add summoned by the boss) is hostile. An actor
    WCL doesn't list is treated as friendly, so damage to it isn't counted."""
    if actor is None:
        return "0x10"
    if actor["id"] == me:
        return "0x511"
    if actor["type"] == "Player":
        return "0x512"
    if actor["type"] == "Pet":
        owner = actors.get(actor.get("petOwner"))
        if owner and owner["id"] == me:
            return "0x2111"
        if owner and owner["type"] == "Player":
            return "0x1112"
    return "0xa48"


def unit(actors, actor_id, instance, me):
    a = actors.get(actor_id)
    name = (f"{a['name']}-{a.get('server') or 'WCL'}" if a and a["type"] == "Player" else a["name"] if a else "nil")
    return [guid(a, instance), name, flags(a, actors, me), "0x80000000"]


def translate_event(e, actors, abilities, me):
    """One WCL event as wowlog field list, or None for events the analysis doesn't use."""
    kind = e.get("type")
    src = unit(actors, e.get("sourceID"), e.get("sourceInstance"), me)
    dst = unit(actors, e.get("targetID"), e.get("targetInstance"), me)
    ability = e.get("abilityGameID", 0)
    spell = [str(ability), abilities.get(ability, f"Spell {ability}"), "0x40"]
    if kind == "combatantinfo":
        return ["COMBATANT_INFO", src[0], str(e.get("specID", 0))]
    if kind == "cast":
        return ["SPELL_CAST_SUCCESS", *src, *dst, *spell]
    if kind == "begincast":
        return ["SPELL_CAST_START", *src, *dst, *spell]
    if kind in AURA_EVENTS:
        event, aura = AURA_EVENTS[kind]
        stack = [str(e["stack"])] if "stack" in e and event.endswith("_DOSE") else []
        return [event, *src, *dst, *spell, aura, *stack]
    if kind == "summon":
        return ["SPELL_SUMMON", *src, *dst, *spell]
    if kind == "death":
        return ["UNIT_DIED", "0000000000000000", "nil", "0x80000000", "0x80000000", *dst]
    advanced = [dst[0], "0000000000000000"] + ["0"] * (wowlog.ADVANCED_FIELDS - 2)
    if kind == "resourcechange":
        return ["SPELL_ENERGIZE", *src, *dst, *spell, *advanced, str(e.get("resourceChange", 0)), str(e.get("waste", 0)),
                str(e.get("resourceChangeType", -1)), str(e.get("maxResourceAmount", 0))]
    if kind == "damage":
        amount, absorbed = int(e.get("amount", 0)), int(e.get("absorbed", 0))
        if not amount and not absorbed:  # immune / missed: the raw log writes a SPELL_MISSED that isn't counted
            return None
        tail = [str(amount), str(e.get("unmitigatedAmount", amount)), "-1", "1", "0", "0", str(absorbed),
                "1" if e.get("hitType") == 2 else "nil", "nil", "nil"]
        if ability == MELEE:
            return ["SWING_DAMAGE", *src, *dst, *advanced, *tail]
        return ["SPELL_PERIODIC_DAMAGE" if e.get("tick") else "SPELL_DAMAGE", *src, *dst, *spell, *advanced, *tail,
                "AOE" if e.get("isAoE") else "ST"]
    return None


def encounter(meta, events, actor_id, segment, kill=True, difficulty=None):
    """A wowlog.Encounter for one boss segment (a raid fight, or a dungeonPull inside a key)."""
    actors = {a["id"]: a for a in meta["masterData"]["actors"]}
    abilities = {a["gameID"]: a["name"] for a in meta["masterData"]["abilities"]}
    enc = wowlog.Encounter(segment["startTime"] / 1000, ["ENCOUNTER_START", str(segment["encounterID"]),
                           segment["name"], DIFFICULTY.get(difficulty, 0), "0"])
    enc.end, enc.success = segment["endTime"] / 1000, bool(kill)
    enc.owners = {guid(a): guid(actors[a["petOwner"]]) for a in actors.values() if a.get("petOwner") in actors}
    for e in events:
        if not segment["startTime"] <= e["timestamp"] <= segment["endTime"]:
            continue
        f = translate_event(e, actors, abilities, actor_id)
        if f:
            enc.events.append((e["timestamp"] / 1000, f))
    return enc


# ---------- benchmark: the top Arcane Sunfury parses for a pull ----------

WCL_DIFFICULTY = {v: k for k, v in DIFFICULTY.items()}  # log difficulty code -> WCL difficulty
MAX_CANDIDATES = 4  # look at up to this many rankings per player wanted (non-Sunfury parses are skipped)


def bench_key(enc):
    """Pulls that share a benchmark: the same raid boss and difficulty, or the same boss in the same dungeon at
    the same key level."""
    if enc.key:
        return f"mplus:{enc.key['map_id']}:{enc.key['level']}:{enc.id}"
    return f"raid:{enc.id}:{enc.difficulty}"


def dungeon_encounter(client, name):
    """WCL's ID for a Mythic+ dungeon, from the newest live Mythic+ zone that has it (not PTR or beta), and that
    zone's key-level brackets ({min, bucket}: bracket 1 is +min)."""
    data = client.query("{ worldData { expansions { zones { id name brackets { min bucket } encounters { id name } } } } }",
                        ttl=7 * 24 * 3600)
    zones = [z for x in data["worldData"]["expansions"] for z in x["zones"]
             if "Mythic+" in z["name"] and not any(w in z["name"] for w in ("PTR", "Beta"))]
    for z in sorted(zones, key=lambda z: -z["id"]):
        for e in z["encounters"]:
            if e["name"] == name:
                return e["id"], z.get("brackets") or {}
    return None, {}


def key_bracket(level, brackets):
    """The ranking bracket for a key level: WCL counts brackets from the zone's lowest key, so +13 is bracket 12."""
    low, size = int(brackets.get("min") or 2), int(brackets.get("bucket") or 1)
    return (int(level) - low) // size + 1


def rankings(client, encounter_id, difficulty, bracket=None, page=1):
    args = f'className: "Mage", specName: "Arcane", difficulty: {int(difficulty)}, metric: dps, page: {int(page)}'
    if bracket:
        args += f", bracket: {int(bracket)}"
    q = f"{{ worldData {{ encounter(id: {int(encounter_id)}) {{ characterRankings({args}) }} }} }}"
    data = client.query(q, ttl=RANKINGS_TTL)["worldData"]["encounter"]
    return (data or {}).get("characterRankings") or {}


def find_actor(meta, name, server):
    players = [a for a in meta["masterData"]["actors"] if a["type"] == "Player" and a["name"] == name]
    squash = (server or "").replace(" ", "").replace("'", "").lower()
    return next((a for a in players if (a.get("server") or "").replace("'", "").lower() == squash),
                players[0] if len(players) == 1 else None)


def segment_for(meta, fight_id, boss_id):
    """The boss's start/end inside a ranked fight: the fight itself (raid) or its dungeonPull (Mythic+)."""
    fight = next((f for f in meta["fights"] if f["id"] == fight_id), None)
    if not fight:
        return None, None
    if fight["encounterID"] == boss_id:
        return fight, fight
    pull = next((p for p in fight.get("dungeonPulls") or [] if p["encounterID"] == boss_id and p["kill"]), None)
    return fight, pull


def top_players(client, enc, n, log=None):
    """Summaries (as wowlog.summarize makes them) of the top n Arcane Sunfury 4-piece parses for this pull's boss."""
    log = log or (lambda msg: None)
    if enc.key:
        ranked_id, brackets = dungeon_encounter(client, enc.key["dungeon"])
        difficulty, bracket = 10, key_bracket(enc.key["level"], brackets)
        label = f"{enc.name}, {enc.key['dungeon']} +{enc.key['level']}"
    else:
        ranked_id, difficulty, bracket = int(enc.id), WCL_DIFFICULTY.get(enc.difficulty), None
        label = f"{wowlog.DIFFICULTY.get(enc.difficulty, '?')} {enc.name}"
    out = {"source": "warcraftlogs", "label": label, "players": [], "checked": 0}
    if not ranked_id or not difficulty:
        out["skipped"] = "no Warcraft Logs rankings for this content"
        return out
    for page in range(1, MAX_RANKING_PAGES + 1):
        if len(out["players"]) >= n or out["checked"] >= MAX_CANDIDATES * n:
            break
        ranks = rankings(client, ranked_id, difficulty, bracket, page)
        rows = ranks.get("rankings") or []
        for r in rows:
            if len(out["players"]) >= n or out["checked"] >= MAX_CANDIDATES * n:
                break
            code = str((r.get("report") or {}).get("code", ""))
            if r.get("hidden") or code.startswith("a:"):  # anonymous: the report can't be read
                continue
            out["checked"] += 1
            try:
                summary = parse_summary(client, r, int(enc.id))
            except RateLimited:
                raise
            except (WclError, KeyError, TypeError, ValueError, AttributeError) as e:
                log(f"  skipped {r.get('name')!r}: {e}")
                continue
            if summary:
                out["players"].append(summary)
        if not rows or not ranks.get("hasMorePages"):
            break
    log(f"Benchmark {label}: {len(out['players'])} Sunfury 4-piece parses from {out['checked']} checked")
    return out


def parse_summary(client, r, boss_id):
    """One ranked parse, analyzed like a raw log pull; None when it isn't Arcane Sunfury with the 4-piece."""
    code, fight_id = r["report"]["code"], r["report"]["fightID"]
    meta = client.report_meta(code)
    actor = find_actor(meta, r["name"], r.get("server", {}).get("name"))
    fight, seg = segment_for(meta, fight_id, boss_id)
    if not actor or not seg:
        return None
    events = client.events(code, fight_id, actor["id"], seg["startTime"], seg["endTime"])
    enc = encounter(meta, events, actor["id"], seg, seg.get("kill", True), fight.get("difficulty"))
    g = guid(actor)
    build, check = wowlog.detect_builds([enc], g)[0]
    if not build["supported"]:
        return None
    name = f"{actor['name']}-{actor.get('server') or 'WCL'}"
    summary = wowlog.summarize(enc, g, name, build, check)
    summary["benchmark"] = {
        "url": f"https://www.warcraftlogs.com/reports/{urllib.parse.quote(str(code), safe='')}#fight={int(fight_id)}",
        "region": r.get("server", {}).get("region"), "server": r.get("server", {}).get("name"),
        "wcl_dps": round(r.get("amount") or 0), "key_level": fight.get("keystoneLevel"),
    }
    return summary


def benchmarks(encounters, n, client=None, log=None):
    """{bench_key: top_players(...)} for every distinct boss among the encounters. Never raises: a failure is
    reported (stderr by default) and leaves that boss without a benchmark."""
    import sys
    log = log or (lambda msg: print(msg, file=sys.stderr))
    out = {}
    try:
        client = client or Client()
        credentials()
    except (WclError, OSError) as e:
        log(f"Benchmark skipped: {e}")
        return out
    stop = None
    for enc in encounters:
        k = bench_key(enc)
        if k in out:
            continue
        if stop:
            out[k] = {"source": "warcraftlogs", "label": enc.name, "players": [], "checked": 0, "skipped": stop}
            continue
        try:
            out[k] = top_players(client, enc, n, log)
        except (WclError, OSError, KeyError, TypeError, ValueError, AttributeError) as e:
            msg = str(e) if isinstance(e, (WclError, OSError)) else f"unexpected response from Warcraft Logs ({e!r})"
            log(f"Benchmark for {enc.name} skipped: {msg}")
            out[k] = {"source": "warcraftlogs", "label": enc.name, "players": [], "checked": 0, "skipped": msg}
            if isinstance(e, RateLimited):
                stop = msg
    return out
