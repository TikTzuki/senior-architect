# Multi-Region Failover

**Rule: a second region is a disaster plan only if three things were decided before the disaster —
how much data you will lose, how long you will be down, and who may accept writes when the regions
cannot see each other.** Without those answers, a second region adds a new way to corrupt data
on top of the outage it was bought to survive.

Part of the distributed set: [network & latency](network-and-latency.md) ·
[timeouts & retries](timeouts-and-retries.md) ·
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md) ·
[consensus & leader election](consensus-and-leader-election.md) ·
[designing for failure](designing-for-failure.md) · **multi-region failover** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Failure modes](#failure-modes)
- [Decide RPO and RTO per data class](#decide-rpo-and-rto-per-data-class)
- [Active-passive: make promotion a decision, not a reflex](#active-passive-make-promotion-a-decision-not-a-reflex)
- [Active-active: give every record a home](#active-active-give-every-record-a-home)
- [Prove it](#prove-it)
- [Review checklist](#review-checklist)

## The anti-pattern

```
region A (primary)  ──── async replication ────▶  region B (standby)
   app + db                                          app + db
         ▲                                                ▲
         └────── health check: "can I reach A?" ──────────┘
                 no → promote B, flip DNS
```

Every box is reasonable, and the diagram says "survives a regional outage". What it actually
encodes: B promotes itself whenever *B* cannot see A — which includes the case where A is
healthy and only the link between them failed — and B starts serving from a copy that is behind
by whatever the replication lag was at that moment, which nobody recorded.

## Why development hides it

There is one region in development, one in staging, and usually one in the load test. Cross-region
replication lag on a healthy link is sub-second, so the standby always looks current. Failover is
exercised, if at all, in a planned drill: the primary is shut down cleanly, replication drains first,
and DNS is flipped by someone watching. A real regional failure is none of those — it is sudden,
the link usually degrades before it drops, and the primary may still be running.

## Failure modes

**1. Every copy is in one region.** Three replicas across three zones survive a zone; a regional
control-plane, network or power event takes all three, and a quorum of zero does not elect anyone.
Counting failure domains is the same exercise as in
[replica placement](replica-placement-and-quorums.md#placement-count-failure-domains-not-copies),
one level up.

**2. Backups called a DR plan.** A nightly dump shipped to another region bounds data loss at a
day and recovery time at however long a multi-terabyte restore takes — hours — *if* the restored
environment starts at all. The usual surprise is that it does not: network config, secrets, IAM and
DNS were never reproduced in the second region. A backup protects against
[deleting the wrong rows](replication-and-sharding.md#replication-is-not-a-backup); it does not keep
the business running.

**3. Failover onto a lagging copy.** Cross-region replication is asynchronous in practice, and its
lag is not constant: when the link degrades — the usual prelude to a regional failure — lag grows
from milliseconds to minutes. Promote the standby then and the newest writes are missing. In a
wallet that is a top-up the bank debited and the wallet no longer shows. Those writes are not gone,
either: they sit in the old primary, and come back as conflicts when it returns. This is
[failover data loss](replication-and-sharding.md#failover-loses-data-by-design) with a larger window.

**4. Both regions promote.** If each region decides on its own that the other is dead, a severed
link between two healthy regions produces two primaries, both accepting writes from their nearby
users. Two hours later the link heals and there are two histories with tens of thousands of
conflicting rows, which no algorithm can merge correctly. Two sites cannot form a majority; a
decision about which side may write needs a third vote.

**5. Active-active on data with an invariant.** Two regions both accepting writes for the same
account will both approve a withdrawal against the same balance. Resolving it afterwards with
last-write-wins silently drops one write, and decides *which* by wall-clock timestamps that differ
between machines. Counters, sets and append-only logs can merge; balances, stock and unique
claims cannot.

**6. The user lands in the other region.** Routing moves a user between regions — load, a failed
health check, a mobile network change. They changed their password in A, and B rejects the new
one; they paid in A, and B shows the order unpaid. See
[cross-region handoff](session-consistency.md#5-cross-region-handoff).

**7. The switch itself is slow and noisy.** DNS-based failover waits on TTLs, and resolvers and
clients that cache longer than the TTL keep sending traffic to the dead region for minutes. A
health check that cannot tell "region down" from "a blip on the check's own path" fails over on
false alarms — and every failover, including the failback, is another chance for modes 3 and 4.

**8. The standby depends on the region that died.** The standby pulls images from a registry in the
primary region, reads secrets from a vault there, uses a control plane or identity service hosted
there, or reports status through it. It cannot start, or cannot be told to. In the 2017 S3 outage in
`us-east-1`, services whose own servers were healthy went down because they depended on S3 in that
one region — AWS's status page among them.

**9. Consensus stretched across regions.** A single Raft or Patroni cluster spanning regions puts a
100–300 ms round trip into every write and every heartbeat. With timeouts tuned for one datacenter
it elects constantly — see [leader churn](consensus-and-leader-election.md#leader-churn).

## Decide RPO and RTO per data class

**RPO** — how much acknowledged data may be lost. **RTO** — how long the service may be down.
They are business numbers, and they decide the architecture rather than the reverse:

| RPO / RTO | What it takes |
|---|---|
| hours / day | Backups restored in another region — tested, with the environment scripted |
| minutes / under an hour | Async replication to a warm standby, promotion by a human with a runbook |
| seconds / minutes | Async replication to a hot standby, promotion by a quorum that includes a third site |
| zero / minutes | Synchronous or quorum writes across regions — every write pays the inter-region round trip |

RPO = 0 between regions means each write waits on another region: tens to hundreds of
milliseconds, every time. Reserve it for the data that needs it, the same per-class decision as
[CAP](replication-and-sharding.md#cap-concretely).

## Active-passive: make promotion a decision, not a reflex

- **The promoter is not one of the two regions.** Use a consensus store with a member in a third
  location, or a human. "I cannot see the primary" from the standby's side is not evidence that the
  primary is down.
- **Fence the old primary** before or as the standby is promoted: revoke its write role, cut its
  traffic, or make storage reject its writes — see
  [fencing](consensus-and-leader-election.md#fencing). Assume it is still running.
- **Read the replication position at promotion time** and record it. That number *is* the data
  loss, and it is what reconciliation starts from.
- **Plan reconciliation** for the writes stranded in the old primary: they are real customer actions
  and need to be replayed, compensated or refunded, not discarded.
- **Fail back deliberately**, as a second planned failover, after the old primary has been rebuilt
  from the new one — never by letting it rejoin with its divergent history.

## Active-active: give every record a home

The workable form of active-active is **geo-partitioning**: each user, account or tenant has a
home region that alone accepts writes for it; other regions serve reads and forward writes. A
region failure then moves the *homes* it owned — which is active-passive per partition, with the
same promotion rules as above. Data that genuinely merges (likes, view counts, presence) can be
written everywhere with a convergent type; data with an invariant cannot.

## Prove it

Run the failure, not a clean shutdown: cut the network between regions while both are running, kill
the primary region mid-traffic, degrade the link before dropping it. Measure the actual RPO (lag at
promotion) and RTO (first successful write after). A failover plan that has only been executed as a
planned switchover has been tested for the case that does not happen.

## Review checklist

- [ ] RPO and RTO are written down per data class, and the replication mode matches each
- [ ] Replicas span regions where the RTO requires it — zones alone are not a regional plan
- [ ] Regional promotion is decided by a third site or a human, never by either region alone
- [ ] The old primary is fenced on promotion, on the assumption that it is still running
- [ ] Replication lag is monitored, and its value at promotion is recorded as the data loss
- [ ] Writes stranded in the old primary have a reconciliation procedure
- [ ] No record that carries an invariant (balance, stock, uniqueness) is writable in two regions
- [ ] Cross-region routing has an answer for read-after-write and monotonic reads
- [ ] The standby region's dependencies — registry, secrets, identity, DNS, control plane — do not live in the primary region
- [ ] Consensus clusters do not span regions with single-datacenter timeouts
- [ ] Failover and failback have been exercised as unplanned failures, with RPO and RTO measured

---

*Prompted by TechCraft's Replication & Scaling P11 (Active-Passive vs Active-Active) and P20
(Multi-Region Design) ([collection](https://www.patreon.com/collection/2351128)). The posts supply
the single-region and AZ-versus-region argument, RPO/RTO as the business framing, the backup-only
recovery story, the lag-spike failover that showed an empty wallet, the two-region split brain, the
password-change routing example, DNS TTL and false-positive health checks, cross-region election
churn, and the 2017 S3 outage. The promotion rules (third-site arbiter, fencing, recording the lag,
reconciling stranded writes, deliberate failback), geo-partitioned homes, the standby-dependency
failure mode, and the review structure are written from established practice, not from the posts.*
