# Consensus & Leader Election

**Rule: agreement requires a majority, not a coordinator.** Any scheme where a single node decides —
or where a minority can still act — will eventually produce two nodes both certain they are in
charge.

Part of the distributed set: [network & latency](network-and-latency.md) ·
[timeouts & retries](timeouts-and-retries.md) ·
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md) ·
**consensus & leader election** (this file) ·
[designing for failure](designing-for-failure.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The question consensus answers](#the-question-consensus-answers)
- [Quorum](#quorum)
- [How leader election actually works](#how-leader-election-actually-works)
- [Split brain](#split-brain)
- [Fencing](#fencing)
- [Leader churn](#leader-churn)
- [The cost, and when to pay it](#the-cost-and-when-to-pay-it)
- [Do not build this](#do-not-build-this)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
# "Whichever instance grabs the flag first is the leader."
if redis.set("leader", instance_id, nx=True, ex=30):
    is_leader = True
    start_scheduled_jobs()          # only the leader should run these

# A background thread refreshes the key every 10s to stay leader.
```

Three failures live in this:

- **The TTL can expire without the leader noticing** — a GC pause or a network stall, and a second
  instance legitimately becomes leader while the first still believes it is. See
  [distributed-locks](distributed-locks.md).
- **A single Redis node is a single point of truth.** Its failover can lose the key entirely, and now
  nobody is leader — or two are.
- **Nothing stops the old leader acting.** It keeps running jobs, writing to the database, and
  believing it has authority.

The result is two leaders, both correct by their own information, both writing.

## Why development hides it

One instance means it is always the leader, and the election logic is never exercised. There is no
second node to compete, no network partition, no pause long enough to expire a lease.

The bug is also **rare and non-deterministic** in production — it needs a partition or a pause at the
wrong moment — so it survives many deploys before appearing, and then presents as data corruption
rather than as an election failure.

## The question consensus answers

Consensus is agreement among nodes on a single value — the next entry in a log, the current leader,
the membership of a cluster — *despite* failures and partitions.

The requirement that makes it hard: the agreement must hold even when nodes cannot distinguish "the
other node is dead" from "I cannot reach the other node." **Those two situations look identical and
demand opposite responses**, and no amount of clever timeout tuning resolves it.

So consensus does not try to detect which case it is. It arranges for **only one side of a partition
to be able to act.**

## Quorum

The mechanism: a decision requires a **majority** of a fixed membership.

```
5 nodes, quorum = 3

partition:  [A B C] | [D E]
            majority ──┘   └── minority: cannot form quorum, must not act
```

Why a majority specifically: two disjoint sets cannot both contain more than half of the same
membership. That single property is what makes split brain impossible — not vigilance, arithmetic.

Consequences worth knowing in review:

- **Use an odd number of nodes.** 3 tolerates 1 failure; 5 tolerates 2. Four nodes tolerate the same
  single failure as three while costing more and being likelier to partition evenly.
- **The minority must refuse service**, not degrade. A minority that keeps serving writes is precisely
  the split-brain being prevented.
- **A quorum write costs a round trip to a majority.** This is the latency floor, and it is why
  consensus systems are not used for high-throughput data paths.

## How leader election actually works

In Raft and its relatives, roughly:

1. Nodes start as followers, expecting heartbeats from a leader.
2. A follower whose **election timeout** expires becomes a candidate for a new **term** (a
   monotonically increasing number) and requests votes.
3. A node grants one vote per term. A candidate winning a **majority** becomes leader.
4. The leader sends heartbeats. A node seeing a **higher term** immediately steps down.
5. Election timeouts are **randomized**, so candidates do not repeatedly split the vote — the same
   jitter logic as [timeouts-and-retries](timeouts-and-retries.md), for the same reason.

The term number is the important part: it makes leadership **ordered**. A stale leader is not detected
by asking whether it is still alive; it is detected because its term is behind.

## Split brain

Two nodes each believing they hold authority, both acting. In practice it happens when:

- Leadership is granted by a **single** node or store, whose failover loses the grant.
- A lease expires without the holder knowing (the pause problem above).
- The minority side of a partition continues serving.
- Someone "helpfully" promotes a replica manually during an incident while the original is alive.

The damage is not downtime but **divergence**: two authorities accept conflicting writes, and after
the partition heals there is no correct merge. You must choose which history to discard.

Note how much worse this is than unavailability. Refusing writes for 30 seconds is recoverable;
accepting two conflicting sets of writes is not. That asymmetry is why consensus systems choose
consistency over availability under partition — see
[replication-and-sharding](replication-and-sharding.md) on CAP.

## Fencing

Consensus decides *who* is leader. Fencing prevents a **former** leader from acting — and it is the
part usually missing.

The leader receives a monotonically increasing token (the term, or an epoch), and **every protected
resource rejects writes carrying a stale token**:

```
leader A, term 7 → paused
leader B, term 8 → writes with term 8 → accepted; resource records 8
A resumes        → writes with term 7 → REJECTED
```

The guarantee lives in the **resource**, which is the only place it can be enforced, because the
resource sees the writes. A stale leader cannot be relied upon to know it is stale.

In practice: a version or epoch column checked on write, a database row lock, or a storage system that
supports conditional writes. Without fencing, leader election reduces to *usually* one leader, which
is fine for a cron job and not fine for money.

## Leader churn

The failure detector behind an election is a timeout, and a timeout cannot tell a dead leader from
a slow one. Set it tighter than the system's bad moments and the cluster deposes healthy leaders:

- **Network jitter or packet loss** delays a few heartbeats past the election timeout.
- **A stop-the-world pause** — GC, a VM steal spike, a container CPU throttle — freezes the leader
  long enough for followers to give up on it.
- **A slow disk.** A Raft leader must persist each entry before it counts its own vote, and so must
  every follower before it acknowledges. On shared or network storage an `fsync` can take hundreds
  of milliseconds, and heartbeats queued behind it miss the deadline.

Each of these produces an election that changes nothing except the leader's identity — and every
one costs a write outage while it happens, dropped client connections, and, where the data
replicates asynchronously behind the leader, possibly the last few acknowledged writes (see
[replica placement & quorums](replica-placement-and-quorums.md)). The cluster looks healthy on
every dashboard that samples state rather than counting transitions.

The timeouts are a budget, and the budget has to cover the worst normal case, not the average:

```
etcd       heartbeat-interval ≈ round-trip time; election-timeout ≈ 10× that
           (defaults 100 ms / 1000 ms); WAL fsync p99 should stay under ~10 ms
Patroni    loop_wait + 2 × retry_timeout ≤ ttl — the defaults 10 + 2×10 = 30 sit exactly on
           the limit, so one slow DCS call during a loop can cost the primary its leader key
```

Two related costs. **Write latency is set by the slowest member the quorum needs**, not the
fastest: with three nodes the leader waits for the quicker of two followers, so one slow node is
hidden and two are not. And **the consensus store's own disk is on the critical path** of every
write — which is why etcd asks for dedicated SSDs, and why a Kubernetes control plane on slow
storage shows up as API-server timeouts long before anyone looks at etcd.

The signal to watch is **the rate of leader changes**, not whether a leader exists. A leader that
exists at every scrape can still change twenty times a day. Alert on churn
(`etcd_server_leader_changes_seen_total`, Patroni's failover history, a broker's controller
changes), and treat a non-zero baseline as a bug to find, not as the system working.

## The cost, and when to pay it

Consensus is expensive: a majority round trip per decision, throughput bounded by the leader, and
operational complexity in membership changes and monitoring.

**Pay it when correctness depends on a single agreed value:** cluster membership, leader identity,
configuration that must not diverge, distributed lock services, and metadata for sharded systems.

**Do not pay it for** the high-volume data path. Consensus systems store *who decides* and *what the
configuration is* — kilobytes of critical state — not your application's records.

And most "we need a leader" requirements are weaker than they look. Ask **efficiency or correctness**:

| Need                                | Answer                                                             |
|-------------------------------------|--------------------------------------------------------------------|
| One instance runs a scheduled job   | A claimed job row, or a lease. Occasional double-run is tolerable. |
| One consumer processes each message | `FOR UPDATE SKIP LOCKED`, or the broker's own assignment           |
| Exactly one writer to a record      | A conditional update or version column — not a leader              |
| Cluster metadata must not diverge   | **Real consensus**                                                 |

## Do not build this

Consensus algorithms are famously easy to get subtly wrong: vote counting during membership change,
log truncation, term handling, and the pause cases are all places where a plausible implementation is
incorrect in ways testing will not reveal.

Use **etcd, ZooKeeper, or Consul**, or a database that already provides it. If you find hand-rolled
election logic in review, the finding is not "tune it" — it is "replace it with a system whose
correctness someone else has proven."

And whichever you use, check the operational questions: what is the quorum size, what happens when
quorum is lost, is loss of quorum alerted on, and has a leader failover actually been tested? An
untested failover is an assumption, exactly like an unrestored backup.

## Review checklist

- [ ] No hand-rolled consensus or election algorithm
- [ ] Leadership is granted by a quorum-based system, not a single node or key
- [ ] Membership is an odd number; quorum size is known
- [ ] The minority side of a partition refuses to act rather than degrading
- [ ] Leadership carries a monotonic term/epoch
- [ ] Protected resources reject writes bearing a stale token (fencing)
- [ ] Leases are not treated as correctness guarantees for money or inventory
- [ ] Each "single leader" requirement is classified efficiency vs correctness
- [ ] Loss of quorum is monitored and alerted on
- [ ] Leader *changes* are counted and alerted on, not just leader presence
- [ ] Election and lease timeouts are derived from measured p99 network, pause and disk-sync times, with margin
- [ ] Leader failover has been tested, not just configured
- [ ] Consensus stores metadata, not the high-volume data path
- [ ] Manual promotion during incidents is gated to prevent creating a second leader

---

*Synthesized from TechCraft's Distributed Systems series, part P16
([collection](https://www.patreon.com/techcraft_official)). The "who decides the next truth" framing
and the incident scenario are TechCraft's, as are the points in Replication & Scaling P10 and
P18 ([collection](https://www.patreon.com/collection/2351128)) that jittery networks cause repeated
elections, that timeouts must be tuned to p99 latency, and that a slow quorum member sets write
latency; the quorum arithmetic, Raft walkthrough, fencing treatment,
the leader-churn budget figures, and checklist are this repository's.*
