# Coaching file

`report.py --coaching FILE` adds a coach's read of the session to the HTML report. The file is
written by Claude from `wowlog.py --json` output (the `arcane-coach` skill, `.claude/skills/coach`). It's
optional: the report renders without it. `report.py --strict` rejects coaching that isn't grounded in the
report (see `check_coaching`).

## Format

```json
{
  "headline": "One or two sentences: the overall verdict and the biggest lever.",
  "habits": [
    {
      "title": "Short imperative, e.g. 'Missiles before Blast when Clearcasting is up and Salvo is low'",
      "what": "What you did, with the numbers from the JSON.",
      "instead": "What to do instead.",
      "why": "Why it matters, in terms of the spec's mechanics.",
      "moments": [{"pull": 4, "t": 102.2, "note": "optional one-line tooltip", "player": "optional name"}]
    }
  ],
  "pulls": {"4": "1-2 sentences for pull #4."},
  "caveats": ["Where a flagged 'mistake' may be correct, or the data is thin."]
}
```

- `habits`: **up to the top 3** across the session, ranked by how much they'd gain; fewer only when there
  genuinely aren't three. `moments` are 2-3 example timestamps: `pull` = the pull `number`, `t` = seconds
  from pull start copied from the report (a `timeline` cast, a `spec_checks` time such as a wasted proc or a
  Soul window, a `windows` start, an idle gap). Copy times exactly; `--strict` requires something in that
  pull within 0.05s. That is a sanity check (right pull, real time, right player), not proof the moment
  shows what the habit says, so pick moments that genuinely do.
  `player` (a name prefix) is only for moments about a comparison player; the default is the main player.
  Moments must be in pulls whose `build.supported` is true. Each renders as a chip that jumps there.
- `pulls`: keyed by pull number as a string. One or two sentences each; skip a pull rather than pad.
- Every field is optional; unknown fields are ignored.

## Writing rules

1. **Ground every claim in the JSON.** Counts, timestamps and Salvo values come from the report, never
   estimated. If a number isn't in the data, don't state it.
2. **Spec facts come from current-season sources**, not memory: the SimC APL on the `midnight` branch and
   the Method.gg Arcane guide. Mechanics change every patch.
3. **Be honest about uncertainty.** The analyzer assumes a single target, so on add-heavy fights (most
   M+ bosses, raid add phases) "Barrage, priority says filler" can be correct AoE play. Arcane Charges
   are approximate. When a habit rests on that, say so in `caveats` rather than overstating it.
4. **Rotation, not deaths.** Deaths are obvious to the player; don't coach them unless asked.
5. **Plain language, second person, no jargon the player didn't use.** "You", not "the player".
6. Only coach builds the report supports (`build.supported`, per pull). If no pull is supported, write a
   headline saying spec coaching isn't available and leave `habits` empty; if some aren't, coach the rest
   and name the skipped pulls in `caveats`.
