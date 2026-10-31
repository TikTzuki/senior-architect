# Event Replay & Projection Rebuild

**Rule: a replay recomputes state; it must never re-execute the past.** Rebuilding a projection is
an online data migration over live traffic — side-effect free, deterministic, built beside the
live copy, verified before cut-over, and sized against the recovery-time objective.

Part of the data-modeling set: [retention & history](data-retention-and-history.md) ·
[temporal data](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
**event replay & projection rebuild** (this file) ·
[event schema evolution](event-schema-evolution.md) · [message delivery](message-delivery-semantics.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Replay is not re-execution](#replay-is-not-re-execution)
- [Apply must be deterministic](#apply-must-be-deterministic)
- [A checkpoint must never skip](#a-checkpoint-must-never-skip)
- [Never rebuild in place](#never-rebuild-in-place)
- [Size the rebuild against the RTO](#size-the-rebuild-against-the-rto)
- [After a restore, distrust what you load](#after-a-restore-distrust-what-you-load)
- [Wrong fact or wrong projection?](#wrong-fact-or-wrong-projection)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
class RenewalProjector:
    def on_subscription_renewed(self, e):
        db.upsert("subscriptions", e.sub_id, status="ACTIVE", renewed_at=datetime.now())
        email.send(e.customer, "Your subscription was renewed")     # side effect
        if fx.rate("EUR", "USD") > 1.1: ...                          # live lookup

# runbook for a projection bug: fix the code, then
#   TRUNCATE subscriptions; reset checkpoint to 0; restart projector
```

Live, every event passes through this handler once and the output looks right. The first time
someone follows the runbook, two years of renewal emails go out in an afternoon, `renewed_at`
becomes the date of the rebuild, the FX branch takes today's rate for last year's events, and the
screen that ops depends on is empty until the replay finishes.

## Why development hides it

A test environment has hundreds of events, written moments before they are read: a replay is
instant, the clock barely moves, the external rate is unchanged, and the outbound email client is
a stub. Nobody rebuilds anything until a production bug forces it — so the rebuild path is the
least-exercised code in the system, run for the first time under incident pressure, against
billions of events and live writes.

## Replay is not re-execution

An event records that something happened. Reading it again is not a request to do it again.

**Separate state transitions from external actions.** Projectors only derive state. Anything that
touches the outside world — email, SMS, webhooks, payment calls, fulfilment requests — is driven
by an explicit intent record (an outbox row with a stable business key) and executed by a separate
worker that is never part of a replay. Replay the state; never replay the outbox.

**A flag is not a guard.** `if replay_mode: return` protects every path someone remembered to
guard, and the next handler written without it fires real effects. Make replay safety structural:

- The replay worker's build does not include the email, payment or webhook clients.
- It runs without production outbound credentials, so a stray call fails authentication.
- Its egress to third-party endpoints is blocked at the network layer.
- A metric of external calls attempted during replay stops the job at the first non-zero value.

**External decisions are facts.** If a fraud check, risk score or FX quote influenced what
happened, store its result in an event (`RiskApproved{decision_id, policy_version}`). A replay
that calls the service again gets today's answer, and can turn a historical approval into a
rejection. Asking what *today's* model would have decided is a simulation, not a replay.

## Apply must be deterministic

`state = fold(apply, initial, events)` holds over years only if `apply` depends on nothing but the
state and the event:

- **No clock reads.** Use the timestamps carried by the event. `now()` makes the result depend on
  when the rebuild ran.
- **No generated IDs.** Random or sequence-generated IDs differ on every run; derive them from the
  event.
- **No lookups.** No network calls, no reads of other services or mutable reference tables.

**Today's policy must not rewrite history.** A fee change from 1% to 1.2% is a new rule from its
effective date, not a correction. A projector that applies the current rule to every event
recomputes last year's fees on the next rebuild. Carry the rule version or effective date in the
event and apply the rule in force for each one. Only a genuine implementation bug — code that
never matched the rule that applied — is fixed by replaying with corrected logic. See
[temporal data](temporal-data.md#valid-time-modelling-when-a-fact-applied) for the same split.

## A checkpoint must never skip

A projector expecting position 4103 that receives 4104 has found a gap, not a quirk. Advancing
the checkpoint to keep it running loses 4103 permanently — if it was a withdrawal, every balance
after it is wrong, and nothing short of a rebuild repairs it. **Halt the partition, or park it and
fetch the missing position**; never advance over a gap. Write the projection change and the
checkpoint in one transaction, the same rule as the [inbox](message-delivery-semantics.md#inbox-do-not-apply-it-twice).

A **poison event** — one the current code cannot read or apply, often an
[old event version](event-schema-evolution.md#failure-modes) the reader no longer understands — needs a policy decided by the
view, not by the deadline. An analytics view can quarantine it and publish a known-incomplete
figure; a balance or ledger view cannot skip it, because every later value depends on it, and the
job must stop for a human.

Infrastructure dashboards do not see any of this: a stuck partition uses no CPU. Alert on the
**age of the oldest unprocessed event per partition**, on the poison-event count, and on drift
between projections and control totals.

## Never rebuild in place

Replaying into the live table races the live projector. A backfill writing a two-year-old document
over one updated a second ago, with no version check, returns products to old prices and stock
levels while the checkpoint reports "complete". And truncating first leaves the readers of that view
with nothing for hours. Instead:

1. **Build a shadow copy** — a new table, index or namespace — while the old one keeps serving.
2. **Fix a high-water mark** at the start: the head position, **per partition** where there is no
   global order. Backfill up to it.
3. **Catch up** on everything written since, and only proceed once the lag is under a stated bound.
4. **Verify** before anyone reads it: row counts and constraints; business control totals against the
   authoritative source; a diff against the old copy, with every difference classified as expected
   (the bug you fixed) or unexpected (investigate); and hand-checked timelines for edge-case
   entities.
5. **Cut over** by switching an alias or flag, and keep the old copy for a rollback window.

Throttle the backfill against the production signals it can hurt — command-path p99, event-store
load, replica lag — not at a fixed rate. A recovery that causes a second incident has not recovered
anything. And choose the smallest scope you can prove correct: if the bug lived three weeks in one
partition, rebuild that partition and those weeks, not five years of everything.

## Size the rebuild against the RTO

"We can always replay" is true and useless without arithmetic:

```
rebuild time ≈ events ÷ measured projector throughput + catch-up + verification
3 000 000 000 ÷ 40 000/s ≈ 21 h  — against an RTO of 2 h
```

If the number exceeds the recovery-time objective, a full replay is not a recovery plan. Restoring
a verified snapshot and replaying only the delta, partitioned and parallel, is. Measure the
throughput in a rehearsal on production-sized data; an untested rebuild is an assumption, like an
unrestored backup.

## After a restore, distrust what you load

A backup restore or a lost append can leave the store *behind* things derived from it:

- **A snapshot ahead of the stream.** If a snapshot's version is higher than the stream head, it was
  built from events that no longer exist. Loading it serves state nothing can justify. **Fail
  closed**: refuse the load and investigate.
- **A gap inside a stream.** A fold over versions 1–90 and 96–140 still produces a plausible
  balance. Check that per-aggregate versions are contiguous on load and after every restore.
- **Snapshot plus tail must equal full replay.** A snapshot serializer that drops or rounds a field
  makes the two disagree. Test the equivalence, and make an unreadable snapshot fall back to a full
  replay automatically.

## Wrong fact or wrong projection?

The reflex in a data incident is "drop the projection and replay". That only helps if the
projection is what is wrong. If a bad command was accepted — a deposit recorded at ten times what
the gateway confirmed — the event *is* the error, and a replay reproduces it exactly, after hours
of work.

So triage first: compare the projection, a fold of the event history, and the external authority
(bank statement, gateway, ledger) for sample entities.

- **History right, projection wrong:** fix the projector and rebuild the affected scope into a
  shadow copy, as above.
- **History wrong:** append a correcting event that references the original and records why and who
  approved it, as in [ledger corrections](payment-ledger.md#corrections-reversing-entries) — then
  fix the validation that let it in.

Before either, freeze the paths that would spread the damage and preserve the evidence: the event
range, checkpoints, and the code version involved.

## Review checklist

- [ ] Projectors only derive state; external effects run from intent records in a separate worker
- [ ] Replay workers cannot reach the outside world: no client libraries, no outbound credentials, egress blocked
- [ ] External calls attempted during replay are counted, and any non-zero value stops the job
- [ ] External decisions (fraud, risk, FX) are stored as events, never re-queried on replay
- [ ] `apply` reads no clock, generates no IDs and calls nothing; timestamps come from events
- [ ] Rules that change over time are applied as in force at each event's time, not as of today
- [ ] Checkpoints never advance over a gap; checkpoint and projection change commit together
- [ ] Each view has a poison-event policy; ledger-type views halt rather than skip
- [ ] Oldest-unprocessed-event age per partition is alerted on
- [ ] Rebuilds go to a shadow copy with a per-partition high-water mark, catch-up and alias cut-over
- [ ] Cut-over requires control totals and a classified diff against the old copy
- [ ] Rebuilds are throttled against production latency and replica lag
- [ ] Full-rebuild time has been measured and compared with the RTO; snapshot-plus-delta exists if it is longer
- [ ] Snapshot loads fail closed when ahead of the stream; stream versions are checked for gaps after restore
- [ ] Data incidents are triaged into wrong-fact versus wrong-projection before any replay

---

*Prompted by TechCraft's Event Sourcing & CQRS series, parts P3, P4, P7, P8, P12, P21, P22, P24 and
P28 ([collection](https://www.patreon.com/techcraft_official)). The posts supply the replay-fired
renewal emails and payment retry, pure apply and external decisions as facts, the infrastructure
guardrails against a replay flag, the policy-change versus bug-fix distinction, gap handling at the
checkpoint, the in-place backfill that restored old prices, watermark, shadow and reconciliation
steps, throttling, the RTO arithmetic, snapshot fail-closed and equivalence tests, restore gaps,
and the wrong-fact incident and triage. The structure, the worked numbers, and the review checklist
are this repository's.*
