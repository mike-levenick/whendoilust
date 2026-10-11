# whendoilust — WoW combat log rotation analyzer

Personal tool for improving the user's Arcane Mage rotation from WoW advanced combat logs (Hekili stopped working when 12.x broke addon APIs).

## Who / what
- Character: **Whendoilust-Sargeras-US**, Arcane Mage, **Sunfury** hero tree, Season 2 4-piece (Cumulative Power), Heroic raid.
- Comparison player: **Zaffira-Mannoroth-US**, another Arcane Sunfury mage in the same raid (`--compare Zaffira`).
- The user cares about **rotation/priority execution, not death analysis** — deaths are obvious to them.

## Running
```
python3 wowlog.py --list                       # numbered pulls in the newest log
python3 wowlog.py --last                       # most recent pull
python3 wowlog.py --last --compare Zaffira     # ...plus another player in the same log
python3 wowlog.py --pull 13                    # a specific pull
python3 wowlog.py --boss Sentinels             # all pulls of a boss
python3 wowlog.py --last --json                # same report as JSON (schema 1), for the dashboard
```
JSON: `{schema, log, log_started, pulls: [{number, boss, difficulty, result, start, duration, players: [{name, build, idle, abilities, failed_casts, spec_checks, timeline}]}]}`. Times are seconds from pull start. `build` = `{spec_id (int|null), spec_name (str|null), hero_tree ("Sunfury"|null = not detected), set_4pc (bool), supported (bool), unsupported_reason (null|"spec_unknown"|"not_arcane"|"sunfury_not_detected"|"no_4pc")}`. Gate on `build.supported`: `spec_checks` is null and timeline entries have no `expected`/`reason`/`matched` when it is false. `timeline` has every cast with its state (charges, salvo, clearcasting, arcane_soul, cumulative_power, prismatic_bolt) and, for rotational casts of a supported build, `expected`/`reason`/`matched` against the SimC priority. The text report is rendered from the same data (`summarize` → `print_report`).
Logs: `/Applications/World of Warcraft/_retail_/Logs/WoWCombatLog-*.txt` on macOS, `C:\Program Files (x86)\World of Warcraft\_retail_\Logs` on Windows; `--log-dir` / `$WOWLOG_DIR` override (newest by the date in the filename is used by default; pass a path to override). The Archon uploader archives logs untouched for 3 days into `Logs/warcraftlogsarchive/Archive-WoWCombatLog-*.txt` (uncompressed, but mtime reset), and the script searches there too. Safe to run while the game is still logging. "Check last pull" from the user means run `--last` and summarize the rotation findings; add `--compare Zaffira` only when Zaffira is in the log (they're absent from most runs, so nothing should be built specifically for them).

## Spec model (patch 12.1, Midnight Season 2) — verified from log + sources
- Sources: SimC APL `ActionPriorityLists/default/mage_arcane.simc` on the `midnight` branch (generated 2026-09-06); Method.gg Arcane 12.1 guide (updated 2026-09-13). Icy Veins blocks fetches; Wowhead guide bodies don't render via fetch, but spell pages do.
- Clearcasting max 3 stacks; gaining at cap logs a lone SPELL_AURA_REFRESH (= wasted proc). A REFRESH is also logged at the same timestamp as every APPLIED_DOSE/REMOVED_DOSE — those are not waste.
- Arcane Salvo max 25, +3% Barrage damage per stack; Missiles builds it, Barrage consumes it. Gains at 25 are silent (no event).
- Arcane Soul (Sunfury, after the phoenix): ~4s; each Barrage during it gives +5 Salvo and +1 Clearcasting and does not consume Salvo. Barrage spam; CC capping inside Soul is expected.
- Cumulative Power (4pc): builds to 8 during a Missiles channel, expires ~2.5s after; consumed by Prismatic Bolt. "Prismatic Bolt!" aura = Bolt available.
- Arcane Charges come from SPELL_ENERGIZE power type 16 (amount, overEnergize); the tool's charge count is approximate (Barrage resets to 0).
- SimC Sunfury priority: Missiles if CC & Salvo<12 → Bolt at 8 CP (no Soul) → Barrage if (4 charges & (CC & Salvo≥12 | Salvo=25)) | Soul | (Salvo>8 & Touch ready) → Bolt → Orb (charges<3) → Blast. Surge (90s) with Touch of the Magi (45s); never hold Touch.
- Build gate (`detect_builds`), **per pull**: spec from that pull's COMBATANT_INFO (else the last seen); Sunfury from a Spellfire Sphere or Arcane Soul buff; S2 4-piece from a Cumulative Power buff. A pull that cast Missiles without gaining Cumulative Power has no 4-piece. Only when a pull shows no signal do other pulls with the same spec decide. Spec checks run only for Arcane (62) + Sunfury + 4-piece; everything else gets generic stats. Talent node IDs aren't decoded.
- Procs (CC, Bolt!, Soul) gained <0.4s before a cast are treated as not yet available (reaction allowance).

## Log format notes
- Line = `M/D/YYYY HH:MM:SS.mmm-TZ  EVENT,fields...` (two spaces). COMBATANT_INFO (logged for every player at each ENCOUNTER_START / CHALLENGE_MODE_START) is parsed only for its spec ID, the last scalar field before the talent brackets (62 = Arcane). Player is found via unit flags `0x511` (mine).
- Advanced params follow the spell prefix on CAST_SUCCESS; aura DOSE events end with the new stack count.

## Progress log
See memory for the user's coaching history; in-repo history is in git.

## Workflow
- Public repo `mike-levenick/whendoilust`. **Conventional Commits**, enforced by the `commit-msg` hook (`./scripts/install-hooks.sh` once after cloning).
- Branch off `main` and open a PR. `main` is protected: PR required, "Build & Test" CI required, linear history, squash-only, no force-push or deletion, and the branch is deleted on merge.
- Label every PR `feature` / `bug` / `chore` / `tech-debt` / `documentation`; `.github/release.yml` builds release notes from them.
- **`/prep`** takes a branch from written to merged: real-log verification gate, draft PR, `/review-panel`, ready-flip, CI watch, then merge after asking. See `.claude/skills/prep/SKILL.md`.
- No review bot. Review is `/review-panel`. Never dismiss a finding without citing the line, test or source that settles it, and never accept one just to seem agreeable.
- Tests use synthetic logs only (`tests/`); real logs are hundreds of MB and must never be committed. New regression tests must be teeth-checked: revert the fix, watch the test fail, restore.
- The analyzer stays **stdlib-only Python**, because it will ship inside a shared Claude Code skill that runs on other people's machines.
- Issues are tracked in milestones `v1` (the shareable coaching skill + HTML report) and `next`.
