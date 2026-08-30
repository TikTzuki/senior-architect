# Settlement

**Rule: "payment succeeded" is an intention; settlement is when the money actually arrives.**
Treating authorization as settlement means paying out funds you do not yet have.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
**settlement** (this file) · [fraud detection](payment-fraud-detection.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Transfer succeeded ≠ money moved](#transfer-succeeded--money-moved)
- [Clearing and settlement](#clearing-and-settlement)
- [Netting](#netting)
- [Settlement timing models](#settlement-timing-models)
- [Modelling it](#modelling-it)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
if payment.status == "succeeded":
    merchant.available_balance += payment.amount     # money you do not have yet
    payout_service.transfer(merchant, payment.amount)  # and now you have sent it
```

The card was authorized. The funds are still at the acquirer and will land in your bank account in
one to three business days — minus fees, and minus anything reversed in between.

You have paid the merchant from your own float. If that payment is later charged back, the money is
gone in both directions: the customer's funds returned to them, and yours already sent onward.

## Why development hides it

The provider sandbox returns `succeeded` instantly and there is no settlement at all — no bank, no
T+2, no settlement report, no fees deducted. The concept has no representation in the test
environment, so a model that conflates authorization with receipt passes every test.

It also looks correct for a long time in production. A system that pays out from float works fine
while volume is small and chargebacks are rare, and fails when either grows.

## Transfer succeeded ≠ money moved

Three distinct events that CRUD-shaped systems collapse into one boolean:

| Event             | What it means                                  | Reversible?                 |
|-------------------|------------------------------------------------|-----------------------------|
| **Authorization** | The issuer confirms funds exist and holds them | Yes, trivially              |
| **Capture**       | You request the held funds                     | Yes, via refund             |
| **Settlement**    | Funds actually reach your account              | Only via chargeback/dispute |

An interbank transfer marked "successful" in your system means *the instruction was accepted*, not
that value has changed hands. The receiving bank may still reject it, the intermediary may return
it, or the batch may fail — hours or days later.

So a payment has **two independent state machines**: the authorization lifecycle
([state & idempotency](payment-state-and-idempotency.md)) and the settlement lifecycle. Modelling
only the first is what produces the anti-pattern above.

## Clearing and settlement

Worth separating precisely, because they are frequently used interchangeably and are not the same:

- **Clearing** — reconciling *what is owed*: matching transactions between parties, computing
  positions, agreeing the obligations. Information moves.
- **Settlement** — *discharging* those obligations: value actually transfers between accounts. Money
  moves.

Clearing can complete while settlement is still pending, which is exactly the window in which your
ledger must show an obligation rather than cash. See [payment-ledger](payment-ledger.md): the
receivable and the settled cash are different accounts, and conflating them is how a balance sheet
stops balancing.

## Netting

Settlement rarely moves gross amounts. **Netting** offsets mutual obligations and transfers only the
difference:

```
Gross:  A owes B 100, B owes A 80   →   two transfers, 180 moved
Net:    A owes B 20                 →   one transfer,   20 moved
```

Fewer transfers, less liquidity required, lower fees. It is the norm in card networks, interbank
systems, and marketplace payouts.

The consequence for your data model: **a single bank credit does not correspond to a single
transaction.** One settlement line covers thousands of payments, minus refunds, minus fees, minus
adjustments. Any reconciliation that assumes 1:1 between a payment and a bank movement will fail
immediately — which is why settlement reconciliation compares *expected net* against *actual
received*, not per-transaction amounts. See [payment-reconciliation](payment-reconciliation.md).

## Settlement timing models

| Model                      | Behaviour                                       | Where used                    |
|----------------------------|-------------------------------------------------|-------------------------------|
| **RTGS** (real-time gross) | Each transaction settles immediately, gross     | High-value interbank          |
| **Deferred net**           | Batched, netted, settled at cycle end           | Card networks, ACH, most PSPs |
| **T+n**                    | Settles n business days after trade/transaction | Securities, card payouts      |

Two details that cause real bugs:

- **Cut-off time.** A transaction after the cut-off settles in the *next* cycle. A payment at 23:50
  and one at 00:10 look adjacent and settle a day apart — and if the cut-off is in a different
  timezone than your servers, the boundary is not where anyone assumes.
- **Business days.** T+2 across a weekend or public holiday is four or five calendar days. Systems
  that compute settlement dates by adding calendar days are wrong for a predictable fraction of
  every month.

## Modelling it

- **Separate the two lifecycles.** A payment carries both an authorization state and a settlement
  state; neither is derivable from the other.
- **Hold funds as a receivable until settled.** Merchant `available_balance` should reflect settled
  cash; pending amounts belong in a distinct `pending_balance`. Payouts draw only on available.
- **Model the settlement batch as an entity** with its own identity, expected net, actual received,
  fees, and status — then link payments to it many-to-one.
- **Fees are ledger entries, not arithmetic.** The gross, the fee, and the net each get an entry, or
  the books cannot be explained.
- **Expect settlement reports to arrive late, out of order, and be restated.** Ingest them
  idempotently, keyed on the provider's batch identifier.
- **Never let a payout be triggered by an authorization event.** Payouts are driven by settled
  balances on a schedule, which is also the control that limits chargeback exposure.

## Review checklist

- [ ] Authorization, capture, and settlement are distinct states, not one boolean
- [ ] Merchant/available balance reflects **settled** funds; pending is tracked separately
- [ ] Payouts draw only on settled balance and are never triggered by an authorization event
- [ ] Unsettled amounts are held as a receivable in the ledger, not as cash
- [ ] Settlement batches are modelled as entities with expected vs actual amounts
- [ ] Reconciliation compares expected net against actual received, not payment-to-transfer 1:1
- [ ] Fees and adjustments are recorded as ledger entries
- [ ] Settlement report ingestion is idempotent and keyed on the provider's batch id
- [ ] Cut-off times are explicit, with the provider's timezone stated
- [ ] Settlement dates are computed in business days, honouring weekends and holidays
- [ ] Chargeback exposure against already-paid-out funds is bounded and monitored

---

*Synthesized from TechCraft's Data Modeling Patterns series, part P17
([collection](https://www.patreon.com/techcraft_official)). The intention-versus-truth framing, the
clearing/settlement distinction, netting, and the cut-off-time trap are TechCraft's; the review
structure and checklist are this repository's.*
