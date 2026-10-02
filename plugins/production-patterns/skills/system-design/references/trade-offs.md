# Core Trade-offs

**Rule: name the axis before you pick the point on it.** Most "which is better" arguments
dissolve once you know whether the constraint is latency or throughput, consistency or
availability, one user or a million.

## Contents

- [Performance vs scalability](#performance-vs-scalability)
- [Latency vs throughput](#latency-vs-throughput)
- [CAP: consistency vs availability](#cap-consistency-vs-availability)
- [Consistency patterns](#consistency-patterns)
- [Availability patterns](#availability-patterns)

## Performance vs scalability

A system is **scalable** if adding resources raises its capacity roughly in proportion. That
capacity means more units of work, or bigger ones as the data grows.

| Symptom                                  | Problem type    | Look at                                                    |
|------------------------------------------|-----------------|------------------------------------------------------------|
| Slow for a single user                   | **Performance** | The algorithm, the query plan, round trips                 |
| Fast for one user, slow under heavy load | **Scalability** | Shared resources: locks, pools, a single primary, hot keys |

Two different diagnoses with two different fixes. Adding machines doesn't fix a performance
problem. Profiling one request rarely finds a scalability problem.

## Latency vs throughput

- **Latency:** time to do one action.
- **Throughput:** actions per unit of time.

Aim for **maximal throughput at acceptable latency**. They pull against each other: batching,
queueing and larger buffers raise throughput but add latency per item. Set the latency bound
(usually a p99, not an average) first, then maximise throughput under it.

## CAP: consistency vs availability

In a distributed system you can guarantee at most two of the three:

- **Consistency:** every read gets the most recent write, or an error
- **Availability:** every request gets a non-error response, possibly stale
- **Partition tolerance:** the system keeps working when the network drops messages between nodes

Networks do partition, so P isn't optional. **The real choice is what to do during a partition:**

| Choice | During a partition                                      | Pick it when                                                     |
|--------|---------------------------------------------------------|------------------------------------------------------------------|
| **CP** | Refuse or time out rather than risk a stale answer      | Business needs atomic reads/writes: money, inventory, uniqueness |
| **AP** | Answer from whatever node is reachable; reconcile later | Eventual consistency is acceptable; staying up matters more      |

The choice isn't global. One system can be CP for the balance and AP for the activity feed.
A sharper framing (PACELC, not in the primer): even with no partition you trade **latency vs
consistency** on every replicated write. Deep dives:
[consistency boundaries](../../production-review/references/consistency-boundaries.md) ·
[consensus & leader election](../../production-review/references/consensus-and-leader-election.md).

## Consistency patterns

What a reader sees after a write, when the data has copies:

| Pattern      | After a write, reads…      | Replication  | Examples                                       | Fits                                 |
|--------------|----------------------------|--------------|------------------------------------------------|--------------------------------------|
| **Weak**     | may or may not see it      | best effort  | memcached, VoIP, live video, multiplayer games | Real-time where late data is useless |
| **Eventual** | will see it, usually in ms | asynchronous | DNS, email                                     | Highly available systems             |
| **Strong**   | see it immediately         | synchronous  | file systems, RDBMS                            | Anything needing transactions        |

"Eventual" carries no bound. A user who writes and then reads from a lagging replica sees their own
write vanish. Session guarantees (read-your-writes, monotonic reads) close that gap. See
[session consistency](../../production-review/references/session-consistency.md).

## Availability patterns

Two complementary tools: **fail-over** (a standby takes over) and **replication** (copies of the
data).

### Fail-over

| Mode                              | How                                                                                 | Downtime on failure                                         |
|-----------------------------------|-------------------------------------------------------------------------------------|-------------------------------------------------------------|
| **Active-passive** (master-slave) | Heartbeats between active and standby; standby takes the active's IP when they stop | Hot standby: seconds. Cold standby: boot time               |
| **Active-active** (master-master) | Both serve traffic. Public: DNS knows both IPs. Internal: the app knows both        | None for the survivor, if it has headroom for the full load |

Costs of both: more hardware and complexity, and **writes not yet replicated are lost** when the
active dies. Active-active only helps if each node can carry the whole load alone. Two nodes at
70% each turn into one node at 140%.

Heartbeat fail-over is also how split brain happens: the standby decides the active is dead
when only the link between them failed. Fencing and quorum are covered in
[consensus & leader election](../../production-review/references/consensus-and-leader-election.md).

### Replication

Master-slave and master-master at the database level are covered in
[data-and-caching.md](data-and-caching.md#replication). Availability numbers and the
sequence-vs-parallel formulas are in [estimation.md](estimation.md#availability-in-nines).

*Concepts and the comparison content are compressed from
[donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0). The diagnosis table, PACELC note, active-active headroom and split-brain notes are
this repository's.*
