#!/usr/bin/env python3
"""Parse a WoW advanced combat log and report on one player's performance per encounter.

Usage: python3 wowlog.py [LOGFILE] [--player NAME]
Defaults to the newest WoWCombatLog in the retail Logs folder and the player flagged as "mine" (0x511).
"""
import argparse
import csv
import glob
import os
from collections import Counter, defaultdict
from datetime import datetime

LOG_DIR = "/Applications/World of Warcraft/_retail_/Logs"
MINE_FLAG = 0x511  # affiliation mine + reaction friendly + controlled by player + type player
GCD = 1.5          # base GCD; haste shrinks it, so gaps above this are a conservative "idle" measure
IDLE_THRESHOLD = 2.0
DIFFICULTY = {14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR", 8: "M+"}
POWER_ARCANE_CHARGES = 16
SALVO_MAX = 25
CLEARCASTING_MAX = 3
REACTION = 0.4  # seconds; a proc that appeared this recently before a cast isn't counted as "available"
PROCS = {"Clearcasting", "Prismatic Bolt!", "Arcane Soul"}  # reaction allowance applies to these, not to resources

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
                continue  # nested bracket structure; not needed yet
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


def report_arcane_sunfury(r, t0):
    """Arcane Mage (Sunfury) checks: replay each GCD against the SimC priority (see sunfury_choice)."""
    out = []
    casts = r["casts"]
    tom_casts = [t for t, s, _ in casts if s == "Touch of the Magi"]

    def tom_ready(t):
        prior = [x for x in tom_casts if x <= t]
        return not prior or t - prior[-1] >= 45

    mismatches = defaultdict(list)
    agree = total = 0
    for t, spell, st in casts:
        if spell not in ROTATIONAL:
            continue
        want, why = sunfury_choice(st, tom_ready(t))
        got = "filler" if spell in ("Arcane Blast", "Arcane Orb", "Arcane Pulse") else spell
        total += 1
        if want == got:
            agree += 1
        else:
            mismatches[(want, spell)].append(f"{fmt_t(t - t0)} ({why})")

    # Arcane Soul windows: what was pressed inside each
    soul_casts = [(t, s, st) for t, s, st in casts if st.get("Arcane Soul", 0) and s in ROTATIONAL]
    windows = [t for t, e in r["auras"].get("Arcane Soul", []) if e == "SPELL_AURA_APPLIED"]
    for w in windows:
        inside = [s.replace("Arcane ", "") for t, s, _ in soul_casts if 0 <= t - w <= 4.5]
        out.append(f"  Arcane Soul at {fmt_t(w - t0)}: {' > '.join(inside) or 'nothing cast'}")

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
    capped_soul = sum(1 for t in capped if in_soul(t))
    out.append(f"  Clearcasting: {gained} gained, {len(capped)} wasted at {CLEARCASTING_MAX} stacks "
               f"({len(capped) - capped_soul} outside Arcane Soul, {capped_soul} during/just after Soul)")

    # Arcane Salvo
    barrages = [st.get("Arcane Salvo", 0) for _, s, st in casts if s == "Arcane Barrage" and not st.get("Arcane Soul", 0)]
    if barrages:
        out.append(f"  Arcane Salvo at Barrage (outside Soul): avg {sum(barrages) / len(barrages):.0f}, "
                   f"{sum(1 for b in barrages if b < 12)} of {len(barrages)} Barrages under 12 stacks")
    hi = [(t, st) for t, s, st in casts if s == "Arcane Missiles" and st.get("Arcane Salvo", 0) >= 12
          and not st.get("Arcane Soul", 0)]
    hi_missiles = len(hi)
    out.append(f"  Missiles started at 12+ Salvo (outside Soul): {hi_missiles} (priority: Barrage first at 4 charges, then Missiles)")
    if hi:
        out.append("        e.g. " + "; ".join(f"{fmt_t(t - t0)} Salvo {st.get('Arcane Salvo', 0)}, CC {st.get('Clearcasting', 0)}, "
                                              f"~{st['_charges']} charges" for t, st in hi[:4]))

    alive = r["fight_len"]
    for name, cd in (("Touch of the Magi", 45), ("Arcane Surge", 90)):
        times = [t - t0 for t, s, _ in casts if s == name]
        possible = 1 + int(max(0, alive - (times[0] if times else 0)) // cd) if alive > 5 else 0
        drift = [b - a - cd for a, b in zip(times, times[1:]) if b - a - cd > 3]
        line = f"  {name}: {len(times)}/{possible} possible uses" + (f", first at {fmt_t(times[0])}" if times else "")
        if drift:
            line += f", held {', '.join(f'{d:.0f}s' for d in drift)} past cooldown"
        out.append(line)

    if total:
        out.append(f"  Matched the SimC priority on {agree}/{total} rotational casts ({100 * agree / total:.0f}%)")
        for (want, got), where in sorted(mismatches.items(), key=lambda kv: -len(kv[1])):
            out.append(f"    {len(where):2d}x pressed {got}, priority says {want}")
            out.append(f"        e.g. {'; '.join(where[:3])}")
    return out


SPEC_REPORTS = {"Arcane Missiles": report_arcane_sunfury}  # crude spec detection by signature spell


def report(enc, guid, name):
    r = analyze(enc, guid)
    print(f"\n=== {enc.label}  [{name.split('-')[0]}]")
    alive = r["fight_len"]
    note = f" (you died at {r['death']:.0f}s)" if r["death"] is not None else ""
    print(f"  Time alive in fight: {alive:.0f}s{note}")
    rot = [c for c in r["casts"] if c[1] not in NON_ROTATIONAL]
    if alive > 0:
        print(f"  Casts: {len(rot)}  ({60 * len(rot) / alive:.0f} per minute)")
        print(f"  Idle time (gaps > {IDLE_THRESHOLD:.0f}s with nothing cast): {r['idle']:.1f}s = {100 * r['idle'] / alive:.0f}% of the fight")
    for at, dur in sorted(r["gaps"], key=lambda g: -g[1])[:5]:
        print(f"    - {dur:4.1f}s gap at {int(at // 60)}:{at % 60:04.1f}")

    counts = Counter(s for _, s, _ in r["casts"])
    print("  Ability usage:")
    for spell, n in counts.most_common():
        print(f"    {n:3d}  {spell}")
    for spell, fn in SPEC_REPORTS.items():
        if spell in counts:
            print("  Spec checks:")
            for line in fn(r, enc.start):
                print(line)
    if r["failed"]:
        print("  Failed casts:")
        for (spell, why), n in r["failed"].most_common():
            print(f"    {n}x {spell}: {why}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", nargs="?")
    ap.add_argument("--player", help="character name (default: the one flagged as yours)")
    ap.add_argument("--boss", help="only report encounters whose name contains this")
    ap.add_argument("--last", action="store_true", help="only report the most recent encounter")
    ap.add_argument("--list", action="store_true", help="list encounters with their numbers and exit")
    ap.add_argument("--pull", type=int, action="append", help="only report pull number N (see --list); repeatable")
    ap.add_argument("--compare", action="append", default=[], help="also report this player, e.g. a same-spec raider")
    args = ap.parse_args()
    path = args.logfile or max(glob.glob(os.path.join(LOG_DIR, "WoWCombatLog*.txt")), key=os.path.getmtime)
    players = [find_player(path, args.player)] + [find_player(path, n) for n in args.compare]
    print(f"Log: {os.path.basename(path)}")
    encounters = [e for e in split_encounters(path) if e.duration > 5]
    numbered = list(enumerate(encounters, 1))
    if args.list:
        for i, e in numbered:
            print(f"  #{i:<3} {datetime.fromtimestamp(e.start):%H:%M}  {e.label}")
        return
    if args.pull:
        encounters = [e for i, e in numbered if i in args.pull]
    if args.boss:
        encounters = [e for e in encounters if args.boss.lower() in e.name.lower()]
    if args.last:
        encounters = encounters[-1:]
    if not encounters:
        print("No boss encounters in this log yet.")
    for enc in encounters:
        for guid, name in players:
            report(enc, guid, name)


if __name__ == "__main__":
    main()
