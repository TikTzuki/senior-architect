# Reconciliation

**Rule: reconciliation is a legitimate source of change, not a silent patch.** Every repair it
makes goes through transition validation, an audit log, and event publishing — exactly like a
user request.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · **reconciliation** (this file) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [Why it exists](#why-it-exists)
- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The four mismatches](#the-four-mismatches)
- [Target high-risk records, not the whole table](#target-high-risk-records-not-the-whole-table)
- [Never patch silently](#never-patch-silently)
- [Ledger and settlement reconciliation](#ledger-and-settlement-reconciliation)
- [What a human must decide](#what-a-human-must-decide)
- [Review checklist](#review-checklist)

## Why it exists

Idempotency, state machines, and the outbox pattern all reduce divergence. None eliminates it.
Workers crash mid-event, providers have incidents, webhooks are lost, and deploys land at
unfortunate moments.

Reconciliation is the safety net that finds what the real-time path missed. It is not a
back-office chore to add later — it is the only mechanism that can answer *where is the money?*
when the primary flow has already failed.

## The anti-pattern

```python
# Nightly job.
for payment in db.query("SELECT * FROM payments"):          # full table scan
    remote = provider.get_payment(payment.provider_id)      # one API call per row
    if remote.status != payment.status:
        db.execute("UPDATE payments SET status = ? WHERE id = ?",
                   remote.status, payment.id)               # silent overwrite
```

Three failures compounding:

- **It scans everything.** At millions of transactions a day this saturates the database and the
  provider's rate limit, and takes longer than the interval it runs on.
- **It overwrites state blindly.** No transition validation, so a stale provider record can drive
  `succeeded → failed`, or a race can resurrect a terminal state.
- **It leaves no trace and tells no one.** Nothing records that the job changed the row, and no
  event is published — so the order service, fulfilment, and notifications never learn that a
  payment became `succeeded`. The database is fixed and the business is still broken.

## Why development hides it

There is nothing to reconcile locally: no lost webhooks, no provider incidents, no crashed
workers. A dev database has a handful of rows, so the full scan is instant and the job looks
efficient. And because reconciliation only matters *after* another failure, a system without it
looks completely healthy right up until the first incident.

## The four mismatches

| Internal            | Provider           | Meaning                                 | Severity                                      |
|---------------------|--------------------|-----------------------------------------|-----------------------------------------------|
| `pending`           | `succeeded`        | Webhook lost or event processing failed | Customer paid, received nothing               |
| `succeeded`         | `failed`           | **Product leakage**                     | **Most severe** — revenue lost, unrecoverable |
| refund `processing` | refund `succeeded` | Refund state drift                      | Wrong financial reporting, confused users     |
| —                   | —                  | Ledger total ≠ payment amount           | Ledger imbalance — an accounting defect       |

**Internal `succeeded` vs provider `failed` is the one to design against hardest.** The order was
unlocked or the goods shipped against a payment that never completed. Unlike the first case, which
is recoverable by finishing what was paid for, this one is money already gone. Detecting it fast
is the difference between one bad order and a systematic leak.

The fourth is different in kind — it is not a state disagreement with the provider but an internal
accounting failure, where the ledger entries do not sum to the payment they represent. See
[ledger](payment-ledger.md).

## Target high-risk records, not the whole table

Reconciliation should be a narrow query, not a sweep. Look only where divergence is plausible:

- Payments **`pending` beyond their normal TTL** — the single highest-value signal
- Payments whose provider call **recorded a timeout** (the UNKNOWN population)
- **Webhook events received but not marked successfully processed**
- Transactions covered by a **settlement report** that just arrived

Each of these is an indexed, bounded query. This also gives the job a natural cadence: near-real-time
for stuck-pending, end-of-day for settlement.

## Never patch silently

The golden rule, and the one most implementations get wrong:

> **Do not patch silently.** Reconciliation is a *source of change*, with the same obligations as
> any other actor in the system.

Every repair must:

1. **Validate the transition.** A reconciliation job is not exempt from the state machine. It may
   not drive `failed → succeeded` without provider evidence, and it may not overwrite a terminal
   state because a stale record disagrees.
2. **Write an audit log entry.** Which system changed what, from what to what, why, on what
   evidence, and when. A state change with no attributable cause is indistinguishable from
   corruption.
3. **Publish the resulting event.** After moving a payment to `succeeded`, emit
   `payment_succeeded` so the order service, fulfilment, and notifications react. Repairing the
   payment row while leaving the order stuck fixes the symptom you measured and none of the
   customer's problem.

Step 3 is the one most often skipped, and it is why "we ran reconciliation" sometimes changes
nothing anyone can see.

## Ledger and settlement reconciliation

State agreement is not enough; the amounts have to agree too.

**Ledger reconciliation** — every succeeded payment has balanced entries, and the totals match the
payment amount. Money debited from a user with nothing credited anywhere is a logic bug that state
comparison will never surface.

**Settlement reconciliation** — the end-of-day check against the provider's settlement report:

```
expected payout  vs  actual payout received (after provider fees)
```

This is what makes period-end financial reporting trustworthy, and it catches fee miscalculations
and provider-side adjustments that no per-transaction check would reveal.

## Cut-off time

The mismatch that is not a mismatch, and it burns hours of investigation.

A provider's settlement report covers a window closing at *their* cut-off, in *their* timezone. A
transaction just after it appears in the next report. Compare your day against their report by
calendar date and you will find a "discrepancy" every single day — transactions you have that they
do not, and vice versa, all of them entirely correct.

Reconcile against **the provider's declared window**, not your local midnight, and record which
batch each transaction settled in. See [payment-settlement](payment-settlement.md).

## What a human must decide

Auto-repair is the goal for unambiguous cases. These are not unambiguous, and a script should
never resolve them:

- **Amount mismatch** — even by one unit. This can indicate tampering, and a job that "corrects"
  it destroys the evidence.
- **Currency mismatch** between request and result.
- **Chargeback conflicts** — disputed, multi-party, and often subject to deadlines.
- **Ledger imbalance** — needs root-cause investigation by finance. A script that "balances" the
  books is hiding a bug, not fixing one.

Route these to a manual review queue with the full evidence attached.

## Review checklist

- [ ] A reconciliation job exists, and is not deferred to "later"
- [ ] It queries high-risk records only — no full table scans, no per-row provider calls at scale
- [ ] Stuck-`pending` age is monitored and alerted on independently
- [ ] All four mismatch classes are detected, including ledger imbalance
- [ ] Internal `succeeded` vs provider `failed` raises a high-severity alert
- [ ] Every repair passes state transition validation; the job is not exempt from the state machine
- [ ] Every repair writes an audit entry naming the actor, evidence, and reason
- [ ] Every repair publishes the corresponding event so downstream systems converge
- [ ] Ledger balance and settlement totals are reconciled, not just states
- [ ] Amount, currency, chargeback, and imbalance cases go to humans, never auto-fixed
- [ ] Comparison windows follow the provider's cut-off and timezone, not local calendar days

---

*Synthesized from TechCraft's Payment System series part P9, reconciled against Data Modeling
Patterns part P16
([collection](https://www.patreon.com/collection/2186651)). The four mismatch cases, high-risk
targeting strategy, the "source of change / never patch silently" rule, and the manual-review
boundary are TechCraft's; the review structure and checklist are this repository's.*
