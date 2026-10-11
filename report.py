#!/usr/bin/env python3
"""Render a wowlog.py --json report (plus optional coaching) as one self-contained HTML file.

Usage: python3 report.py REPORT.json [--coaching COACHING.json] [-o report.html]

The page makes no network requests: styles, script, data and charts (inline SVG) are all inside the file,
so it can be emailed or dropped in Discord and still work later. Stdlib only.
"""
import argparse
import html
import json
import os
import re
import sys

import wowlog

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")


def embed(obj):
    # JSON inside <script type="application/json">. Escaping every < > & (valid JSON string escapes) means no
    # log or coaching text can end the element or open "<!--<script", which would swallow the real </script>.
    text = json.dumps(obj, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# Seconds between a coaching moment and the report time it cites. Times are copied verbatim from the JSON
# (millisecond precision), so this only absorbs rounding; at 0.5s, half of random times on a busy pull passed.
TOLERANCE = 0.05


def anchor_times(player):
    """Every time a moment may cite: casts, spec-check events (wasted procs, Soul, high-Salvo Missiles,
    mismatches, cooldown uses), window starts and idle gaps."""
    times = [c["t"] for c in player.get("timeline", [])]
    times += [w["start"] for spans in (player.get("windows") or {}).values() for w in spans]
    times += [g["t"] for g in player.get("idle", {}).get("gaps", [])]
    c = player.get("spec_checks") or {}
    times += [w["t"] for w in c.get("clearcasting", {}).get("wasted", [])]
    times += [w["t"] for w in c.get("arcane_soul", [])]
    times += [m["t"] for m in c.get("salvo", {}).get("missiles_at_12_plus", [])]
    times += [x["t"] for m in c.get("priority", {}).get("mismatches", []) for x in m["casts"]]
    times += [t for cd in c.get("cooldowns", []) for t in cd["uses"]]
    return times


def is_number(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return float(v) == float(v)  # not NaN, and not an int too large for a float
    except OverflowError:
        return False


def check_coaching(report, coaching):
    """Problems that mean the coaching isn't grounded in this report; an empty list means it is.

    Never raises on malformed coaching: a wrong shape is reported as a problem, so a writer can fix it.
    """
    if coaching is None:
        return []
    if not isinstance(coaching, dict):
        return ["coaching must be a JSON object (see docs/coaching.md)"]
    problems = []
    pulls = {p["number"]: p for p in report.get("pulls", [])}

    def coached(pull, name=None):
        """The player a moment is about: `name` (prefix match, like --player) or the report's main player."""
        players = pull.get("players") or []
        if name is None:
            return players[0] if players else None
        return next((pl for pl in players if pl["name"].lower().startswith(str(name).lower())), None)

    habits = coaching.get("habits", [])
    if not isinstance(habits, list) or not all(isinstance(h, dict) for h in habits):
        problems.append("habits must be a list of objects")
        habits = []
    if len(habits) > 3:
        problems.append(f"{len(habits)} habits; the report shows the top 3 only")
    supported = [n for n, p in pulls.items() if (coached(p) or {}).get("build", {}).get("supported")]
    if habits and not supported:
        problems.append("habits written for a build without spec coaching (build.supported is false in every pull)")
    for i, h in enumerate(habits, 1):
        moments = h.get("moments", [])
        if not isinstance(moments, list) or not all(isinstance(m, dict) for m in moments):
            problems.append(f"habit {i}: moments must be a list of objects")
            continue
        for m in moments:
            number, t = m.get("pull"), m.get("t")
            pull = pulls.get(number) if isinstance(number, int) and not isinstance(number, bool) else None
            if pull is None:
                problems.append(f"habit {i}: no pull #{number} in this report")
                continue
            player = coached(pull, m.get("player"))
            if player is None:
                problems.append(f"habit {i}: no player {m.get('player')!r} in pull #{number}")
            elif not player["build"]["supported"]:
                problems.append(f"habit {i}: pull #{number} has no spec coaching for {player['name']} "
                                f"({player['build']['unsupported_reason']})")
            elif not is_number(t) or not any(round(abs(a - t), 3) <= TOLERANCE for a in anchor_times(player)):
                problems.append(f"habit {i}: nothing in pull #{number} within {TOLERANCE}s of t={t!r}")
    notes = coaching.get("pulls", {})
    if not isinstance(notes, dict):
        problems.append("pulls must be an object keyed by pull number")
        notes = {}
    for key in notes:
        if not (str(key).isascii() and str(key).isdigit() and str(int(key)) == str(key) and int(key) in pulls):
            problems.append(f"pull note for #{key}, which isn't in this report")
    for key in ("headline",):
        if key in coaching and not isinstance(coaching[key], str):
            problems.append(f"{key} must be a string")
    if "caveats" in coaching and not (isinstance(coaching["caveats"], list)
                                      and all(isinstance(c, str) for c in coaching["caveats"])):
        problems.append("caveats must be a list of strings")
    return problems


def render(report, coaching=None):
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    players = {p["name"] for pull in report["pulls"] for p in pull["players"]}
    who = ", ".join(sorted(n.split("-")[0] for n in players)) or "No player"
    title = f"{who} - {report['log_started'][:10]}"
    values = {"TITLE": html.escape(title), "REPORT": embed(report), "COACHING": embed(coaching)}
    # One pass, so substituted text (which may contain "{{REPORT}}" in a player name) is never re-expanded.
    return re.sub(r"\{\{(TITLE|REPORT|COACHING)\}\}", lambda m: values[m.group(1)], page)


def load(path, what):
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"Couldn't read the {what} file {path}: {e}")


def load_report(path):
    r = load(path, "report")
    if not (isinstance(r, dict) and isinstance(r.get("pulls"), list)):
        raise SystemExit(f"{path} isn't a wowlog.py --json report")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", help="output of wowlog.py --json ('-' for stdin)")
    ap.add_argument("--coaching", help="coaching JSON written for this report (see docs/coaching.md)")
    ap.add_argument("-o", "--output", default="report.html")
    ap.add_argument("--strict", action="store_true", help="fail instead of warn when the coaching doesn't match the report")
    ap.add_argument("--text", action="store_true", help="print the text report for REPORT.json instead of rendering HTML")
    args = ap.parse_args()
    report = load_report(args.report)
    if args.text:  # the same text report wowlog.py prints, from this exact JSON
        print(f"Log: {report.get('log', '?')}")
        if not report["pulls"]:
            print("No boss encounters in this log yet.")
        for pull in report["pulls"]:
            for p in pull["players"]:
                wowlog.print_report(pull["label"], p)
        return
    coaching = load(args.coaching, "coaching") if args.coaching else None
    problems = check_coaching(report, coaching)
    for p in problems:
        print(f"coaching: {p}", file=sys.stderr)
    if problems and args.strict:
        raise SystemExit(1)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(render(report, coaching))
    print(os.path.abspath(args.output))


if __name__ == "__main__":
    main()
