# Memory & Garbage Collection

**Rule: your server's RAM is mostly not your data, and the collector's cost is paid in latency you
did not write.** Allocation rate, not heap size, is what makes GC hurt.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
**memory & GC** (this file) · [caching](caching.md) ·
[distributed locks](distributed-locks.md) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
[deployment & config](deployment-and-config.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Where the RAM actually goes](#where-the-ram-actually-goes)
- [GC is not free](#gc-is-not-free)
- [Your code decides how much GC hurts](#your-code-decides-how-much-gc-hurts)
- [Leaks that are not leaks](#leaks-that-are-not-leaks)
- [Containers make it worse](#containers-make-it-worse)
- [Review checklist](#review-checklist)

## The anti-pattern

```java
// Load everything, transform, return. Correct, and it will OOM.
List<Order> all = orderRepository.findAll();          // 4M rows into the heap
return all.stream()
          .map(this::toDto)                            // 4M more objects
          .collect(Collectors.toList());               // and a third copy
```

```java
// A cache with no bound. Technically a cache; functionally a leak.
private static final Map<String, Report> CACHE = new HashMap<>();
```

Neither is a bug in the ordinary sense. Both work, pass tests, and behave well for months — until
the table grows or the key space widens.

## Why development hides it

Development datasets are small, so `findAll()` returns 200 rows and the unbounded cache holds a
handful of keys. Nothing accumulates because processes are restarted constantly.

GC behaviour is invisible for a different reason: **a young process with a small heap collects
cheaply.** Pauses grow with live-set size and allocation rate, both of which need production traffic
and uptime to appear. A load test that runs for two minutes will not show what an eight-hour heap
looks like.

## Where the RAM actually goes

A server using 4 GB is rarely holding 4 GB of business data:

- **Thread stacks** — 512 KB–1 MB each. 500 threads is 250–500 MB before anything else. This is the
  hidden cost of thread-per-request; see
  [concurrency model](server-concurrency-model.md).
- **Connection buffers** — read and write buffers per socket, plus TLS session state. At 64 KB per
  connection, 10,000 connections is 640 MB.
- **Runtime overhead** — JVM metaspace, code cache, JIT-compiled code, interpreter structures.
- **Per-object overhead** — a JVM object header is 12–16 bytes. A `Long` is 16–24 bytes to hold
  8 bytes of value. A `HashMap` entry costs ~32–48 bytes beyond key and value. Small-object-heavy
  data structures cost several times the size of the data they hold.
- **Allocator fragmentation** — freed memory returns to the allocator, not usually to the OS, so
  RSS stays high after a spike. **Memory not returning to the OS is normal, not a leak** — and
  mistaking one for the other sends teams hunting a bug that does not exist.
- **Off-heap** — direct byte buffers, memory-mapped files, native libraries. Invisible to heap
  monitoring, which is why a container can be OOM-killed while heap graphs look calm.

## GC is not free

The collector must find live objects, and that work competes with your request handling.

**GC pause** is the part that shows in latency. Modern collectors are concurrent for most phases but
still have stop-the-world points. The signature in production is a **p99 that is far worse than p50
for no visible reason** — most requests are fine, and the unlucky ones land inside a pause.

Generational collectors exploit the fact that most objects die young: a cheap young-generation
collection reclaims them, and survivors get promoted. That gives the rule that matters:

> An object that dies immediately is nearly free. An object that survives long enough to be promoted
> and then dies is expensive.

So the pathological pattern is **medium-lived objects** — allocated per request, surviving young
collection, promoted, then discarded. They force old-generation work, which is the expensive kind.

## Your code decides how much GC hurts

You cannot tune your way out of an allocation problem, and this is the reviewable part:

- **Allocation rate is the primary lever.** Halving allocations roughly halves collection frequency.
  Look for per-request object churn in loops, string concatenation in hot paths, boxing of
  primitives, and defensive copies nobody reads.
- **Do not materialize whole result sets.** Stream, page, or process in batches. `findAll()` on a
  growing table is a time bomb with a schedule set by data growth.
- **Watch the promotion path.** Large per-request buffers and collections that outlive a young
  collection are what generate old-gen pressure. Sizing collections correctly up front avoids the
  repeated grow-and-copy that produces garbage *and* promotion.
- **Beware large objects.** In some collectors, objects above a threshold are allocated directly in
  old space (or as "humongous" regions), skipping the cheap path entirely.
- **Bound every cache.** Size or entry limits with eviction, not a bare map. See
  [caching](caching.md).

Tuning has a place — collector choice, heap sizing, region size — but it redistributes cost. Reducing
garbage removes it.

## Leaks that are not leaks

In a managed runtime a true leak is rare; unintentional retention is common:

- **Unbounded caches and maps** — the most frequent cause by a wide margin.
- **Static collections** that only ever grow.
- **Listeners and callbacks never unregistered**, holding their enclosing scope.
- **`ThreadLocal` on pooled threads** — the thread outlives the request, so the value is retained
  indefinitely. Clear it in a `finally`.
- **Substring/slice views** retaining a much larger backing buffer.
- **Closures capturing more than intended** in JavaScript, keeping large objects reachable.

The tell is a **live set that grows monotonically across GC cycles**. Heap sawtooth returning to the
same floor is healthy; a floor that keeps rising is retention. Alert on post-collection live-set
size, not on total heap used — the latter is supposed to fluctuate.

## Containers make it worse

Two specific traps worth checking in review:

- **The runtime must see the container's limits.** An older JVM reads the host's memory and sizes its
  heap for a machine it does not have, then gets OOM-killed. Modern JVMs are container-aware
  (`MaxRAMPercentage`); verify rather than assume.
- **Heap is not the limit.** `container_limit ≥ heap + metaspace + code cache + thread stacks +
  direct buffers + native`. Setting `-Xmx` equal to the container limit guarantees an eventual kill,
  and the kill looks like a crash with no exception — SIGKILL leaves no stack trace, which is why
  these incidents are so often misdiagnosed.

## Review checklist

- [ ] No unbounded query result loaded into memory; large reads are streamed, paged, or batched
- [ ] Every cache and long-lived map has a size bound and eviction policy
- [ ] `ThreadLocal` values are cleared in a `finally` when threads are pooled
- [ ] Listeners, callbacks, and subscriptions are unregistered
- [ ] Collections are pre-sized where the size is known
- [ ] Allocation rate is measured on hot paths, not just heap usage
- [ ] Post-collection live-set size is monitored and alerted on, not total heap
- [ ] p99-vs-p50 latency divergence is investigated as possible GC pause
- [ ] Container memory limit accounts for heap **plus** metaspace, stacks, and off-heap
- [ ] The runtime is container-aware, verified rather than assumed
- [ ] Thread count is understood as a memory cost, not only a scheduling one

---

*Synthesized from TechCraft's Backend Internals series, parts P6–P7
([collection](https://www.patreon.com/techcraft_official)). The "RAM is not just your code" framing,
the allocation-versus-deallocation cost analysis, and "your code directly punishes the GC" are
TechCraft's; the review structure and checklist are this repository's.*
