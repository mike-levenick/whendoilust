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

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")


def embed(obj):
    # JSON inside <script type="application/json">. Escaping every < > & (valid JSON string escapes) means no
    # log or coaching text can end the element or open "<!--<script", which would swallow the real </script>.
    text = json.dumps(obj, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def check_coaching(report, coaching):
    """Problems that mean the coaching isn't grounded in this report; an empty list means it is."""
    if not coaching:
        return []
    problems = []
    pulls = {p["number"]: p for p in report["pulls"]}
    if not isinstance(coaching, dict):
        return ["coaching must be a JSON object (see docs/coaching.md)"]
    habits = coaching.get("habits") or []
    if len(habits) > 3:
        problems.append(f"{len(habits)} habits; the report shows the top 3 only")
    supported = any(pl["build"]["supported"] for p in report["pulls"] for pl in p["players"])
    if habits and not supported:
        problems.append("habits written for a build without spec coaching (build.supported is false everywhere)")
    for i, h in enumerate(habits, 1):
        for m in h.get("moments") or []:
            pull = pulls.get(m.get("pull"))
            if pull is None:
                problems.append(f"habit {i}: no pull #{m.get('pull')} in this report")
                continue
            t = m.get("t")
            casts = [c["t"] for c in pull["players"][0]["timeline"]]
            if not isinstance(t, (int, float)) or not any(abs(c - t) <= 0.5 for c in casts):
                problems.append(f"habit {i}: pull #{pull['number']} has no cast within 0.5s of t={t}")
    for number in (coaching.get("pulls") or {}):
        if not str(number).isdigit() or int(number) not in pulls:
            problems.append(f"pull note for #{number}, which isn't in this report")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("report", help="output of wowlog.py --json ('-' for stdin)")
    ap.add_argument("--coaching", help="coaching JSON written for this report (see CLAUDE.md)")
    ap.add_argument("-o", "--output", default="report.html")
    ap.add_argument("--strict", action="store_true", help="fail instead of warn when the coaching doesn't match the report")
    args = ap.parse_args()
    report = json.load(sys.stdin if args.report == "-" else open(args.report, encoding="utf-8"))
    coaching = json.load(open(args.coaching, encoding="utf-8")) if args.coaching else None
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
