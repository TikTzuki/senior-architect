# Server Concurrency Model

**Rule: a server slow at low CPU is not compute-bound — it is waiting.** How your runtime spends
that wait decides whether adding threads helps or makes it worse.

Part of the backend set: **concurrency model** (this file) · [memory & GC](memory-and-gc.md) ·
[caching](caching.md) · [distributed locks](distributed-locks.md) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
[deployment & config](deployment-and-config.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The paradox of idle CPU](#the-paradox-of-idle-cpu)
- [What a thread actually costs](#what-a-thread-actually-costs)
- [Thread pools are a protection, not an optimization](#thread-pools-are-a-protection-not-an-optimization)
- [Event loops](#event-loops)
- [Blocking the event loop](#blocking-the-event-loop)
- [Holding many connections](#holding-many-connections)
- [Choosing, and sizing](#choosing-and-sizing)
- [Review checklist](#review-checklist)

## The anti-pattern

```java
// "Latency is high, so we need more workers."
server.setMaxThreads(2000);
```

```javascript
// Node: one CPU-heavy call on the request path.
app.get('/report', (req, res) => {
  const rows = db.querySync(...);              // blocking call in an event loop
  res.json(rows.map(heavyTransform));          // and CPU work on top
});
```

Both come from the same misread: latency is high, so the server must need more capacity. It
doesn't. In the first case the server is waiting on I/O and 2,000 threads add context switching to
the wait. In the second, a single request stops *every* other request on that process.

## Why development hides it

One request at a time means no contention, no queueing, and no thread starvation. The event loop is
never blocked because nothing else is waiting on it, and 2,000 threads behave exactly like 20 when
only one is in use.

The signal that misleads everyone is the dashboard: **CPU is low, so the server "isn't busy."** That
reading is wrong, and it is why teams scale the wrong resource for weeks.

## The paradox of idle CPU

Trace a typical request:

```
parse + route          0.4 ms   CPU
database query        12   ms   WAITING
call another service   40   ms   WAITING
serialize response      0.6 ms   CPU
                      ─────────
total                 53   ms   of which ~1 ms is CPU
```

98% of the request is waiting on something else. So the constraint is not compute — it is **how many
waits your process can hold at once**, and what a single wait costs to hold.

That reframes the whole problem. Adding CPU does nothing. Adding threads helps only until the cost
of holding a wait exceeds the benefit of holding one more.

## What a thread actually costs

A thread is an OS-scheduled entity, not a cheap object:

- **Stack memory** — 512 KB to 1 MB reserved by default (JVM default is typically 512 KB–1 MB).
  1,000 threads is roughly a gigabyte of stacks before your application allocates anything.
- **Context switching** — the scheduler saves and restores register state and pollutes CPU caches.
  With more runnable threads than cores, the machine spends a growing share of its time switching
  rather than executing.
- **Scheduler and kernel bookkeeping** — every thread participates in scheduling decisions.

So **thread explosion** has a shape: throughput rises, plateaus, then *falls*, while latency climbs
across every endpoint. The system is fully occupied doing coordination. Past that break point, more
threads strictly hurt — which is the same curve as
[connection pools](connection-pools-and-latency.md), for the same reason.

## Thread pools are a protection, not an optimization

Reusing threads avoids creation cost, but that is the smaller benefit. The real one is **bounding
concurrency**.

An unbounded server accepts work until it dies. A bounded pool with a bounded queue makes the system
**shed or delay load instead of collapsing** — a queue in front of a healthy server beats unlimited
admission into a thrashing one.

Where pools become the problem:

- **Unbounded queue** — the pool stops rejecting and starts accumulating. Requests sit until the
  client has already timed out, so you spend capacity computing answers nobody will read. Bound the
  queue and reject when full.
- **One pool for everything** — a slow downstream call fills the shared pool and takes down
  unrelated endpoints. Separate pools per dependency (a bulkhead) contains it — see
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Blocking inside the pool while holding another resource** — a pooled thread waiting on an HTTP
  call while holding a database connection couples two exhaustion modes. See
  [lock-contention](lock-contention.md).

## Event loops

The alternative: one thread (or a few) plus non-blocking I/O. Instead of parking a thread on a wait,
the runtime registers a callback and moves to other work; the OS reports readiness via `epoll` /
`kqueue`.

Holding 10,000 waiting connections costs 10,000 small state objects, not 10,000 stacks. This is what
makes event-loop runtimes efficient for I/O-heavy, high-connection workloads — Node, nginx, Netty,
async Python.

The trade: **you must never block the loop.**

## Blocking the event loop

The characteristic failure, and it is far more damaging than a blocked thread:

```javascript
// One request; every concurrent request pays.
const hash = crypto.pbkdf2Sync(password, salt, 600000, 64, 'sha512');  // ~800 ms of CPU
```

With thread-per-request, one slow request occupies one thread. With an event loop, **one slow
callback stalls every connection on that process**, because they all share the loop.

What blocks it: synchronous file or database calls, `JSON.parse` on a large payload, crypto,
image processing, regex backtracking, and any tight loop over a large array. The fix is to move CPU
work off the loop — a worker pool, a separate process, or a queue (see
[message-delivery-semantics](message-delivery-semantics.md)).

**Measure event loop lag directly.** It is the single most useful metric for these runtimes, and
rising lag explains latency that CPU and memory graphs will not.

## Holding many connections

Two separate limits get conflated:

- **Concurrent connections held** — mostly idle, waiting. Bounded by memory per connection and file
  descriptors. Event loops excel here; a thread-per-connection design does not.
- **Concurrent requests in flight** — actively consuming CPU or a downstream resource. Bounded by
  the pool and by downstream capacity.

A server can reasonably hold 50,000 idle WebSocket connections while processing 200 requests
concurrently. Sizing for 50,000 *workers* because there are 50,000 *connections* is the mistake.

Practical limits to check: `ulimit -n` (file descriptors) — often the real ceiling and it fails with
a confusing error; ephemeral port exhaustion on the outbound side; and per-connection buffers, which
at 64 KB × 50,000 is 3 GB.

## Choosing, and sizing

| Workload                                           | Model                                             |
|----------------------------------------------------|---------------------------------------------------|
| I/O-bound, many concurrent connections             | Event loop, or virtual threads                    |
| CPU-bound work per request                         | Thread pool sized near core count                 |
| Mixed                                              | Event loop for I/O + separate worker pool for CPU |
| Long-lived connections (WebSocket, SSE, streaming) | Event loop                                        |

**Virtual threads** (Java 21+, Go goroutines) collapse the dichotomy: thread-per-request code with
event-loop-like scaling, because a blocked virtual thread parks cheaply instead of holding an OS
thread. The caveat worth knowing in review: a virtual thread pinned by a `synchronized` block or a
native call still holds its carrier thread, which reintroduces the original limit exactly where you
stopped expecting it.

Sizing rules of thumb: CPU-bound → about core count; I/O-bound thread pools → higher, but derive it
from measured wait time and downstream capacity rather than guessing upward, because the downstream
resource is usually the real constraint.

## Review checklist

- [ ] "Slow but low CPU" is diagnosed as waiting, not as needing more CPU or threads
- [ ] Thread pool size is derived from cores and downstream capacity, not from request volume
- [ ] Pool queues are bounded, and rejection is handled rather than queued indefinitely
- [ ] Separate pools isolate slow dependencies from request traffic
- [ ] No blocking call, CPU-heavy work, or large parse runs on an event loop
- [ ] Event loop lag is monitored for event-loop runtimes
- [ ] Connection capacity and request concurrency are sized as separate limits
- [ ] `ulimit -n` and per-connection buffer memory are accounted for
- [ ] No pooled thread waits on a network call while holding another pooled resource
- [ ] Where virtual threads are used, pinning by `synchronized` or native calls is checked

---

*Synthesized from TechCraft's Backend Internals series, parts P2–P4 and P8
([collection](https://www.patreon.com/techcraft_official)). The idle-CPU paradox, the thread-pool
as-protection framing, and thread-explosion analysis are TechCraft's; the review structure and
checklist are this repository's. P1 (request lifecycle) was not recoverable from the saved page.*
