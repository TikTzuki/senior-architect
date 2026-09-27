# hrm: New Era HRM for Claude Code

Lets Claude Code work with your HRM recruiting data:

- **upload candidate CVs** to a position (one file or a whole folder), with AI reading each CV;
- **fetch a candidate's CV review**: score, strengths, weaknesses, and JD match % per position;
- list open positions and find candidates by name or email.

It uses a personal **API token** you create in HRM. Claude acts as you, with your role, and every
change shows up in HRM's history under your name.

```
You:    upload the CVs in ~/Downloads/qa-batch to the QA Engineer position, source LinkedIn
Claude: 5 files → 4 created, 1 duplicate (already in this position). 2 are missing a phone number.
You:    what did the review say about Tran Thi Binh?
Claude: 7.5/10. Strengths: test automation… Matches QA Engineer at 68%.
```

## Requirements

- Claude Code
- `python3` (3.9+). No packages to install, it uses only the standard library.
- An HRM account with role **TA**, **Hiring manager** or **HR admin**

## 1. Install the skill

**Option A: from the marketplace (recommended, gets updates)**

```
/plugin marketplace add TikTzuki/senior-architect
/plugin install hrm@senior-architect
```

Restart Claude Code after installing.

**Option B: copy it by hand**

```bash
git clone https://github.com/TikTzuki/senior-architect.git
mkdir -p ~/.claude/skills
cp -r senior-architect/plugins/hrm/skills/hrm ~/.claude/skills/hrm
```

For a single project only, copy it to `<project>/.claude/skills/hrm` instead.

## 2. Create an API token

1. Open HRM → **Cài đặt / Settings** → **API token**.
2. Name it (e.g. `Claude Code – laptop`) and pick the access:
    - **Chỉ đọc / Read only**: search candidates and read reviews. Start with this.
    - **Đọc và ghi / Read and write**: also upload CVs and run reviews / matching.
3. Pick an expiry (30–365 days) → **Tạo token / Create token**.
4. Copy the two `export` lines shown. **The token is shown only once.** If you lose it, revoke
   it and create a new one.

## 3. Give the token to Claude Code

Pick one:

**In your shell profile.** Paste the two lines into `~/.zshrc` (or `~/.bashrc`), then open a
new terminal:

```bash
export HRM_BASE_URL=https://<your-hrm-host>   # the address you open HRM at, e.g. http://localhost:8000
export HRM_API_TOKEN=hrm_pat_...
# optional: error messages in Vietnamese
export HRM_LANG=vi
```

**Or only for Claude Code.** Add them to `~/.claude/settings.json`:

```json
{
  "env": {
    "HRM_BASE_URL": "https://<your-hrm-host>",
    "HRM_API_TOKEN": "hrm_pat_..."
  }
}
```

Either way the token sits in a plain-text file in your home folder. Treat it like a password:
don't commit it, don't share it, don't put it in a project's `.claude/settings.json`.

## 4. Check it works

Start Claude Code and ask:

```
check my HRM connection
```

Claude runs `hrm whoami` and should answer with your name and role. You can also run it
yourself:

```bash
python3 ~/.claude/skills/hrm/scripts/hrm.py whoami        # option B path
```

**Optional: stop the permission prompts.** Allow the CLI in `~/.claude/settings.json`:

```json
{ "permissions": { "allow": ["Bash(python3 */skills/hrm/scripts/hrm.py *)"] } }
```

## Things to ask

- "list open positions in HRM"
- "upload every PDF in ~/Downloads/cv to position Backend Engineer, source referral"
- "find Nguyen Van A in HRM and show me their CV review"
- "run the CV review and JD match for candidate 42"
- "which candidates for QA Engineer scored above 7?"

Claude asks before uploading, and before running reviews in bulk. Each review is an AI call.

## Troubleshooting

| Message                                                    | Fix                                                                             |
|------------------------------------------------------------|---------------------------------------------------------------------------------|
| `HRM_BASE_URL and HRM_API_TOKEN must be set`               | Step 3, then open a **new** terminal / restart Claude Code                      |
| `Invalid token`                                            | Revoked or mistyped: create a new token                                         |
| `The token has expired`                                    | Create a new token                                                              |
| `A read-only token cannot change data`                     | Create a **read and write** token for uploads and reviews                       |
| `cannot reach …`                                           | Check `HRM_BASE_URL` (the address you open HRM at, without `/api`) and your VPN |
| `Tokens can only be managed from a Google sign-in session` | Expected: tokens are created and revoked in the web UI only                     |

## Revoking

HRM → Settings → API token → **Thu hồi / Revoke**. It stops working immediately. HR admins can
see and revoke everyone's tokens (tick "Everyone's tokens").

## What's inside

```
plugins/hrm/
├── .claude-plugin/plugin.json
├── README.md                    ← this guide
└── skills/hrm/
    ├── SKILL.md                 ← instructions Claude follows
    ├── scripts/hrm.py           ← the CLI (standard-library Python, prints JSON)
    └── references/api.md        ← endpoints, exit codes, token rules
```
