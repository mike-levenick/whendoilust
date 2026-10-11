---
name: arcane-coach
description: Coach an Arcane Mage (Sunfury, Season 2 4-piece) from a World of Warcraft combat log - analyze the pulls against the SimC priority, write human-friendly coaching, and build a self-contained HTML report (shareable, printable to PDF). Use when the user asks to check, analyze, review or coach their last pull, key, raid night, a boss, or a combat log.
argument-hint: "[last pull | session | boss name | pull N | path/to/WoWCombatLog.txt] [character]"
---

# Arcane coach

Turns a WoW advanced combat log into a coached report: `wowlog.py` replays every cast against the SimC
Arcane Sunfury priority, you write the coaching, and `report.py` renders one self-contained HTML file.

## 1. Find the tools

The skill may be installed by symlink, so resolve its real location and use the repo it lives in:

```bash
SKILL_FILE="<this skill's base directory>/SKILL.md"
REPO=$(cd "$(dirname "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SKILL_FILE")")" && git rev-parse --show-toplevel)
ls "$REPO/wowlog.py" "$REPO/report.py" "$REPO/docs/coaching.md"
```

If any is missing, stop and say the skill must stay inside its `whendoilust` checkout (install it with the
symlink in the README). Python 3.9+ is the only requirement; never install packages.

## 2. Pick the log, the pulls, and the character

```bash
python3 "$REPO/wowlog.py" --list            # newest log; add a path, or --log-dir DIR, if the user gave one
```

- **Log**: the newest one, unless the user named a file or folder. If none is found, the error lists where
  it looked; ask for the path (Windows logs live in `...\World of Warcraft\_retail_\Logs`). Remind them
  that logging needs **Advanced Combat Logging** on and `/combatlog` typed in game.
- **Pulls**: "last pull" means the last one (`--last`). "Tonight", "the key", "the session" or no
  qualifier means every pull in the log. A boss name means `--boss NAME`. "Pull 3" means `--pull 3`. Don't
  ask when the request maps cleanly onto one of these.
- **Character**: whoever recorded the log, by default. Use `--player NAME` only when the user names someone
  else. Add `--compare NAME` only if the user asks for a comparison *and* that player is in the log.

## 3. Run the analysis

Save everything under a reports folder so past sessions are kept:

```bash
OUT="${WHENDOILUST_REPORTS:-$HOME/Documents/whendoilust-reports}"; mkdir -p "$OUT"
BASE="$OUT/<log date>-<short slug, e.g. sethraliss-key or sentinels-pull13>"
python3 "$REPO/wowlog.py" <log/pull args> > "$BASE.txt"
python3 "$REPO/wowlog.py" <same args> --json > "$BASE.json"
```

Read `$BASE.txt` for the overview: it is the same data as the JSON in a readable form. Pull exact
timestamps, Salvo values and counts from the JSON with short `python3 -c` one-liners rather than reading
the whole file (a raid night is over 1 MB).

**Build gate.** Each player in each pull has `build.supported`. If it's false everywhere, there is no spec
coaching: say why (`build.unsupported_reason`: not Arcane, Sunfury not detected, no 4-piece, spec
unknown), write a coaching file with only a `headline` saying so, and still render the report for the
generic stats.

## 4. Write the coaching

Read `$REPO/docs/coaching.md` and follow its format and writing rules exactly. The essentials:

- A one or two sentence `headline`: the overall verdict and the biggest lever.
- **Top 3 habits** across the selected pulls, ranked by how much they'd gain. Each has `what` (with the
  real numbers), `instead`, `why` (the mechanic), and 2-3 `moments` copied from real casts in the JSON
  (`pull` = the pull number, `t` = the cast's `t`).
- One or two sentences per pull in `pulls`, skipping pulls with nothing to say.
- `caveats` wherever a flagged cast may be correct: the analyzer assumes one target, so "Barrage, priority
  says filler" on add-heavy fights (most M+ bosses) can be right; Arcane Charges are approximate.
- Rotation only, not deaths. Second person. Spec facts from the SimC `midnight` APL and Method.gg, never
  from memory. Every number must come from the report.

Write it to `$BASE.coaching.json`.

## 5. Render and check

```bash
python3 "$REPO/report.py" "$BASE.json" --coaching "$BASE.coaching.json" -o "$BASE.html" --strict
```

`--strict` fails if a moment doesn't match a real cast, a pull note names a pull that isn't in the report,
or there are habits for an unsupported build. Fix the coaching and re-run until it passes; never drop
`--strict` to get past it.

Then open it: `open "$BASE.html"` on macOS, `start "" "$BASE.html"` on Windows, `xdg-open` on Linux.

## 6. Reply

In the chat, keep it short: the headline, the three habits as one line each, and the path to the HTML.
Mention it's a single file they can share or print to PDF (the **Save as PDF** button). Don't paste the
whole coaching or the text report.

## Don'ts

- Don't upload or publish the report anywhere; it stays on the user's machine unless they share it.
- Don't coach builds the analyzer doesn't support, and don't present guesses as data.
- Don't edit the repo's code to make a report work; if the analyzer is wrong, say so.
