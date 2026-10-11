# whendoilust

A rotation coach for **Arcane Mage (Sunfury, Midnight Season 2 4-piece)** built
from World of Warcraft advanced combat logs. It replays every cast against the
SimulationCraft priority list and reports wasted procs, downtime, cooldown
timing, and where your button presses differed from the priority.

> Work in progress; see the
> [v1 milestone](https://github.com/mike-levenick/whendoilust/milestone/1).

## Use it as a Claude Code skill

The repo includes an `arcane-coach` skill: ask Claude Code to "check my last pull" or "coach tonight's key"
and it analyzes your newest log, writes coaching, and opens a self-contained HTML report (shareable, and
printable to PDF). Reports are saved under `~/Documents/whendoilust-reports/` (override with
`$WHENDOILUST_REPORTS`).

```
git clone https://github.com/mike-levenick/whendoilust.git
ln -s "$PWD/whendoilust/.claude/skills/coach" ~/.claude/skills/arcane-coach   # use it from any folder
```

(Or just run Claude Code inside the clone; the skill is picked up there automatically.) On Windows, create
the link with `mklink /D` or copy the folder path into your skills directory.

## Requirements

- Python 3.9+ (standard library only)
- **Advanced Combat Logging** enabled in WoW (*Options → System → Network*), and
  logging turned on with `/combatlog`
- Only Arcane Sunfury with the Season 2 4-piece gets spec coaching today; other
  specs see generic stats only.

## Usage

```
python3 wowlog.py --list              # numbered boss pulls in the newest log
python3 wowlog.py --last              # most recent pull
python3 wowlog.py --pull 3            # a specific pull
python3 wowlog.py --boss Avatar       # every pull of a boss
python3 wowlog.py --last --json       # machine-readable report
python3 wowlog.py path/to/WoWCombatLog-*.txt --compare SomeMage   # another player in the same log

python3 wowlog.py --json > report.json
python3 report.py report.json -o report.html   # self-contained HTML report (add --coaching, see docs/coaching.md)
```

By default it reads the newest log in the retail `Logs` folder, including logs
the Warcraft Logs / Archon uploader has moved to `Logs/warcraftlogsarchive/`,
and analyzes the character who recorded the log.

## Development

```
./scripts/install-hooks.sh                    # Conventional Commits hook
python3 -m unittest discover -s tests -v
```

`main` is protected; changes land through squash-merged PRs. See `CLAUDE.md`.

## License

MIT
