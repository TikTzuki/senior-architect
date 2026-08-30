# Payment Ledger

**Rule: payment status says whether a transaction succeeded; only a ledger says where the money
went.** Every movement is two entries that sum to zero, and no entry is ever updated or deleted.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · **ledger** (this file) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Status is a snapshot; a ledger is the movement](#status-is-a-snapshot-a-ledger-is-the-movement)
- [Double-entry](#double-entry)
- [Immutability: no UPDATE, no DELETE](#immutability-no-update-no-delete)
- [One ledger transaction, many entries](#one-ledger-transaction-many-entries)
- [Balances are a cache](#balances-are-a-cache)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
-- The entire financial record of a 100,000đ payment.
UPDATE orders
SET status = 'paid'
WHERE id = :order_id;
UPDATE merchants
SET balance = balance + 100000
WHERE id = :merchant_id;
```

At month end, accounting asks questions this schema cannot answer:

- Where is that 100,000đ *right now*?
- How much did the provider take in fees?
- What exactly does the merchant get paid after platform fees?
- Why is the merchant's balance 3,000đ off from the sum of their orders?

A mutable `balance` column is a running total with no derivation. When it is wrong — and it will
be, after one failed job or one race — there is nothing to recompute it from and no way to find
out when it drifted.

## Why development hides it

`balance = balance + amount` is correct for a single successful payment, and that is the only
case a dev environment produces. Fees, partial refunds, chargebacks, multi-party splits, and
failed mid-sequence writes never appear.

Ledger defects are also silent by nature: nothing throws, no request fails. The system reports
success while the numbers quietly stop adding up, and it surfaces weeks later as a finance
question rather than an engineering alert.

## Status is a snapshot; a ledger is the movement

> Payment status answers "what state is this transaction in?" A ledger answers the more important
> question: "how did the money move?"

Status is a photograph at one instant. A ledger is the full recording: whose account the money
left, which fees it passed through, and where it came to rest. You need both, and they answer
different questions — you cannot derive the second from the first.

## Double-entry

Every financial movement is recorded as at least two entries — a **debit** and a **credit** —
written together. For a 100,000đ payment:

| Account             | Direction | Amount  |
|---------------------|-----------|---------|
| User cash           | debit     | 100,000 |
| Merchant receivable | credit    | 100,000 |

The invariant that makes this self-checking:

```
sum(debits) = sum(credits)        -- equivalently, the entries sum to zero
```

**If that does not balance, reject the transaction.** This is the mechanism that makes it
impossible for software to create or destroy money — a logic bug or a race can produce a wrong
*entry*, but it cannot produce an unbalanced *transaction*. Enforce it as a constraint or a
checked assertion at write time, not as a report you run later.

An entry needs, at minimum: the ledger transaction it belongs to, the account, direction, amount,
currency, and a reference to the business event that caused it.

## Immutability: no UPDATE, no DELETE

Once an entry is written, it is permanent. Never `UPDATE` an amount, never `DELETE` a row.

To correct a mistake, **write an adjustment entry** that offsets it. The error and its correction
both remain visible, which is precisely what an audit requires — a ledger you can edit is not
evidence of anything.

This is the same principle refunds run on: a refund is not the erasure of a payment but a new,
opposite movement. See [refunds](payment-refunds.md).

Practical enforcement: revoke `UPDATE` and `DELETE` on the ledger tables at the database role
level. An application-level convention will not survive a hotfix at 2am.

## One ledger transaction, many entries

A real payment is rarely two entries. Money leaves the user, part goes to the merchant, part to
platform fees:

| Account              | Direction | Amount  |
|----------------------|-----------|---------|
| User cash            | debit     | 100,000 |
| Merchant receivable  | credit    | 97,000  |
| Platform fee revenue | credit    | 3,000   |

All of these belong to **one ledger transaction**, written **atomically**. Either every entry
lands or none does.

A partial write is the worst state the ledger can hold: the user is debited, the merchant is
credited, and the fee entry is missing — so the books do not balance, and no single record shows
which step failed. Group the entries and commit them together.

## Account types, and why the equation holds

Double-entry only self-checks if accounts are typed. The minimum vocabulary:

- **Asset** — value you control (cash at the acquirer, bank account, receivable from a provider)
- **Liability** — value you owe (customer wallet balances, merchant payables, unsettled funds)
- **Equity / revenue** — what the business has earned (platform fees)

A customer's wallet balance is a **liability**, not an asset. That single classification prevents a
whole family of errors: it makes explicit that holding customer funds is an obligation, and that
paying it out reduces both an asset and a liability rather than "spending" your own money. See
[payment-settlement](payment-settlement.md) for why unsettled funds are a receivable rather than
cash.

## Corrections: reversing entries

The operational form of immutability. To correct an entry, write its exact mirror — same amount,
opposite direction — then write the correct entry. Three rows now exist: the error, its reversal,
and the truth.

This is strictly better than a single "adjustment" that nets the difference, because the audit trail
shows *what was wrong* and not merely that something changed. It is also the same mechanism refunds
use — see [refunds](payment-refunds.md) — which is why a refund needs no special case in the ledger.

## Balances are a cache

Summing every entry for an account on each read does not survive scale. So keep a **balance
snapshot** per account for fast lookup.

But keep the direction of authority straight: **ledger entries are the source of truth; the
snapshot is a cache.** It must always be recomputable from the entries, and it must be
periodically reconciled against them — see [reconciliation](payment-reconciliation.md).

The failure mode to watch for in review is code that treats the snapshot as authoritative:
updating a balance without writing entries, or trusting a balance that has drifted. If the
snapshot and the entries disagree, the entries are right.

## Review checklist

- [ ] Money movement is recorded as ledger entries, not as mutations of a `balance` column
- [ ] Every ledger transaction balances: `sum(debits) = sum(credits)`, enforced at write time
- [ ] Unbalanced transactions are rejected, not logged and accepted
- [ ] Ledger entries are never updated or deleted; corrections are adjustment entries
- [ ] `UPDATE`/`DELETE` are revoked on ledger tables at the database level
- [ ] All entries for one event — including fees — are written atomically in one transaction
- [ ] Each entry references the business event that caused it
- [ ] Balance snapshots are treated as a cache, recomputable from entries
- [ ] Snapshots are reconciled against entry sums on a schedule
- [ ] Currency is explicit on every entry; no cross-currency arithmetic without conversion records
- [ ] Accounts are typed (asset / liability / equity); customer balances are modelled as liabilities
- [ ] Corrections use reversing entries, not net adjustments

---

*Synthesized from TechCraft's Payment System series part P7
([collection](https://www.patreon.com/collection/2186651)), and Data Modeling Patterns parts P14–P15
([collection](https://www.patreon.com/techcraft_official)). The status-vs-flow framing, the
double-entry invariant, immutability, balance-as-cache, asset/liability classification, and reversing
entries are TechCraft's; the review structure and checklist are this repository's.*
