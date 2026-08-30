# Fraud Detection

**Rule: technically valid is not the same as trustworthy, and blocking too much costs more than
the fraud.** Fraud detection is a risk decision layer, not an `if` statement.

Part of the payment set: [state & idempotency](payment-state-and-idempotency.md) ·
[provider & webhooks](payment-provider-and-webhooks.md) ·
[order consistency](payment-order-consistency.md) · [ledger](payment-ledger.md) ·
[refunds](payment-refunds.md) · [reconciliation](payment-reconciliation.md) ·
[settlement](payment-settlement.md) · **fraud detection** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Where the decision belongs](#where-the-decision-belongs)
- [Rules, then scoring](#rules-then-scoring)
- [Velocity checks](#velocity-checks)
- [The false-positive economics](#the-false-positive-economics)
- [The grey zone: async review](#the-grey-zone-async-review)
- [Why the provider's tool is not enough](#why-the-providers-tool-is-not-enough)
- [Auditability](#auditability)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
if amount > 10_000_000:
    block()  # a hard-coded rule, and the only one
charge(amount)  # otherwise straight through to money movement
```

An account created five minutes ago, a brand-new card, several high-value orders in seconds from
an IP never seen before. Every field validates. The card is real, in date, correctly
authenticated. The transaction is *valid* and should not proceed.

A single threshold is also trivially learned: attackers discover the boundary and sit just under
it. A rule you cannot change without a deploy is a rule the attacker outlasts.

## Why development hides it

There is no adversary in development. Test data is well-formed and benign, so the only path
exercised is the legitimate one. Fraud patterns are *behavioural* — they exist in the relationship
between many transactions over time — and a dev database has no history to compare against.

The false-positive side is even more invisible: blocking good customers produces no error and no
log line. It shows up as a conversion-rate dip that nobody attributes to the fraud rules.

## Where the decision belongs

A **risk decision layer** sits between checkout and money movement, and it runs at more than one
point in the lifecycle:

| Stage                               | Question                                            |
|-------------------------------------|-----------------------------------------------------|
| **Pre-payment**                     | Should we even attempt this charge?                 |
| **Post-authorization, pre-capture** | Funds are held. Should we actually take them?       |
| **Post-payment**                    | Flag for review, watchlist, or future scoring input |

The pre-capture hook is the valuable one and the most often missed: authorization is reversible
at little cost, capture is not. A system that only decides pre-payment has one chance to be
right, using the least information it will ever have.

The shift in question is from *does this payment have funds?* to **does this payment deserve to
proceed?**

## Rules, then scoring

**Rule-based** is where everyone starts, and it has real advantages: simple, explainable, and
auditable — you can tell a customer, a regulator, or your own ops team exactly why something was
blocked.

```
amount > 10,000,000 AND account_age < 1 day  ->  flag for review
```

Its weakness is brittleness. Rules are binary and learnable.

**Risk scoring** aggregates many weak signals into one number:

- **Account** — age, transaction history, prior clean behaviour
- **Transaction** — amount, currency, item category
- **Technical** — device fingerprint, IP reputation, proxy/VPN detection
- **Address** — shipping vs billing mismatch
- **History** — prior chargebacks or refund abuse

Three outcomes, not two: **allow** (low), **manual review** (medium), **block** (high). The middle
band is what makes scoring worth the complexity — see [async review](#the-grey-zone-async-review).

Keep rules alongside scoring rather than replacing them. Rules express policy you must be able to
state plainly; scores express suspicion you can tune.

## Velocity checks

The sharpest real-time signal, because it measures behaviour rather than attributes:

- 5 transactions in one minute
- 10 different cards tried on one account
- Many orders from one IP or device in a short window

Card testing and account takeover both look completely normal transaction-by-transaction and
obvious in aggregate. Velocity is what turns a set of individually valid payments into a
detectable pattern — which is exactly the information a per-request `if` cannot see.

## The false-positive economics

This is the part engineers systematically get wrong: **the cost of over-blocking usually exceeds
the cost of the fraud.**

Turn sensitivity up and you block honest customers. They do not file a bug — they buy from a
competitor. A fraud system tuned too tight can cost 1–2% of GMV, which for most businesses dwarfs
the fraud it prevented.

So "block more" is not the safe default, and a fraud rule shipped without measuring its
false-positive rate is unfinished work. Every threshold is a business trade-off between security
and conversion, and it needs an owner who sees both numbers.

## The grey zone: async review

Not every decision must be instant. For medium-risk transactions, run **asynchronous review**:
show the customer a pending state, put the transaction in a manual review queue, and decide
without holding up checkout.

This buys analysis time without adding checkout latency — and it converts a forced binary guess
into a decision made with human judgment.

**The rule that matters: never fulfil before the fraud decision resolves.** Especially for
high-resale-value or instantly-delivered goods — gift cards, top-up codes, digital licences —
where fulfilment is irreversible and immediate.

## Why the provider's tool is not enough

Stripe Radar and equivalents are strong, and they are not sufficient, because **the provider does
not know your product.**

They can tell you a card is risky. They cannot know that the item in this cart is the one with a
hot resale market, that this SKU is the one fraud rings target, or that this customer's behaviour
is abnormal *for your business*. Provider signals are about payment instruments; your rules are
about your domain. You need both.

## Auditability

When a chargeback arrives 30 days later, or ops asks whether a rule is doing anything useful, you
need the decision reconstructable. Persist for every evaluation:

- **Signals** — the raw values collected at decision time, not recomputed later
- **Risk score** — the number and the component weights
- **Rules matched** — which specific rules fired
- **Decision and decider** — allow/review/block, and which human or version of the system chose
- **Timestamps** — per stage

Capturing signals *as they were* is the part that gets skipped, and it is what makes the record
useful: account age and prior-chargeback count both change, so a decision cannot be explained
from today's data. This log is also the training and tuning input for reducing false positives.

## Review checklist

- [ ] Risk decisioning is a distinct layer, not conditionals inline in the payment path
- [ ] Evaluation happens pre-payment **and** pre-capture, not only at checkout
- [ ] Thresholds and rules are configurable without a deploy
- [ ] Scoring combines account, transaction, technical, address, and history signals
- [ ] Velocity checks exist across account, card, IP, and device
- [ ] Three outcomes are supported: allow / manual review / block
- [ ] False-positive rate is measured and owned, not just fraud caught
- [ ] Grey-zone transactions go to async review rather than a forced instant decision
- [ ] Nothing is fulfilled before the fraud decision resolves, especially instant-delivery goods
- [ ] Business-level rules exist alongside the provider's tooling
- [ ] Signals are persisted as captured, with score, rules matched, decision, and decider

---

*Synthesized from TechCraft's Payment System series, part P10
([collection](https://www.patreon.com/collection/2186651)). The risk-layer framing, lifecycle
stages, velocity signals, and false-positive economics are TechCraft's; the review structure and
checklist are this repository's.*
