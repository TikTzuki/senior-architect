# Session Consistency: Read-Your-Writes, Monotonic Reads & Causal Order

**Rule: "eventually consistent" is not a promise you can hand a user.** Each read path has to
commit to one of three per-client guarantees, and the default — route reads to any replica —
commits to none of them.

Part of the storage set: [storage engines](storage-engines.md) ·
[indexes & query plans](indexes-and-query-plans.md) ·
[replication & sharding](replication-and-sharding.md) · **session consistency** (this file) ·
[connection pools & latency](connection-pools-and-latency.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Three guarantees, three different symptoms](#three-guarantees-three-different-symptoms)
- [Why development hides it](#why-development-hides-it)
- [How it fails in production](#how-it-fails-in-production)
- [The correct pattern](#the-correct-pattern)
- [What each mechanism costs](#what-each-mechanism-costs)
- [Review checklist](#review-checklist)

## The anti-pattern

```
writes  ──> primary
reads   ──> load balancer ──> replica A | replica B | replica C   (round robin)
```

> "Reads go to replicas. The data is eventually consistent, which is fine — nobody cares
> about a few hundred milliseconds."

Someone does care, and it is never "a few hundred milliseconds" for the person who just
clicked Save. Three separate failures hide behind that one sentence, and fixing one does not
fix the others.

The tell in a diff: a read that is routed to a replica pool with **no relationship to the
write that preceded it** — no version token, no session pinning, no primary override. The
code is correct. The database is correct. The user is told a lie.

## Three guarantees, three different symptoms

These are not degrees of the same thing. They are different promises with different scopes,
and satisfying a stronger-sounding one does not imply the others.

| Guarantee                               | The promise                                              | Symptom when missing                     | Scope                             |
|-----------------------------------------|----------------------------------------------------------|------------------------------------------|-----------------------------------|
| **Read-after-write** (read-your-writes) | If *you* wrote it, *you* see it                          | "I saved it and it's not there"          | one client, one write             |
| **Monotonic reads**                     | Once you have seen version N, you never see older than N | "The number went backwards"              | one client, over time and devices |
| **Causal consistency**                  | If A caused B, nobody sees B without A                   | "The reply appeared before the question" | across clients                    |

The trap worth internalising: **read-after-write does not imply monotonic reads.** Pin
every post-write read to the primary and a user still sees time run backwards — they write on
their phone, then open a laptop, and the laptop's read lands on a lagging replica. Same
person, same account, two sessions, two positions in time.

And **monotonic reads do not imply causal consistency.** You can guarantee one user never
sees data regress while still showing a third party the comment before the post it replies to.
Monotonic reads is a promise to *you*; causal consistency is a promise about *relationships
between people*.

## Why development hides it

Every mechanism that produces these bugs is absent locally.

- **Replication lag is zero** because there is one node. The write and the read hit the same
  data file.
- **There is no load balancer**, so there is no chance of consecutive reads landing on
  replicas at different positions.
- **One browser, one tab.** The cross-device case that breaks monotonic reads needs two
  clients, and nobody tests that way.
- **Tests seed data, then read it.** They rarely write and re-read inside the lag window,
  because locally there is no window.
- **Staging has one replica**, so "reads from replica" is tested against a single, usually
  idle, node whose lag is microseconds.

The bug therefore appears for the first time in production, on the read path, with green
dashboards — no 5xx, no slow query, no error in the log. The database is not wrong. Only the
user's view of it is.

## How it fails in production

### 1. The write that didn't happen

A user updates a shipping address on a $2,000 order and gets "Updated successfully". They
refresh to check; the read lands on a lagging replica and shows the old address.

The damage is not the stale render — it is what the human does next. They assume the save
failed and **submit again**. Now two write workflows run for one intent: two address-change
events, two shipping-fee recalculations, two notification emails. A duplicate-submit guard
keyed on a record that has not replicated yet will not catch it, because the check reads a
replica too.

This is a race condition where one of the racing parties is a person. Idempotency logic that
reads from a replica is not idempotency logic — see
[payment state & idempotency](payment-state-and-idempotency.md).

### 2. Time running backwards

A wallet balance shows 100M after a deposit, 50M on the next refresh, then 100M again. Nothing
is wrong with the data; consecutive reads landed on replicas at different positions, because
the load balancer is round-robin and the replicas have unequal lag.

Where this stops being cosmetic: any screen a user **acts on**. A price that appears to drop
because a stale replica served it invites a trade against a price that no longer exists. The
order is then rejected, or filled somewhere the user did not intend. That is a financial and
compliance problem wearing a UI bug's clothing.

### 3. Effect before cause

Alice posts; Bob comments on the post. A third user sees Bob's comment with no post above it.
Both rows committed; both replicated. They simply arrived in different order, because they
travelled independent paths — different replicas with different lag, or different Kafka
partitions consumed by services under different load.

The same shape, with money: a "payment successful" push notification that arrives before the
ledger entry is readable, so the user opens the app and sees the money apparently gone with no
transaction to explain it. See
[message delivery semantics](message-delivery-semantics.md) for why per-partition ordering is
not global ordering.

### 4. Failover rewind

Asynchronous replication means the promoted replica can be missing the last writes the old
primary acknowledged. On failover **every** client jumps backwards at once — not one unlucky
session. Sticky routing does not help; the timeline itself moved.

This is the failure mode that makes "we pinned users to a replica" insufficient as a
consistency story. Quantify it as RPO and check it against what the business thinks was
durable — [replication & sharding](replication-and-sharding.md) covers the durability side.

### 5. Cross-region handoff

Session pinning is almost always scoped to a region. A user who moves between regions — travel,
a VPN, DNS re-resolution, a mobile network handing off — arrives at a replica set with an
entirely different position in the stream, and can see state from before their last several
writes. Any stickiness scheme needs an explicit answer for what happens when the sticky target
is unreachable or in another region, and "route them anywhere" is a monotonic-read violation
by construction.

### 6. Adding replicas makes it worse

The instinct when the primary is loaded is to add read replicas. Each one is another
destination the primary must ship its change stream to. At some point the fan-out is itself
the bottleneck, lag rises across the board, and **the change made to improve performance
widened the inconsistency window.** Scaling reads and holding consistency pull against each
other; a plan that only mentions the first is incomplete.

## The correct pattern

### Step 1 — classify every read path

Not all reads need the same guarantee, and pretending they do is what makes people reach for
strong consistency and then abandon it on cost.

| Read path                                           | Needs              |
|-----------------------------------------------------|--------------------|
| A user reading data they own or just changed        | read-after-write   |
| Any screen a user watches or refreshes repeatedly   | monotonic reads    |
| Anything where one record explains another          | causal consistency |
| Aggregates, feeds of other people's data, analytics | eventual is fine   |

### Step 2 — pick the weakest guarantee that still holds the invariant

Strong consistency everywhere is the naive fix: every write waits on a cross-region quorum,
so the system runs at the speed of its slowest node and stops entirely during a partition.
Global two-phase commit for ordering is the same mistake with more steps. You do not need
everything ordered — only **related** things ordered.

### Step 3 — use a version token, not stickiness

Three mechanisms deliver read-after-write and monotonic reads. Prefer the third.

**Read from primary for owner reads.** Simple and correct. But if every user viewing their own
profile hits the primary, you have given back the entire benefit of replicas. Acceptable as a
narrow rule (account settings, KYC status), not as a strategy.

**Sticky sessions.** Hash `user_id` to a fixed replica at the load balancer. Cheap, and it does
deliver monotonic reads — a single replica's timeline only moves forward. But it is
probabilistic: the guarantee evaporates when that node dies, it creates hot replicas when
heavy users hash together, and rebalancing while preserving stickiness is genuinely hard.

**Version tokens (preferred).** Make the position explicit rather than implied by routing:

1. A successful write returns the primary's position — an LSN, a commit timestamp, an opaque
   token.
2. The application stores it against the session (cookie, session store, response header).
3. Subsequent reads send the token.
4. A proxy or middleware compares it to the candidate replica's applied position, then either
   **waits** briefly for the replica to catch up, **reroutes** to a replica that is caught up,
   or falls back to the primary.

This reads from the primary only when genuinely necessary, survives a replica dying, and
creates no hot spots. It is what cloud "session consistency" tiers implement for you.

### Step 4 — for causal order, track dependencies, not wall clocks

Server clocks disagree, so timestamps cannot establish "happened before". Carry the dependency
instead: the comment records the id and version of the post it replies to. On the read side,
if the effect arrives and its cause is not yet visible, **buffer the effect** until the cause
lands rather than rendering an orphan. Lamport or vector clocks generalise this when the
dependency is not a single obvious parent.

### Step 5 — degrade visibly rather than lying

When the system detects it cannot honour the guarantee — the client's token is ahead of every
reachable replica — show that. A "syncing…" state is a small UX cost. A fake success banner
followed by stale data costs trust, and provokes the resubmit that turns one problem into two.

Related: do not report success for work that has not become visible yet. Model it as a state
the client can poll — [async & long-running operations](api-async-operations.md).

## What each mechanism costs

| Mechanism            | Buys                                            | Costs                                                                       |
|----------------------|-------------------------------------------------|-----------------------------------------------------------------------------|
| Read from primary    | read-after-write                                | replica benefit lost on that path                                           |
| Sticky replica       | monotonic reads, cheaply                        | hot replicas; guarantee lost on node failure or region change               |
| Version token + wait | read-after-write and monotonic reads, precisely | tail latency while waiting; a proxy that must know replica positions        |
| Causal metadata      | effect never precedes cause                     | metadata storage that grows with dependency depth; buffering logic; pruning |

None of these survive failover for free. Every one of them needs an explicit answer to "what
do we do when the pinned or caught-up target is gone".

## Review checklist

- [ ] For each read path touched: which of the three guarantees does it need, and what
  enforces it? "Eventually consistent" is not an answer for an owner read.
- [ ] Does a read that immediately follows a write in the same user action go to the primary,
  carry a version token, or neither?
- [ ] Is there a duplicate-submit or idempotency check that **reads from a replica**? It can
  miss its own predecessor inside the lag window.
- [ ] Is the read routing round-robin over a replica pool for a screen users refresh? That is
  a monotonic-read violation waiting for unequal lag.
- [ ] Does the client hold a version/session token across devices, or is monotonicity assumed
  from routing alone?
- [ ] Where one record explains another (comment→post, notification→ledger, refund→charge),
  is the dependency recorded and is the effect buffered until the cause is visible?
- [ ] Are the notification path and the data path independent? A push that can outrun the read
  model will.
- [ ] What is the stated RPO, and does failover's rewind break a guarantee promised elsewhere
  in the product?
- [ ] If sticky routing is used: what happens when that replica is unreachable, and what stops
  heavy users hashing onto one node?
- [ ] Does the change add replicas? If so, has the effect on fan-out and lag been considered,
  not just the read throughput won?
- [ ] When the guarantee cannot be met, does the UI say so, or does it show stale data under a
  success message?

---

*Topic prompted by the Replication & Scaling series (P1–P7) on
[TechCraft](https://www.patreon.com/c/TechCraft). The series frames the three guarantees and
supplies the failure scenarios — the profile-photo and address cases, the balance that moves
backwards, the comment ordering, the failover rewind and the replica fan-out paradox. The
review structure, the read-path classification and the checklist are written from established
practice, not transcribed from it.*
