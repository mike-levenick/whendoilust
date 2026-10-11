---
name: arcane-coach
description: Coach an Arcane Mage (Sunfury, Season 2 4-piece) from a World of Warcraft combat log - analyze the pulls against the SimC priority, write human-friendly coaching, and build a self-contained HTML report (shareable, printable to PDF). Use when the user asks to check, analyze, review or coach their last pull, key, raid night, a boss, or a combat log.
argument-hint: "[last pull | session | boss name | pull N | path/to/WoWCombatLog.txt] [character]"
---

# Arcane coach

Turns a WoW advanced combat log into a coached report: `wowlog.py` replays every cast against the SimC
Arcane Sunfury priority, you write the coaching, and `report.py` renders one self-contained HTML file.

The commands below are bash (macOS, Linux, or Git Bash on Windows). In PowerShell, run the same programs
with PowerShell syntax. **If any command fails, stop and report the error**; don't carry on with partial
files.

## 1. Find the tools

The skill may be installed by symlink or by copying its folder, so find the checkout it belongs to by
walking up from the skill's real location (no git needed). Use whichever Python is installed:

```bash
# First interpreter that is real Python 3.9+ (skips the Windows Store and macOS Xcode stubs)
PY=""; for c in python3 python py; do
  command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null && { PY=$c; break; }
done
REPO=$("$PY" -c '
import os, sys
d = os.path.dirname(os.path.realpath(sys.argv[1]))
while d != os.path.dirname(d) and not os.path.exists(os.path.join(d, "wowlog.py")):
    d = os.path.dirname(d)
print(d if os.path.exists(os.path.join(d, "wowlog.py")) else "")' "<this skill's base directory>/SKILL.md")
```

(Write the base directory with forward slashes, also on Windows.)

- `PY` empty: tell the user to install Python 3.9+ (python.org); never install packages.
- `REPO` empty: the skill was copied away from its `whendoilust` checkout. Tell the user to install it as
  the README says (clone, then symlink `.claude/skills/coach` into `~/.claude/skills/arcane-coach`).

## 2. Pick the log, the pulls, and the character

```bash
"$PY" "$REPO/wowlog.py" --list            # newest log; add a path, or --log-dir DIR, if the user gave one
```

- **Log**: the newest one, unless the user named a file or folder. If none is found, the error lists where
  it looked; ask for the path (Windows logs live in `...\World of Warcraft\_retail_\Logs`). Remind them that
  logging needs **Advanced Combat Logging** on and `/combatlog` typed in game.
- **Pulls**: "last pull" means `--last`. "Tonight", "the key", "the session" or no qualifier means every pull
  in the log. A boss name means `--boss NAME`; "pull 3" means `--pull 3` (numbers from `--list`). If that
  is more than 8 pulls, ask whether to coach all of them, the kills only, or one boss; three habits across
  20 pulls get too thin. If the selection is empty ("No boss encounters"), say so and stop.
- **Character**: whoever recorded the log, by default. `--player NAME` only when the user names someone else.
  It is a prefix match, so check that the name in the report header is the one they meant.
- **Comparison**: `--compare NAME` only when the user asks for one. If the run fails with "Couldn't find
  NAME in this log", run again without `--compare` and tell the user that player wasn't in the log.

## 3. Run the analysis

Keep each run's files together under a reports folder:

```bash
OUT="${WHENDOILUST_REPORTS:-$HOME/Documents/whendoilust-reports}"; mkdir -p "$OUT"
```

Name the run `YYYY-MM-DD-<slug>`: the date of the log (from its file name, `WoWCombatLog-MMDDYY_...`) and a
slug of lowercase letters, digits and hyphens only (e.g. `sethraliss-key`, `sentinels-pull-13`). If a file
with that name already exists, add `-2`, `-3`, ... so earlier reports are never overwritten. Then:

```bash
BASE="$OUT/<name>"
"$PY" "$REPO/wowlog.py" <log/pull/player args> --json > "$BASE.json"
"$PY" "$REPO/report.py" "$BASE.json" --text > "$BASE.txt"     # same data, readable; made from the JSON
```

If `$BASE.txt` says "No boss encounters", the selection matched nothing: say so and stop. Otherwise read
`$BASE.txt` for the overview. Pull exact timestamps, Salvo values and counts from the JSON with short
`"$PY" -c` one-liners rather than reading the whole file (a raid night is over 1 MB).

**Build gate.** Spec coaching depends on each pull's `players[0].build.supported`:
- **False in every pull**: no spec coaching. Explain why from `build.unsupported_reason` (not Arcane,
  Sunfury not detected, no 4-piece, spec unknown), write a coaching file with only a `headline` saying so,
  and still render the report for the generic stats.
- **False in some pulls**: coach only the supported pulls, and say in `caveats` which pulls were skipped
  and why.

## 4. Write the coaching

Read `$REPO/docs/coaching.md` and follow its format and writing rules exactly. The essentials:

- A one or two sentence `headline`: the overall verdict and the biggest lever.
- **Up to 3 habits** across the selected pulls, ranked by how much they'd gain; fewer if there genuinely
  aren't three, never padded. Each has `what` (with the real numbers), `instead`, `why` (the mechanic), and
  2-3 `moments`: `pull` = the pull number, `t` = a time copied exactly from the JSON (a cast, a wasted proc, a Soul
  window, an idle gap, a cooldown use). Add `"player": "Name"` for a moment about the comparison player.
- One or two sentences per pull in `pulls`, skipping pulls with nothing to say.
- `caveats` wherever a flagged cast may be correct: the analyzer assumes one target, so "Barrage, priority
  says filler" on add-heavy fights (most M+ bosses) can be right; Arcane Charges are approximate.
- Rotation only, not deaths. Second person. Spec facts from the SimC `midnight` APL and Method.gg, never
  from memory. Every number must come from the report.

Write it to `$BASE.coaching.json`.

## 5. Render and check

```bash
"$PY" "$REPO/report.py" "$BASE.json" --coaching "$BASE.coaching.json" -o "$BASE.html" --strict
```

`--strict` prints every `coaching:` problem and fails: a moment with nothing within 0.05s in that pull, a
note for a pull that isn't in the report, a moment in a pull without spec coaching, more than 3 habits, or
a wrongly shaped file. Fix the coaching and re-run until it passes; never drop `--strict` to get past it,
and never move a moment to an unrelated time just to pass.

Then open it: `open "$BASE.html"` on macOS, `xdg-open "$BASE.html"` on Linux, and on Windows
`cmd.exe //c start "" "$(cygpath -w "$BASE.html")"` from Git Bash (`Start-Process` in PowerShell).

## 6. Reply

In the chat, keep it short: the headline, each habit in one line, and the path to the HTML. Mention it's a
single file they can share or print to PDF (the **Save as PDF** button). Don't paste the whole coaching or
the text report.

## Don'ts

- Don't upload or publish the report anywhere; it stays on the user's machine unless they share it.
- Don't coach builds the analyzer doesn't support, and don't present guesses as data.
- Don't edit the repo's code to make a report work; if the analyzer is wrong, say so.
