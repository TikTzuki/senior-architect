# Data Retention & History

**Rule: keeping the data is not the same as keeping the history.** `is_deleted = true` preserves a
row and destroys the record of what happened to it.

Part of the data-modeling set: **retention & history** (this file) ·
[temporal data](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[message delivery](message-delivery-semantics.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Why large systems almost never delete](#why-large-systems-almost-never-delete)
- [Soft delete is a first step, not a solution](#soft-delete-is-a-first-step-not-a-solution)
- [Audit log records actions](#audit-log-records-actions)
- [History tables record state](#history-tables-record-state)
- [Which one you need](#which-one-you-need)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
-- Monday: the cleanup script "tidies up".
DELETE FROM credit_limits WHERE customer_id = :id AND active = false;

-- Tuesday: compliance asks why this customer's limit went 10M → 50M → gone.
-- There is no answer. There is no row. There is no record that a row existed.
```

The gentler-looking version has the same defect:

```sql
UPDATE credit_limits SET is_deleted = true WHERE id = :id;
```

The row survives, and every question anyone will actually ask is still unanswerable: *who* did
this, *when*, *why*, and *what was the value before*. You preserved the object and lost the event.

## Why development hides it

Nothing in development asks a historical question. You verify the current state is right, the test
passes, and the feature ships. The bill arrives months later, from someone who is not an engineer —
an auditor, a lawyer, a support agent, a regulator — asking what the data looked like at a specific
past moment.

By then the information was never captured. This is the defining property of this bug class: **it
is not fixable retroactively.** You cannot backfill history you did not record.

## Why large systems almost never delete

`DELETE` is treated as a destructive operation of last resort, because a row is not just a row:

- **Legal and regulatory** — retention periods often mandate years, and deleting inside one is
  itself the violation.
- **Dispute resolution** — "prove the customer agreed to this limit" requires the state at signing.
- **Debugging** — a bug from three weeks ago is unreproducible if the data it acted on is gone.
- **Financial correctness** — deleted rows break reconciliation, because totals no longer add up
  and nothing explains the difference.

The exception that must still work is a genuine erasure request (GDPR and similar). That is
deliberate, scoped, logged deletion or anonymization of specific personal fields — not a cleanup
script, and it should leave a record that the erasure happened.

## Soft delete is a first step, not a solution

Soft delete exists because hard delete caused disasters, and it does solve exactly one problem:
the row is recoverable.

What it does not do, and what teams assume it does:

- **It records no actor and no time.** `is_deleted = true` says nothing about who or when. Add
  `deleted_at` and `deleted_by` at minimum — a boolean alone is close to useless.
- **It records no prior values.** An `UPDATE` that changed the limit from 10M to 50M leaves no
  trace at all; soft delete only covers deletion.
- **It leaks into every query, forever.** Every `SELECT` needs `WHERE is_deleted = false`, and the
  one place someone forgets is a correctness bug that shows deleted data to users. Partial indexes
  and views help; discipline alone does not.
- **Uniqueness breaks.** A `UNIQUE(email)` constraint now collides with soft-deleted rows. The
  usual fix is a partial unique index (`WHERE deleted_at IS NULL`).

## Audit log records actions

An audit log answers *what happened, who did it, and when*:

| Field            | Purpose                                |
|------------------|----------------------------------------|
| entity type + id | what was touched                       |
| action           | created / updated / deleted / approved |
| actor            | user, service, or job — never nullable |
| timestamp        | when                                   |
| changed fields   | old → new for what moved               |
| reason / context | request id, ticket, approval reference |

Properties that matter: **append-only** (revoke `UPDATE`/`DELETE` at the database role level, as in
[payment-ledger](payment-ledger.md)), written in the same transaction as the change it describes so
it cannot be missing, and carrying an actor that is always populated — "system" is a valid actor,
`NULL` is not.

## History tables record state

Here is the distinction most teams miss, and it is the whole reason both patterns exist:

> Knowing **who changed** the data is a different question from knowing **what the data was** at a
> point in time.

An audit log gives you a stream of actions. To answer *"what did this whole record look like at
10:30?"* you must replay every action from creation forward and hope none were missed. That is
expensive, fragile, and usually wrong.

A **history table** stores the full row on every change:

```sql
CREATE TABLE contracts_history (
  history_id    bigserial PRIMARY KEY,
  contract_id   bigint NOT NULL,
  -- ... every business column, as it was ...
  valid_from    timestamptz NOT NULL,
  valid_to      timestamptz,          -- NULL = current version
  changed_by    text NOT NULL
);
```

Now "the record as of 10:30" is one indexed query, not a replay. The cost is storage and write
amplification; the benefit is that the question is answerable at all.

## Which one you need

| Question you must answer                                             | Pattern                                                                                                                |
|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| "Who suspended this account, and when?"                              | Audit log                                                                                                              |
| "What did the contract say on the day it was signed?"                | History table                                                                                                          |
| "Why did we approve this limit?"                                     | Audit log (reason/context field)                                                                                       |
| "What was every field's value at 10:30 last Tuesday?"                | History table                                                                                                          |
| "Reconstruct how the balance reached this number"                    | Event sourcing / ledger — see [event sourcing & CQRS](event-sourcing-and-cqrs.md), [payment-ledger](payment-ledger.md) |
| "What did we *believe* was true back then, versus what is true now?" | Bitemporal — see [temporal data](temporal-data.md)                                                                     |

They are complementary, not alternatives. Most regulated systems need the audit log for
accountability and the history table for reconstruction, and having only one is the common failure.

Choose based on the questions the business will actually ask — and ask that question *before*
shipping the schema, because this is the one design decision you cannot revisit later.

## Review checklist

- [ ] No `DELETE` on business-meaningful data outside a deliberate, logged retention policy
- [ ] Soft delete carries `deleted_at` and `deleted_by`, not just a boolean
- [ ] Soft-deleted rows are excluded consistently — via view or partial index, not per-query memory
- [ ] Unique constraints account for soft-deleted rows (partial unique index)
- [ ] An audit log exists for state changes that carry accountability
- [ ] Audit rows are append-only, with `UPDATE`/`DELETE` revoked at the database level
- [ ] Audit rows are written in the same transaction as the change they describe
- [ ] Actor is never null; automated changes name the system or job
- [ ] If point-in-time state must be reconstructable, a history table exists — not replay-from-audit
- [ ] Retention periods are explicit per data class, and GDPR-style erasure is possible and logged

---

*Synthesized from TechCraft's Data Modeling Patterns series, parts P1–P4
([collection](https://www.patreon.com/techcraft_official)). The "keeping data is not keeping
history" framing and the action-versus-state distinction between audit logs and history tables are
TechCraft's; the review structure and checklist are this repository's.*
