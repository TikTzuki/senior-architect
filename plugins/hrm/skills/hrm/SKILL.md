---
name: hrm
description: "Work with New Era HRM (x-hrm) recruiting data: upload candidate CVs to a position, and fetch a candidate's AI CV review and JD match results. Use when the user wants to add/import candidates or CVs into HRM, look up a candidate, read or run a CV review (điểm CV, đánh giá CV, chấm CV), check how well a candidate matches a position, or list open positions (vị trí tuyển, requisitions). Triggers on 'upload CV', 'add candidate to HRM', 'tải CV lên', 'CV review', 'review of NAME', 'match score', 'open positions', or mentions of HRM / x-hrm."
metadata:
  {
    "openclaw": {
      "emoji": "🧑‍💼",
      "requires": { "bins": [ "python3" ], "env": [ "HRM_BASE_URL", "HRM_API_TOKEN" ] }
    }
  }
---

# HRM — candidates and CV reviews

All access goes through one bundled CLI, standard-library Python, that prints JSON:

```bash
# Installed as a plugin: ${CLAUDE_PLUGIN_ROOT} is set. Copied by hand into ~/.claude/skills/hrm: it isn't.
HRM="python3 ${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/hrm/scripts/hrm.py"
```

(It is `scripts/hrm.py` inside this skill's own directory. If neither path exists, use that.)

It authenticates with a personal API token (`HRM_API_TOKEN`) against `HRM_BASE_URL`. Everything
it does runs **as the token's owner**, with their current role, and is written to HRM's audit
history under their name.

## First: check the setup

```bash
$HRM whoami
```

- Exit code 2 / "must be set" → the env vars are missing. Walk the user through
  [the install guide](../../README.md#2-create-an-api-token): create a token in HRM →
  Settings → API tokens, then export both variables. Never ask them to paste the token into chat.
- 401 "Invalid token" / "expired" → the token was revoked or expired: they create a new one.
- 403 "read-only token" on an upload or review run → they need a **read + write** token.

## Commands

| Goal                                  | Command                                                               |
|---------------------------------------|-----------------------------------------------------------------------|
| Open positions (to get a position id) | `$HRM positions [--q qa] [--all]`                                     |
| Find a candidate (name or email)      | `$HRM search "nguyen van a" [--position ID] [--all]`                  |
| Upload CVs as new candidates          | `$HRM upload a.pdf b.docx --position ID --source LINKEDIN [--review]` |
| Read a candidate's review + match     | `$HRM review CANDIDATE_ID`                                            |
| …and run what's missing               | `$HRM review CANDIDATE_ID --run --match`                              |
| Check / wait on an AI job             | `$HRM job JOB_ID --wait`                                              |

`search` hides hired / rejected / withdrawn applications unless `--all` is passed. If a person
you expect isn't found, retry with `--all` before saying they don't exist.

Sources: `LINKEDIN INTERNAL_REFERRAL FACEBOOK_GROUP JOB_BOARD DIRECT AGENCY WEBSITE OTHER`.
CV files: `.pdf .docx .md .txt`. Scanned image-only PDFs have no text and are rejected.

## Uploading candidates

1. Resolve the position with `$HRM positions --q <words>`. If several match, **ask** which one.
   Never guess a position id.
2. Confirm with the user before uploading: which files, which position, which source. An upload
   creates real candidate records that TAs will see.
3. Run `upload`. By default it refuses to add a file that's already in that position
   (`"status": "duplicate"`), so re-running over a folder is safe. Only pass
   `--allow-duplicate` if the user explicitly wants a second copy.
4. The command waits for HRM's AI to read each CV. Use the `candidateId` **from the command's
   output**: when the CV's email matches an existing candidate, HRM merges the upload into that
   person (`"merged": true`) and the id changes.
5. Report per file: created / duplicate / failed, and any `missingFields` (usually phone) that a
   TA should fill in by hand. AI-extracted details always need a human check.

`--review` also runs the general CV review for each upload. Pass it only when the user asks for
reviews; each one is an AI call.

## Reading reviews

`$HRM review ID` returns:

- `review`: the **general** CV review. `overall_score` is 0–10, plus `review_text`,
  `strengths`, `weaknesses`. It's `null` when none has been run yet.
- `applications[]`: one per position applied to. `match.percentage` is how much of that JD
  the CV covers, plus `details` and `items`. It's `null` when not run.

Missing results are **not** created unless you pass `--run` (review) or `--match` (match). Ask
before doing that for more than a handful of candidates. `--force` re-runs an existing review; it
costs AI credits, so use it only when asked.

When summarising for the user, give the score, 2–3 strengths and weaknesses, and match % per
position. Say it's an AI assessment. Don't present it as a hiring decision.

## Rules

- CVs and reviews are personal data. Keep them in this conversation. Don't paste them into other
  tools, files, commits or chats unless the user asks for that specific destination.
- The CLI never prints the token. Don't echo `$HRM_API_TOKEN` or write it to files.
- Anything the CLI can't do (moving stages, rejecting, editing candidates) belongs in the HRM web
  UI. Say so; don't improvise raw API calls with a write token.

Endpoint details, exit codes and response shapes: [references/api.md](references/api.md).
