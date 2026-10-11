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
    args = ap.parse_args()
    report = json.load(sys.stdin if args.report == "-" else open(args.report, encoding="utf-8"))
    coaching = json.load(open(args.coaching, encoding="utf-8")) if args.coaching else None
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(render(report, coaching))
    print(os.path.abspath(args.output))


if __name__ == "__main__":
    main()
