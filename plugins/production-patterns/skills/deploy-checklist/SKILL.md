---
name: deploy-checklist
description: Audit a repository against a pre-deploy checklist — build and release, secrets, auth, data, observability, dependencies, web security headers, mobile signing and storage, OWASP Web + Mobile Top 10, compliance, incident response — and return a GO / NO-GO verdict with cited evidence for every item. Use before a production deploy, when asked "are we ready to ship" or "is this release safe", or for a release-readiness or security sign-off review.
argument-hint: "[web|mobile|both]"
allowed-tools: Bash, Read, Grep, Glob
---

# Deploy Checklist

A verification pass run **against a repository before a production deploy**. It answers one
question — is there evidence, right now, in this code, that each thing we require is actually
true — and returns GO or NO-GO.

## What this is not

Three skills in this plugin look adjacent and are not interchangeable. Running the wrong one
gives a clean report on the wrong question.

| Skill                                              | Asks                                                              | Timing                 |
|----------------------------------------------------|-------------------------------------------------------------------|------------------------|
| [production-review](../production-review/SKILL.md) | Will this *design* survive concurrency, retries, partial failure? | At design / PR         |
| `/code-review`                                     | Is this code correct?                                             | At PR                  |
| **deploy-checklist** (this one)                    | Is the release *configured and hardened* to ship?                 | Immediately pre-deploy |

`production-review` teaches failure modes and reasons about mechanisms. This skill does not
teach anything — it verifies. That distinction is deliberate: a checklist item is only worth
having when the answer is mechanically checkable against an artifact in the repo.

## Steps

**1. Fix the scope.** Web-only repo: skip section 3 (Mobile-Specific) and 4.3 (OWASP Mobile).
Mobile-only: skip section 2 (Web-Specific). Full-stack: check everything. Determine this from
the repo rather than asking, then state which scope you chose and why in one line.

**2. Read the checklist.**

```bash
cat ${CLAUDE_PLUGIN_ROOT}/skills/deploy-checklist/references/base.md
ls  ${CLAUDE_PLUGIN_ROOT}/skills/deploy-checklist/references/   # any <stack>.md that applies?
```

The base applies to any stack. A stack file (`go.md`, `java.md`, …) sitting beside it *adds*
items — run the base first, then the stack file. Never treat a stack file as a replacement.

**3. Audit each in-scope item against the actual artifact.** One pass per section, in order.
This is the whole job, and [the evidence rule](#the-evidence-rule) below is what makes it worth
anything.

**4. Report.** The output contract is fixed — three parts, in this order.

### 4.1 Summary counts

```text
PASS: <n>   FAIL: <n>   N/A: <n>   (total <n> items checked)
```

### 4.2 FAIL table

Only failures appear here. If there are none, write `No failures.`

| Item              | Section                   | Severity    | Evidence                                     | Fix                |
|-------------------|---------------------------|-------------|----------------------------------------------|--------------------|
| (short item text) | (e.g. Web / Security 4.2) | `[BLOCKER]` | (file:line, config value, or command output) | (the exact change) |

### 4.3 Verdict

```text
VERDICT: GO
```

or

```text
VERDICT: NO-GO — <n> blocker(s) unresolved
```

**One unresolved `[BLOCKER]` is NO-GO**, no matter how many items passed. Severity is defined in
the base checklist's legend: `[BLOCKER]` blocks the deploy, `[SHOULD]` ships only with a tracked
ticket and a named owner, `[NICE]` is for when there is time.

## The evidence rule

This is the rule that makes the audit worth running. Without it the report is a restatement of
the checklist with optimistic boxes ticked.

- **Evidence or it is a FAIL.** PASS requires proof: a file path and line, a concrete config
  value, or the output of a command actually run. "Looks fine", "probably set", "the framework
  handles that by default" are not evidence. When unsure, investigate or mark FAIL — never
  round up to PASS.
- **Check the artifact, not the intent.** "No secrets in the repo" means grepping the *history*,
  not `HEAD` — a rotated key still committed in 2023 is still leaked. "TLS only" means reading
  the live config, not a comment asserting it.
- **`N/A` carries a reason, one line.** `PCI DSS — N/A: no card data handled, payments delegated
  to Stripe Checkout (app/checkout/route.ts:14)`. An unexplained `N/A` is how a whole section
  disappears without anyone deciding it should.
- **Report first, fix second.** Produce findings and proposed fixes, then stop. A human approves
  before anything is changed and before anything deploys. The `allowed-tools` above grant no
  write access on purpose — if a fix is wanted, it is a separate, approved step.
- **Never deploy, never run a destructive command.** Read, grep and inspect only, even when an
  item would be easier to verify by mutating something.

## Adding a stack checklist

Copy the template and keep it beside the base — the skill picks it up with no change here:

```bash
cp ${CLAUDE_PLUGIN_ROOT}/skills/deploy-checklist/references/_template.md \
   ${CLAUDE_PLUGIN_ROOT}/skills/deploy-checklist/references/<stack>.md
```

Only items unique to that stack belong in it. Restating a base item in a stack file means the
two drift apart and reviews start checking whichever copy they happened to load.

## Sign-off

The audit does not replace the human **Sign-off** block at the end of the base checklist. A
person records the final GO / NO-GO and owns it, even when an agent produced every finding
underneath. An agent verdict is an input to that decision, not the decision.

---

*Checklist content — all 114 items, the severity scheme, the PASS/FAIL/N-A contract, and the
verify-not-assume rule — comes
from [PinoyFreeCoder/deployment-checklist](https://github.com/PinoyFreeCoder/deployment-checklist),
whose README grants copy, fork and adapt. Restructured here as a plugin skill: the upstream
`agent.md` prompt told an agent to fetch the checklist over HTTP, which is replaced by the
`${CLAUDE_PLUGIN_ROOT}` copy in `references/` so the skill works offline and for marketplace
installs. The positioning against `production-review` and `/code-review` is local.*
