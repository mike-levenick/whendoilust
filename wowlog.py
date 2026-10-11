#!/usr/bin/env python3
"""Parse a WoW advanced combat log and report on one player's performance per encounter.

Usage: python3 wowlog.py [LOGFILE] [--player NAME]
Defaults to the newest WoWCombatLog in the retail Logs folder and the player flagged as "mine" (0x511).
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

# Default retail log folders, macOS then Windows. Override with --log-dir or $WOWLOG_DIR.
LOG_DIRS = [
    "/Applications/World of Warcraft/_retail_/Logs",
    r"C:\Program Files (x86)\World of Warcraft\_retail_\Logs",
    r"C:\Program Files\World of Warcraft\_retail_\Logs",
]
MINE_FLAG = 0x511  # affiliation mine + reaction friendly + controlled by player + type player
GCD = 1.5          # base GCD; haste shrinks it, so gaps above this are a conservative "idle" measure
IDLE_THRESHOLD = 2.0
DIFFICULTY = {14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR", 8: "M+"}
POWER_ARCANE_CHARGES = 16
SALVO_MAX = 25
CLEARCASTING_MAX = 3
REACTION = 0.4  # seconds; a proc that appeared this recently before a cast isn't counted as "available"
PROCS = {"Clearcasting", "Prismatic Bolt!", "Arcane Soul"}  # reaction allowance applies to these, not to resources

# Timeline windows: buffs on the player, plus Touch of the Magi, a debuff the player puts on the target.
WINDOW_BUFFS = {"Arcane Surge": "Arcane Surge", "Arcane Soul": "Arcane Soul", "Bloodlust": "Lust", "Heroism": "Lust",
                "Time Warp": "Lust", "Primal Rage": "Lust", "Fury of the Aspects": "Lust"}
WINDOW_DEBUFFS = {"Touch of the Magi": "Touch of the Magi"}
SERIES = {"Arcane Salvo": "salvo", "Clearcasting": "clearcasting"}  # stack counts over time

# Spells that aren't part of the damage rotation, excluded from "casts" accounting.
NON_ROTATIONAL = {"Arcane Intellect", "Freightrunner's Flask"}


def parse_ts(s):
    # "10/9/2026 21:07:06.454-5" -> naive datetime (timezone suffix dropped; we only need deltas)
    s = s.rsplit("-", 1)[0] if s.count("-") else s
    return datetime.strptime(s, "%m/%d/%Y %H:%M:%S.%f").timestamp()


def read_events(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            ts, sep, rest = line.rstrip("\n").partition("  ")
            if not sep:
                continue
            if rest.startswith("COMBATANT_INFO"):
                # Nested brackets (talents, gear) follow; only the scalar head is parsed.
                # Its last field is the spec ID.
                head = rest.split(",[", 1)[0].split(",")
                if len(head) > 2:  # a line still being written can be truncated
                    yield parse_ts(ts), ["COMBATANT_INFO", head[1], head[-1]]
                continue
            fields = next(csv.reader([rest]))
            yield parse_ts(ts), fields


class Encounter:
    def __init__(self, t, fields):
        self.start = t
        self.id, self.name, self.difficulty, self.size = fields[1], fields[2], int(fields[3]), fields[4]
        self.end = None
        self.success = None
        self.events = []

    @property
    def label(self):
        diff = DIFFICULTY.get(self.difficulty, str(self.difficulty))
        result = {True: "KILL", False: "wipe", None: "incomplete"}[self.success]
        return f"{self.name} ({diff}) - {result}, {self.duration:.0f}s"

    @property
    def duration(self):
        last = self.end or (self.events[-1][0] if self.events else self.start)
        return last - self.start


def split_encounters(path):
    encounters, cur = [], None
    for t, f in read_events(path):
        ev = f[0]
        if ev == "ENCOUNTER_START":
            cur = Encounter(t, f)
            encounters.append(cur)
        elif ev == "ENCOUNTER_END" and cur:
            cur.end, cur.success = t, f[5] == "1"
            cur = None
        elif cur:
            cur.events.append((t, f))
    return encounters


def find_player(path, name=None):
    """Return (guid, full name) for `name` (prefix match), or for the character flagged as yours."""
    for _, f in read_events(path):
        if len(f) > 3 and f[1].startswith("Player-"):
            if name:
                if f[2].lower().startswith(name.lower()):
                    return f[1], f[2]
                continue
            try:
                if int(f[3], 16) & 0xFFFF == MINE_FLAG:
                    return f[1], f[2]
            except ValueError:
                pass
    raise SystemExit(f"Couldn't find {name or 'your character'} in this log.")


def analyze(enc, guid):
    t0 = enc.start
    casts = []            # (t, spell, state snapshot at cast time)
    stacks = defaultdict(int)          # aura on me -> current stacks
    gained_at = {}                     # aura -> (time of last stack gain, stacks before it)
    charges = 0
    cast_starts = {}      # spell -> start t, for hardcast duration
    busy = []             # (start, end) intervals the player was casting / on GCD
    death = None
    auras = defaultdict(list)          # aura -> [(t, event)]
    charge_gain = charge_waste = 0
    charge_waste_by = Counter()
    failed = Counter()
    salvo_capped_hits = 0              # Missiles hits landing while Arcane Salvo was already at 25
    windows = defaultdict(list)        # window name -> [[start, end or None]]
    series = defaultdict(list)         # SERIES key -> [(t, stacks)]

    for t, f in enc.events:
        ev = f[0]
        src = f[1] if len(f) > 1 else None
        dst = f[5] if len(f) > 5 else None
        if ev == "UNIT_DIED" and dst == guid:
            death = t
        spell = f[10] if len(f) > 10 else None
        if ev.startswith("SPELL_AURA") and dst == guid:
            if ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_APPLIED_DOSE") and spell in PROCS:
                prev = gained_at.get(spell)
                before = prev[1] if prev and t - prev[0] < REACTION else stacks[spell]
                gained_at[spell] = (t, before)
            if ev == "SPELL_AURA_APPLIED":
                stacks[spell] = int(f[-1]) if f[-1].isdigit() else 1
            elif ev in ("SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REMOVED_DOSE"):
                stacks[spell] = int(f[-1])
            elif ev == "SPELL_AURA_REMOVED":
                stacks[spell] = 0
            elif ev == "SPELL_AURA_REFRESH":
                # A REFRESH also accompanies every stack change (same timestamp); only a lone one at cap is a wasted gain
                if spell == "Clearcasting" and stacks[spell] >= CLEARCASTING_MAX and not (auras[spell] and auras[spell][-1][0] == t):
                    ev = "CAPPED"
            auras[spell].append((t, ev))
            if spell in SERIES and (not series[SERIES[spell]] or series[SERIES[spell]][-1][1] != stacks[spell]):
                series[SERIES[spell]].append((t, stacks[spell]))
        window = (WINDOW_BUFFS.get(spell) if dst == guid else None) or \
                 (WINDOW_DEBUFFS.get(spell) if src == guid and dst != guid else None)
        if window and ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"):
            spans = windows[window]
            if ev == "SPELL_AURA_APPLIED" and not (spans and spans[-1][1] is None):
                spans.append([t, None])
            elif ev == "SPELL_AURA_REMOVED" and spans and spans[-1][1] is None:
                spans[-1][1] = t
        if src != guid:
            continue
        if ev == "SPELL_CAST_START":
            cast_starts[spell] = t
        elif ev == "SPELL_CAST_SUCCESS":
            snap = dict(stacks, _charges=charges)
            for aura, (gt, before) in gained_at.items():
                if t - gt < REACTION:   # too fresh to have reacted to
                    snap[aura] = before
            casts.append((t, spell, snap))
            if spell == "Arcane Barrage":
                charges = 0
            st = cast_starts.pop(spell, None)
            begin = st if st is not None and t - st < 6 else t
            busy.append((begin, max(t, begin + GCD)))
        elif ev == "SPELL_DAMAGE" and spell == "Arcane Missiles" and stacks["Arcane Salvo"] >= SALVO_MAX:
            salvo_capped_hits += 1
        elif ev == "SPELL_CAST_FAILED":
            failed[(spell, f[-1])] += 1
        elif ev == "SPELL_ENERGIZE" and int(f[-2]) == POWER_ARCANE_CHARGES:
            amount, over = float(f[-4]), float(f[-3])
            charge_gain += amount
            charges = 4 if over else min(4, charges + int(amount))
            charge_waste += over
            if over:
                charge_waste_by[spell] += over

    end = death or enc.end or (enc.events[-1][0] if enc.events else t0)
    fight_len = end - t0

    # Idle time: gaps between busy windows (and before first cast) longer than threshold.
    gaps = []
    cursor = t0
    for b, e in sorted(busy):
        if b - cursor > IDLE_THRESHOLD:
            gaps.append((cursor - t0, b - cursor))
        cursor = max(cursor, e)
    if end - cursor > IDLE_THRESHOLD:
        gaps.append((cursor - t0, end - cursor))
    idle = sum(g for _, g in gaps)
    for spans in windows.values():
        for span in spans:
            if span[1] is None:  # still up when the pull ended
                span[1] = end

    return {
        "fight_len": fight_len,
        "death": death - t0 if death else None,
        "casts": casts,
        "gaps": gaps,
        "idle": idle,
        "auras": auras,
        "charge_gain": charge_gain,
        "charge_waste": charge_waste,
        "charge_waste_by": charge_waste_by,
        "failed": failed,
        "salvo_capped_hits": salvo_capped_hits,
        "windows": windows,
        "series": series,
    }


def fmt_t(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


ROTATIONAL = {"Arcane Missiles", "Arcane Barrage", "Arcane Blast", "Prismatic Bolt", "Arcane Orb", "Arcane Pulse"}


def sunfury_choice(st, tom_ready):
    """What the SimC Sunfury APL (midnight branch, 2026-09-06) would press given this state.

    actions.sunfury=arcane_missiles,if=buff.clearcasting.react&buff.arcane_salvo.stack<12
    +=/prismatic_bolt,if=(!4pc|buff.cumulative_power.stack=8)&buff.arcane_soul.down
    +=/arcane_barrage,if=(charges=4&((clearcasting&salvo>=12)|salvo=25))|buff.arcane_soul.up|(salvo>8&touch_of_the_magi ready)
    +=/prismatic_bolt
    +=/arcane_orb,if=charges<3
    +=/arcane_blast
    Returns (spell, reason). Orb/Pulse charges aren't visible in the log, so they're folded into "filler".
    """
    salvo, cc = st.get("Arcane Salvo", 0), st.get("Clearcasting", 0)
    soul = st.get("Arcane Soul", 0) > 0
    bolt = st.get("Prismatic Bolt!", 0) > 0
    charges = st["_charges"]
    if cc and salvo < 12:
        return "Arcane Missiles", f"Clearcasting up and Salvo {salvo} < 12"
    if bolt and not soul and st.get("Cumulative Power", 0) >= 8:
        return "Prismatic Bolt", "Prismatic Bolt at 8 Cumulative Power"
    if soul:
        return "Arcane Barrage", "Arcane Soul is up"
    if charges == 4 and ((cc and salvo >= 12) or salvo >= SALVO_MAX):
        return "Arcane Barrage", f"4 charges and Salvo {salvo}" + (" with Clearcasting" if cc else " (capped)")
    if salvo > 8 and tom_ready:
        return "Arcane Barrage", "spend Salvo right before Touch of the Magi"
    if bolt:
        return "Prismatic Bolt", "Prismatic Bolt available"
    return "filler", "build with Blast/Orb"


def rnd(t):
    return round(t, 3)  # the log has millisecond precision


def check_arcane_sunfury(r, t0):
    """Arcane Mage (Sunfury) checks: replay each GCD against the SimC priority (see sunfury_choice).

    Returns (spec dict, {cast index: (expected, reason)}) so the timeline can be annotated.
    """
    casts = r["casts"]
    tom_casts = [t for t, s, _ in casts if s == "Touch of the Magi"]

    def tom_ready(t):
        prior = [x for x in tom_casts if x <= t]
        return not prior or t - prior[-1] >= 45

    verdicts = {}
    mismatches = defaultdict(list)
    agree = total = 0
    for i, (t, spell, st) in enumerate(casts):
        if spell not in ROTATIONAL:
            continue
        want, why = sunfury_choice(st, tom_ready(t))
        got = "filler" if spell in ("Arcane Blast", "Arcane Orb", "Arcane Pulse") else spell
        verdicts[i] = (want, why, want == got)
        total += 1
        if want == got:
            agree += 1
        else:
            mismatches[(want, spell)].append({"t": rnd(t - t0), "reason": why})

    # Arcane Soul windows: what was pressed inside each
    soul_casts = [(t, s) for t, s, st in casts if st.get("Arcane Soul", 0) and s in ROTATIONAL]
    windows = [t for t, e in r["auras"].get("Arcane Soul", []) if e == "SPELL_AURA_APPLIED"]
    soul = [{"t": rnd(w - t0), "casts": [s for t, s in soul_casts if 0 <= t - w <= 4.5]} for w in windows]

    # Clearcasting waste, split by whether Arcane Soul was up (soul Barrages generate CC, so some cap there is expected)
    cc_events = r["auras"].get("Clearcasting", [])
    soul_spans = []
    for t, e in r["auras"].get("Arcane Soul", []):
        if e == "SPELL_AURA_APPLIED":
            soul_spans.append([t, t + 4.5])
        elif e == "SPELL_AURA_REMOVED" and soul_spans:
            soul_spans[-1][1] = t + 0.5
    in_soul = lambda t: any(a <= t <= b for a, b in soul_spans)
    gained = sum(1 for _, e in cc_events if e in ("SPELL_AURA_APPLIED", "SPELL_AURA_APPLIED_DOSE", "CAPPED"))
    capped = [t for t, e in cc_events if e == "CAPPED"]
    clearcasting = {
        "max_stacks": CLEARCASTING_MAX,
        "gained": gained,
        "wasted": [{"t": rnd(t - t0), "in_soul": in_soul(t)} for t in capped],
    }

    # Arcane Salvo
    barrages = [st.get("Arcane Salvo", 0) for _, s, st in casts if s == "Arcane Barrage" and not st.get("Arcane Soul", 0)]
    hi = [{"t": rnd(t - t0), "salvo": st.get("Arcane Salvo", 0), "clearcasting": st.get("Clearcasting", 0),
           "charges": st["_charges"]}
          for t, s, st in casts if s == "Arcane Missiles" and st.get("Arcane Salvo", 0) >= 12 and not st.get("Arcane Soul", 0)]
    salvo = {
        "barrages_outside_soul": len(barrages),
        "avg_at_barrage": round(sum(barrages) / len(barrages), 1) if barrages else None,
        "barrages_under_12": sum(1 for b in barrages if b < 12),
        "missiles_at_12_plus": hi,
    }

    alive = r["fight_len"]
    cooldowns = []
    for name, cd in (("Touch of the Magi", 45), ("Arcane Surge", 90)):
        times = [t - t0 for t, s, _ in casts if s == name]
        possible = 1 + int(max(0, alive - (times[0] if times else 0)) // cd) if alive > 5 else 0
        drift = [b - a - cd for a, b in zip(times, times[1:]) if b - a - cd > 3]
        cooldowns.append({"spell": name, "cooldown": cd, "uses": [rnd(t) for t in times], "possible": possible,
                          "held_past_cooldown": [rnd(d) for d in drift]})

    priority = {
        "matched": agree,
        "total": total,
        "mismatches": [{"expected": want, "pressed": got, "count": len(where), "casts": where}
                       for (want, got), where in sorted(mismatches.items(), key=lambda kv: -len(kv[1]))],
    }
    spec = {"spec": "arcane_sunfury", "priority": priority, "arcane_soul": soul, "clearcasting": clearcasting,
            "salvo": salvo, "cooldowns": cooldowns}
    return spec, verdicts


SPECS = {  # COMBATANT_INFO spec IDs
    250: "Blood", 251: "Frost Death Knight", 252: "Unholy", 577: "Havoc", 581: "Vengeance",
    102: "Balance", 103: "Feral", 104: "Guardian", 105: "Restoration Druid",
    1467: "Devastation", 1468: "Preservation", 1473: "Augmentation", 253: "Beast Mastery", 254: "Marksmanship",
    255: "Survival", 62: "Arcane", 63: "Fire", 64: "Frost Mage", 268: "Brewmaster", 270: "Mistweaver",
    269: "Windwalker", 65: "Holy Paladin", 66: "Protection Paladin", 70: "Retribution", 256: "Discipline",
    257: "Holy Priest", 258: "Shadow", 259: "Assassination", 260: "Outlaw", 261: "Subtlety",
    262: "Elemental", 263: "Enhancement", 264: "Restoration Shaman", 265: "Affliction", 266: "Demonology",
    267: "Destruction", 71: "Arms", 72: "Fury", 73: "Protection Warrior",
}
SUPPORTED_BUILD = "Arcane Sunfury with the Season 2 4-piece"
ARCANE = 62
SUNFURY_AURAS = {"Spellfire Sphere", "Arcane Soul"}  # buffs only Sunfury gains
SET_4PC_AURA = "Cumulative Power"                    # the 4-piece grants it on every Missiles channel
MISSILES_PROOF = 3
SPEC_CHECKS = {"arcane_sunfury_4pc": check_arcane_sunfury}


def pull_signals(enc, guid):
    """(spec ID from this pull's COMBATANT_INFO or None, buffs seen, Arcane Missiles casts)."""
    spec_id, auras, missiles = None, set(), 0
    for _, f in enc.events:
        if f[0] == "COMBATANT_INFO" and f[1] == guid and f[2].isdigit() and int(f[2]) in SPECS:
            spec_id = int(f[2])
        elif f[0].startswith("SPELL_AURA_") and len(f) > 10 and f[5] == guid:
            auras.add(f[10])  # any aura event: a buff already up at the pull start only logs DOSE/REFRESH
        elif f[0] == "SPELL_CAST_SUCCESS" and len(f) > 10 and f[1] == guid and f[10] == "Arcane Missiles":
            missiles += 1
    return spec_id, auras, missiles


def detect_builds(encounters, guid):
    """Per pull: [(build, spec check function or None)].

    The spec comes from the pull's own COMBATANT_INFO (else the last one seen). Hero tree and 4-piece come from
    the pull's buffs, so a mid-log gear or talent swap is caught. Only when a pull shows no signal either way
    (no Sunfury buff; no Missiles cast to prove the 4-piece absent) do other pulls with the same spec decide.
    """
    signals = [pull_signals(e, guid) for e in encounters]
    # Carry the spec forward; pulls before the first COMBATANT_INFO take the first one seen.
    last = next((s for s, _, _ in signals if s), None)
    specs = []
    for spec_id, _, _ in signals:
        last = spec_id or last
        specs.append(last)
    by_spec = defaultdict(set)
    for spec_id, (_, auras, _) in zip(specs, signals):
        by_spec[spec_id] |= auras
    out = []
    for spec_id, (_, auras, missiles) in zip(specs, signals):
        same_spec = by_spec[spec_id]
        # A pull can't prove Sunfury absent (Spheres and Soul may just not have come up), so fall back freely.
        # Known limit: a Sunfury -> Spellslinger swap within one spec isn't caught (#10).
        sunfury = spec_id == ARCANE and bool((auras | same_spec) & SUNFURY_AURAS)
        if SET_4PC_AURA in auras:
            set_4pc = True
        elif missiles >= MISSILES_PROOF:  # enough channels that a missing Cumulative Power is no fluke
            set_4pc = False
        else:
            set_4pc = SET_4PC_AURA in same_spec
        if spec_id is None:
            reason = "spec_unknown"
        elif spec_id != ARCANE:
            reason = "not_arcane"
        elif not sunfury:
            reason = "sunfury_not_detected"
        elif not set_4pc:
            reason = "no_4pc"
        else:
            reason = None
        build = {
            "spec_id": spec_id,
            "spec_name": SPECS.get(spec_id),
            "hero_tree": "Sunfury" if sunfury else None,
            "set_4pc": set_4pc,
            "supported": reason is None,
            "unsupported_reason": reason,
        }
        out.append((build, SPEC_CHECKS["arcane_sunfury_4pc"] if reason is None else None))
    return out


UNSUPPORTED_TEXT = {
    "spec_unknown": "spec unknown",
    "not_arcane": None,
    "sunfury_not_detected": "Arcane, Sunfury not detected",
    "no_4pc": "Arcane Sunfury, no Season 2 4-piece",
}


def build_line(b):
    if b["supported"]:
        return f"  Build: {b['spec_name']} {b['hero_tree']}, Season 2 4-piece"
    have = UNSUPPORTED_TEXT[b["unsupported_reason"]] or b["spec_name"]
    return f"  Build: {have} - spec coaching only supports {SUPPORTED_BUILD}; showing generic stats"


STATE_KEYS = {"Arcane Salvo": "salvo", "Clearcasting": "clearcasting", "Arcane Soul": "arcane_soul",
              "Cumulative Power": "cumulative_power", "Prismatic Bolt!": "prismatic_bolt"}


def summarize(enc, guid, name, build, checks):
    """Everything known about one player in one pull, as plain JSON-ready data. Times are seconds from pull start."""
    r = analyze(enc, guid)
    t0, alive = enc.start, r["fight_len"]
    counts = Counter(s for _, s, _ in r["casts"])
    rot = [c for c in r["casts"] if c[1] not in NON_ROTATIONAL]
    spec, verdicts = checks(r, t0) if checks else (None, {})
    timeline = []
    for i, (t, spell, st) in enumerate(r["casts"]):
        entry = {"t": rnd(t - t0), "spell": spell,
                 "state": {"charges": st["_charges"], **{k: st.get(a, 0) for a, k in STATE_KEYS.items()}}}
        if i in verdicts:
            want, why, ok = verdicts[i]
            entry.update(expected=want, reason=why, matched=ok)
        timeline.append(entry)
    return {
        "name": name,
        "guid": guid,
        "build": build,
        "time_alive": rnd(alive),
        "died_at": rnd(r["death"]) if r["death"] is not None else None,
        "casts": len(rot),
        "casts_per_minute": round(60 * len(rot) / alive, 1) if alive > 0 else None,
        "idle": {"threshold": IDLE_THRESHOLD, "total": rnd(r["idle"]),
                 "gaps": [{"t": rnd(at), "duration": rnd(d)} for at, d in r["gaps"]]},
        "abilities": dict(counts.most_common()),
        "failed_casts": [{"spell": s, "reason": why, "count": n} for (s, why), n in r["failed"].most_common()],
        "spec_checks": spec,
        "windows": {name: [{"start": rnd(a - t0), "end": rnd(b - t0)} for a, b in spans]
                    for name, spans in sorted(r["windows"].items())},
        "series": {key: [[rnd(t - t0), n] for t, n in points] for key, points in sorted(r["series"].items())},
        "timeline": timeline,
    }


def print_report(label, p):
    print(f"\n=== {label}  [{p['name'].split('-')[0]}]")
    alive = p["time_alive"]
    note = f" (you died at {p['died_at']:.0f}s)" if p["died_at"] is not None else ""
    print(f"  Time alive in fight: {alive:.0f}s{note}")
    print(build_line(p["build"]))
    if alive > 0:
        print(f"  Casts: {p['casts']}  ({p['casts_per_minute']:.0f} per minute)")
        print(f"  Idle time (gaps > {IDLE_THRESHOLD:.0f}s with nothing cast): {p['idle']['total']:.1f}s = {100 * p['idle']['total'] / alive:.0f}% of the fight")
    for g in sorted(p["idle"]["gaps"], key=lambda g: -g["duration"])[:5]:
        print(f"    - {g['duration']:4.1f}s gap at {fmt_t(g['t'])}")
    print("  Ability usage:")
    for spell, n in p["abilities"].items():
        print(f"    {n:3d}  {spell}")
    if p["spec_checks"]:
        print("  Spec checks:")
        for line in sunfury_lines(p["spec_checks"]):
            print(line)
    if p["failed_casts"]:
        print("  Failed casts:")
        for f in p["failed_casts"]:
            print(f"    {f['count']}x {f['spell']}: {f['reason']}")


def sunfury_lines(c):
    out = []
    for w in c["arcane_soul"]:
        inside = [s.replace("Arcane ", "") for s in w["casts"]]
        out.append(f"  Arcane Soul at {fmt_t(w['t'])}: {' > '.join(inside) or 'nothing cast'}")
    cc = c["clearcasting"]
    in_soul = sum(1 for w in cc["wasted"] if w["in_soul"])
    out.append(f"  Clearcasting: {cc['gained']} gained, {len(cc['wasted'])} wasted at {cc['max_stacks']} stacks "
               f"({len(cc['wasted']) - in_soul} outside Arcane Soul, {in_soul} during/just after Soul)")
    sv = c["salvo"]
    if sv["barrages_outside_soul"]:
        out.append(f"  Arcane Salvo at Barrage (outside Soul): avg {sv['avg_at_barrage']:.0f}, "
                   f"{sv['barrages_under_12']} of {sv['barrages_outside_soul']} Barrages under 12 stacks")
    hi = sv["missiles_at_12_plus"]
    out.append(f"  Missiles started at 12+ Salvo (outside Soul): {len(hi)} (priority: Barrage first at 4 charges, then Missiles)")
    if hi:
        out.append("        e.g. " + "; ".join(f"{fmt_t(m['t'])} Salvo {m['salvo']}, CC {m['clearcasting']}, "
                                              f"~{m['charges']} charges" for m in hi[:4]))
    for cd in c["cooldowns"]:
        line = f"  {cd['spell']}: {len(cd['uses'])}/{cd['possible']} possible uses" + (f", first at {fmt_t(cd['uses'][0])}" if cd["uses"] else "")
        if cd["held_past_cooldown"]:
            line += f", held {', '.join(f'{d:.0f}s' for d in cd['held_past_cooldown'])} past cooldown"
        out.append(line)
    pr = c["priority"]
    if pr["total"]:
        out.append(f"  Matched the SimC priority on {pr['matched']}/{pr['total']} rotational casts ({100 * pr['matched'] / pr['total']:.0f}%)")
        for m in pr["mismatches"]:
            out.append(f"    {m['count']:2d}x pressed {m['pressed']}, priority says {m['expected']}")
            examples = "; ".join(f"{fmt_t(x['t'])} ({x['reason']})" for x in m["casts"][:3])
            out.append(f"        e.g. {examples}")
    return out


def pull_info(i, enc):
    return {"number": i, "boss": enc.name, "encounter_id": int(enc.id),
            "difficulty": DIFFICULTY.get(enc.difficulty, str(enc.difficulty)),
            "result": {True: "kill", False: "wipe", None: "incomplete"}[enc.success],
            "start": datetime.fromtimestamp(enc.start).isoformat(timespec="seconds"),
            "duration": rnd(enc.duration), "label": enc.label}


def log_started(path):
    """Session start from the name (WoWCombatLog-MMDDYY_HHMMSS.txt); the Archon app's archive resets mtime."""
    m = re.search(r"WoWCombatLog-(\d{6}_\d{6})\.txt$", path)
    return datetime.strptime(m.group(1), "%m%d%y_%H%M%S") if m else datetime.fromtimestamp(os.path.getmtime(path))


def newest_log(log_dir=None):
    """Newest combat log, also finding ones the Archon app moved to Logs/warcraftlogsarchive/Archive-WoWCombatLog-*.txt."""
    override = log_dir or os.environ.get("WOWLOG_DIR")
    dirs = [os.path.expanduser(override)] if override else LOG_DIRS
    # escape: a folder like "D:\WoW [PTR]\Logs" must not be read as a glob pattern
    logs = [p for d in dirs for p in glob.glob(os.path.join(glob.escape(d), "**", "*WoWCombatLog*.txt"), recursive=True)]
    if not logs:
        looked = [d if os.path.isdir(d) else f"{d} (does not exist)" for d in dirs]
        raise SystemExit("No combat logs found. Looked in:\n  " + "\n  ".join(looked) +
                         "\nPass a log file, or the folder with --log-dir (or set WOWLOG_DIR).")
    return max(logs, key=log_started)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", nargs="?")
    ap.add_argument("--player", help="character name (default: the one flagged as yours)")
    ap.add_argument("--boss", help="only report encounters whose name contains this")
    ap.add_argument("--last", action="store_true", help="only report the most recent encounter")
    ap.add_argument("--list", action="store_true", help="list encounters with their numbers and exit")
    ap.add_argument("--pull", type=int, action="append", help="only report pull number N (see --list); repeatable")
    ap.add_argument("--compare", action="append", default=[], help="also report this player, e.g. a same-spec raider")
    ap.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    ap.add_argument("--log-dir", help="folder to search for the newest log (default: the standard retail Logs folder)")
    args = ap.parse_args()
    path = args.logfile or newest_log(args.log_dir)
    players = [find_player(path, args.player)] + [find_player(path, n) for n in args.compare]
    encounters = [e for e in split_encounters(path) if e.duration > 5]
    numbered = list(enumerate(encounters, 1))
    if args.list:
        print(f"Log: {os.path.basename(path)}")
        for i, e in numbered:
            print(f"  #{i:<3} {datetime.fromtimestamp(e.start):%H:%M}  {e.label}")
        return
    if args.pull:
        numbered = [(i, e) for i, e in numbered if i in args.pull]
    if args.boss:
        numbered = [(i, e) for i, e in numbered if args.boss.lower() in e.name.lower()]
    if args.last:
        numbered = numbered[-1:]
    builds = {guid: dict(zip(map(id, encounters), detect_builds(encounters, guid))) for guid, _ in players}
    pulls = [dict(pull_info(i, e), players=[summarize(e, guid, name, *builds[guid][id(e)]) for guid, name in players])
             for i, e in numbered]
    if args.json:
        json.dump({"schema": 1, "log": os.path.basename(path), "log_started": log_started(path).isoformat(),
                   "pulls": pulls}, sys.stdout, indent=1)
        print()
        return
    print(f"Log: {os.path.basename(path)}")
    if not pulls:
        print("No boss encounters in this log yet.")
    for pull in pulls:
        for p in pull["players"]:
            print_report(pull["label"], p)


if __name__ == "__main__":
    main()
