# Distributed Locks

**Rule: a lock inside one process protects nothing once you run two instances.** And a distributed
lock without a TTL deadlocks your system, while one with a TTL cannot actually guarantee mutual
exclusion — only a fencing token can.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
[memory & GC](memory-and-gc.md) · [caching](caching.md) ·
**distributed locks** (this file) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
[deployment & config](deployment-and-config.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Each process has its own memory](#each-process-has-its-own-memory)
- [The lease: locks must expire](#the-lease-locks-must-expire)
- [Why a TTL breaks mutual exclusion](#why-a-ttl-breaks-mutual-exclusion)
- [Fencing tokens](#fencing-tokens)
- [Split brain](#split-brain)
- [Prefer not to need one](#prefer-not-to-need-one)
- [Review checklist](#review-checklist)

## The anti-pattern

```java
// Works perfectly on one instance. Protects nothing on twenty.
public synchronized void reserveLastUnit(String sku) {
    int stock = inventory.get(sku);
    if (stock > 0) inventory.set(sku, stock - 1);
}
```

`synchronized` is a guarantee within **one JVM**. Behind a load balancer with twenty pods, twenty
threads hold twenty different monitors and all enter simultaneously. The code reads as protected and
is not — the most dangerous combination, and it is the same non-fix listed in
[transaction-isolation](transaction-isolation.md).

The naive distributed version has the opposite problem:

```python
if redis.setnx("lock:order", "1"):     # no TTL
    process_order()
    redis.delete("lock:order")         # never runs if the process dies
```

The pod is OOM-killed mid-processing and that key is now held forever. Every future request blocks
on a lock whose owner no longer exists.

## Why development hides it

You run **one instance**. A process-local lock is genuinely sufficient, and every test passes.

The defect appears at the moment someone scales to two replicas — an infrastructure change, not a
code change, made by someone who is not reading this code. Nothing in the application signals that
its correctness depended on a replica count of one.

## Each process has its own memory

> Each server has its own memory. The worst mistake is thinking a lock in one server is enough.

Any in-process primitive — `synchronized`, `ReentrantLock`, a Python `threading.Lock`, a Node module
flag — is scoped to one process's address space. Mutual exclusion across instances requires a
**shared external authority**: Redis, ZooKeeper, etcd, Consul, or the database itself.

The reviewable signal is a mismatch: an in-process lock guarding a resource that is *not* in-process
(a database row, an external API, a shared file, a queue message).

## The lease: locks must expire

A distributed lock holder can vanish — crash, OOM kill, network partition, pod eviction. It cannot
release what it holds, so **every distributed lock must be a lease with a TTL**:

```python
token = str(uuid.uuid4())
if redis.set("lock:order:42", token, nx=True, ex=30):    # atomic acquire + expiry
    try:
        process_order()
    finally:
        # Release only if we still own it — compare-and-delete, atomically.
        redis.eval("""
          if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
          end
          return 0
        """, 1, "lock:order:42", token)
```

Three details that are each a bug if omitted:

1. **Acquire and expiry must be one atomic operation** (`SET NX EX`). `SETNX` followed by `EXPIRE`
   can crash in between and leave a permanent lock.
2. **The value must be a unique token**, not `"1"`.
3. **Release must be compare-and-delete.** A plain `DEL` can delete *someone else's* lock if yours
   already expired — which is exactly the scenario below.

## Why a TTL breaks mutual exclusion

The uncomfortable consequence, and the part most implementations never confront:

```
t=0    A acquires the lock, TTL 30s
t=5    A pauses — GC pause, CPU starvation, network stall
t=30   lock expires. A does not know.
t=31   B acquires the lock. Legitimately.
t=32   A resumes, still believing it holds the lock
              →  A and B are both in the critical section
```

No implementation bug. The lock service behaved correctly. The flaw is that **a lease can expire
without the holder finding out**, and a process cannot detect its own pause.

So a TTL-based lock gives you *efficiency* — usually only one worker does the job — but **not
correctness**. If two concurrent executions would corrupt data or double-spend money, a Redis lock
alone is not sufficient protection, however carefully written.

This is also why extending the TTL with a background heartbeat helps but does not close the hole: the
heartbeat thread can be paused by the same GC pause that stalled the work.

## Fencing tokens

The actual fix, when correctness matters. The lock service issues a **monotonically increasing
token** with each grant, and the protected resource **rejects any write carrying a stale token**:

```
A acquires  → token 41 → paused
B acquires  → token 42 → writes with token 42   → accepted, resource records 42
A resumes   → writes with token 41              → REJECTED (41 < 42)
```

The guarantee moves from the lock to the resource being protected — which is where it can actually be
enforced, because the resource sees the writes.

In practice, most systems already have a fencing mechanism and should use it instead of a lock:

- **A version column** with a conditional update — see
  [locking](optimistic-vs-pessimistic-locking.md)
- **A monotonic sequence number** checked on write
- **Database row locks**, where the lock and the data share a transaction and cannot diverge

Redis's Redlock is worth naming: it coordinates across independent Redis nodes, and it remains
contested precisely because it does not solve the pause problem above. Do not treat it as a
correctness guarantee for money.

## Split brain

A network partition can leave two nodes each believing they hold the lock or the leader role, and
both acting. For a lock service this means the partition minority may still grant or honour locks.

What matters in review:

- Use a lock service with a **consensus-based** majority requirement (ZooKeeper, etcd) when
  correctness depends on it, not a single Redis instance whose failover can lose the lock state.
- Understand that a lock held on a primary that fails over **may not exist** on the new primary if
  replication was asynchronous — the same data-loss window as
  [replication-and-sharding](replication-and-sharding.md).
- Pair leadership with fencing, so a stale leader's writes are rejected rather than merely
  discouraged.

## Prefer not to need one

Most distributed locks in real systems are avoidable, and removing one is better than perfecting it:

| Instead of a lock                           | Use                                                                               |
|---------------------------------------------|-----------------------------------------------------------------------------------|
| Guarding a counter or balance update        | Atomic conditional `UPDATE ... WHERE`                                             |
| Preventing duplicate creation               | `UNIQUE` constraint                                                               |
| Ensuring one worker processes a job         | `SELECT ... FOR UPDATE SKIP LOCKED` on a queue table                              |
| Ensuring one instance runs a scheduled task | Leader election, or a job row claimed atomically                                  |
| Preventing duplicate side effects           | Idempotency key — see [message-delivery-semantics](message-delivery-semantics.md) |
| Rebuilding a hot cache entry once           | Cache lock, where "usually once" is genuinely enough — see [caching](caching.md)  |

The distinction that decides the design: **is this lock for efficiency or for correctness?** Cache
rebuild and scheduled-job deduplication are efficiency — an occasional double execution wastes work
and harms nothing. Money movement and inventory allocation are correctness, and they need a
constraint or a fencing token, not a lease.

## Review checklist

- [ ] No in-process lock (`synchronized`, mutex) guards a resource shared across instances
- [ ] Correctness does not depend on running exactly one replica
- [ ] Every distributed lock has a TTL, acquired atomically with the lock (`SET NX EX`)
- [ ] Lock values are unique tokens; release is compare-and-delete, not plain delete
- [ ] Lock TTL exceeds worst-case critical-section duration, with headroom
- [ ] It is stated whether each lock is for efficiency or correctness
- [ ] Correctness-critical paths use a constraint, conditional update, or fencing token — not a lease
- [ ] Fencing tokens are validated by the protected resource, not trusted from the client
- [ ] Leader election and lock services use consensus where correctness depends on them
- [ ] Failover behaviour of the lock store is understood (a lock may not survive it)
- [ ] Lock wait time and acquisition failure rate are monitored

---

*Synthesized from TechCraft's Backend Internals series, parts P11–P12
([collection](https://www.patreon.com/techcraft_official)). The "each server has its own memory"
framing, the permanent-lock hazard and lease solution, and the split-brain treatment are TechCraft's;
the fencing-token analysis, efficiency-versus-correctness distinction, and checklist are this
repository's.*
