---
name: prep
description: Prep the current branch for review — verify against a real combat log first, open as draft, run the adversarial review panel, flip to ready, then watch CI and merge once it's green. Invoke as /prep.
argument-hint: (none — operates on the current branch)
---

# Prep skill (whendoilust)

Drives a branch from "code is written" to "merged". Adapted from lutebox's
`/prep`; same lifecycle, this repo's checks.

**Review itself is delegated to `/review-panel`** (user-level skill). This skill
owns the lifecycle around it: the verification gate, the draft PR, the ready
flip, CI, and the merge. If the two disagree about how review works,
`/review-panel` wins.

Conventions (true as of 2026-10-10):
- Repo: `mike-levenick/whendoilust`, public, solo.
- `main` is protected: PR-only, required "Build & Test" check, linear history,
  squash-only, branch auto-deleted on merge. **Never push to `main`.**
- CI (`.github/workflows/ci.yml`) runs on **every** PR, draft or not —
  GitHub-hosted runners are free for public repos. It only sees synthetic
  fixtures, never a real log.

## Step 1 — Survey the branch

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch origin main >/dev/null 2>&1
git log --oneline origin/main..HEAD
# Three dots: compare against the fork point, not main's current tip.
git diff --stat origin/main...HEAD
```

- If `BRANCH == main`, or there are no commits ahead of main, stop.
- Check for an existing PR: `gh pr view --json number,isDraft,title,url`.
  - **Exists, draft** — capture `PR_NUM` / `PR_URL`, skip step 3, resume at step 4.
  - **Exists, ready** — stop and ask.
  - **None** — continue.

## Step 2 — Real-log verification gate

CI can't tell whether a change gives *right answers on a real night*. Propose a
focused checklist keyed on what the diff touches:

- **Parser or spec checks (`analyze`, `sunfury_choice`, `check_*`)** — run the
  text report on main and on the branch over the same real log and diff them:
  ```bash
  LOG=$(python3 -c "import wowlog; print(wowlog.newest_log())")
  D=$(mktemp -d); git show origin/main:wowlog.py > "$D/wowlog_main.py"
  python3 "$D/wowlog_main.py" "$LOG" > "$D/main.txt"
  python3 wowlog.py "$LOG" > "$D/branch.txt"
  diff "$D/main.txt" "$D/branch.txt"
  ```
  Every changed line must be explained by the PR. Spot-check one changed
  finding against the raw log lines.
- **A new mechanic or proc rate** — cite the current-season source (SimC APL on
  the `midnight` branch, Method.gg, Wowhead spell page) in the PR body. Memory
  is not a source; mechanics change every patch.
- **JSON shape** — if a field was renamed or removed, bump `schema` and say so.
- **HTML report** — open it in a browser, light and dark, and print-preview it.

Present via `AskUserQuestion`: **"Verified — proceed"** (recommended) or
**"Bypass — I'll verify later"**. Don't skip silently. When bypassed, say in the
PR body which claims rest on synthetic fixtures alone.

## Step 3 — Commit, draft PR metadata, push and open

From here to step 6 run hands-off — print decisions, don't prompt.

- Commit pending work for this branch as one Conventional Commit. Stage
  explicit paths, never `git add -A`.
- Title: one Conventional-Commit-shaped sentence, ≤70 chars. Body:
  `## Summary` bullets, then `## Test plan` mirroring step 2.
- Label from the lead commit's prefix: `feat:` → `feature`, `fix:` → `bug`,
  `chore:` → `chore`, `refactor:` → `tech-debt`, `docs:` → `documentation`.
  Default `chore`.
- Closing issues: `Closes #A. Closes #B.` — the comma form only closes the first.

```bash
git push -u origin "$BRANCH" 2>&1 | tail -3
gh pr create --draft --label <label> --title "<title>" --body "<body>"
PR_NUM=$(gh pr view --json number --jq .number)
```

## Step 4 — Review panel

Invoke **`/review-panel <PR_NUM>`** and follow it. Pass it the context a diff
can't show:

- **Load-bearing claims to attack**, e.g. *"Can a REFRESH paired with a stack
  change be counted as a wasted proc?"* · *"Does a proc that appeared <0.4s
  before a cast count as available?"* · *"Does the JSON still round-trip to the
  same text report?"*
- **Out of scope**: anything already filed as an issue (check the `v1` / `next`
  milestones), and that the analyzer assumes single-target.
- **Deliberate choices, not defects**: stdlib-only Python; Arcane Charges are
  approximate; Orb/Pulse charges aren't visible in the log, so they fold into
  "filler".

Policy: fix pre-existing bugs in code the diff touches, in this PR.

## Step 5 — Verify before claiming done

```bash
python3 -m py_compile wowlog.py && python3 -m unittest discover -s tests -v
```

- **Teeth-check any new regression test**: revert the fix, watch the test fail,
  restore.
- State plainly which fixes rest on inspection alone.

## Step 6 — Report

```
## Tweaks landed
## Pushback
## Deferred
## Unverified
```

## Step 7 — Flip to ready

`gh pr ready $PR_NUM`

## Step 8 — Watch CI

Select the run by head SHA (a fresh push takes a few seconds to register):

```bash
HEAD_SHA=$(git rev-parse HEAD); RUN_ID=""
for _ in $(seq 1 20); do
  RUN_ID=$(gh run list -R mike-levenick/whendoilust --branch "$BRANCH" \
    --json databaseId,headSha --jq "[.[] | select(.headSha == \"$HEAD_SHA\")] | .[0].databaseId // empty")
  [ -n "$RUN_ID" ] && break; sleep 15
done
[ -z "$RUN_ID" ] && echo "CI_RESULT=absent" || { gh run watch "$RUN_ID" -R mike-levenick/whendoilust --exit-status \
  && echo "CI_RESULT=green" || echo "CI_RESULT=failed"; }
```

Run it in the background. `absent` is neither green nor red — never merge on it.
On green, check `gh pr view $PR_NUM --json mergeable,mergeStateStatus`:
`CLEAN`/`UNSTABLE` → step 9; `BEHIND` → `gh pr update-branch $PR_NUM` and redo
step 8; anything else → report and stop. On failure, read
`gh run view "$RUN_ID" --log-failed` for that same run.

## Step 9 — Merge

**Ask before merging.** Show the command and let the user say go:

```bash
gh pr merge $PR_NUM --squash --delete-branch
```

Never merge red, `BLOCKED`, or `DIRTY`. No `--admin`.
