# Health Checks, Discovery & Load Balancing

**Rule: adding servers does not scale a system — distributing correctly does.** And a service that
answers `200` on its health check while unable to do work is worse than one that is plainly down.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
[memory & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed locks](distributed-locks.md) ·
**health checks & load balancing** (this file) ·
[deployment & config](deployment-and-config.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Liveness is not readiness](#liveness-is-not-readiness)
- [What a health check should test](#what-a-health-check-should-test)
- [Health checks that cause outages](#health-checks-that-cause-outages)
- [Service discovery](#service-discovery)
- [Load balancing is not free scaling](#load-balancing-is-not-free-scaling)
- [State is what breaks it](#state-is-what-breaks-it)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
@app.get("/health")
def health():
    return {"status": "ok"}        # returns 200 as long as the process exists
```

The process is alive. Its database connection pool is exhausted, its downstream dependency is
timing out, and it cannot serve a single real request — but the load balancer keeps sending traffic,
because it asked the only question this endpoint can answer.

A **zombie service**: alive by every signal being measured, useless in practice. Worse than a crash,
because a crashed pod is replaced and this one silently absorbs and fails a share of all traffic.

The mirror-image mistake:

```yaml
livenessProbe:
  httpGet: { path: /health }     # and /health checks the database
```

Now a brief database blip fails the liveness probe on **every** pod at once, the orchestrator
restarts them all, and a recoverable dependency problem becomes a total outage with a cold start.

## Why development hides it

There is one instance, no load balancer, and no orchestrator. Nothing consumes the health endpoint,
so whatever it returns is untested and unexamined.

Discovery is absent too: `localhost:5432` always resolves, so hardcoded addresses work perfectly
until the first environment where instances are ephemeral.

## Liveness is not readiness

Two different questions with two different remedies, and conflating them is the root of both
anti-patterns above:

| Probe         | Question                                     | If it fails              | Should check                |
|---------------|----------------------------------------------|--------------------------|-----------------------------|
| **Liveness**  | Is this process irrecoverably broken?        | **Restart it**           | Only process-internal state |
| **Readiness** | Can this instance serve traffic *right now*? | **Remove from rotation** | Dependencies it needs       |
| **Startup**   | Has it finished initializing?                | Wait longer              | Warm-up completion          |

The rule that follows: **liveness must not check dependencies.** A restart cannot fix someone else's
database, so making liveness depend on it converts an external problem into a restart loop — and a
restart loop across every replica simultaneously is far worse than degraded service.

Readiness *should* check dependencies, because removing one instance from rotation while others serve
is exactly the right response. Note the asymmetry: if the dependency is down for everyone, readiness
fails everywhere and you serve nothing — so for shared-dependency failures, prefer serving degraded
responses over declaring universal unreadiness.

**Startup probes matter more than they look.** Without one, a slow-booting service (JIT warm-up,
cache priming, migrations) gets killed by liveness before it ever becomes ready, producing a crash
loop that reads as a code bug.

## What a health check should test

Readiness should verify the things this instance needs and can assess cheaply:

- Database connection **acquirable from the pool** — not a full query, and not a new connection
- Required caches or brokers reachable
- Migrations applied and application state initialized
- Not currently shutting down

What it should not do: run expensive queries, fan out to every downstream service, or take longer
than the probe timeout. A health check heavy enough to add load is itself a scaling problem, and it
is polled continuously by every prober.

Two implementation points that matter more than the check's content: **use a separate connection
pool** (or a reserved connection) for health checks, so an exhausted request pool does not fail the
probe and evict a recoverable instance; and **cache the result briefly** (a second or two) so
aggressive probing does not multiply cost.

## Health checks that cause outages

Worth reviewing as its own failure class, because the mechanism is counterintuitive:

- **Shared pool** — the request pool exhausts, the health check cannot get a connection, all
  instances fail readiness, and everything is removed from rotation. See
  [connection-pools-and-latency](connection-pools-and-latency.md).
- **Cascading readiness** — A's readiness depends on B, B's on C. C hiccups and all three tiers
  report unready, turning one dependency's blip into a system-wide outage.
- **Too-aggressive thresholds** — a one-second timeout with one failure allowed will evict healthy
  instances during a GC pause. See [memory & GC](memory-and-gc.md).
- **No graceful shutdown** — the instance stops accepting work before the load balancer notices, so
  in-flight requests fail. Readiness should report false *first*, drain, then exit.

## Service discovery

Hardcoded addresses stop working the moment instances become ephemeral — autoscaling, rescheduling,
rolling deploys all change addresses continuously.

A **service registry** holds the current set of healthy instances; services look up by *name*, not
address. Two topologies:

- **Client-side** — the caller queries the registry and chooses an instance. One less hop, and
  discovery logic must exist in every service and language.
- **Server-side** — the caller hits a stable address (load balancer, Kubernetes Service, mesh
  sidecar) which routes. Clients stay simple; the balancer is on the path.

What to check in review either way: **registration is tied to readiness**, not to process start — an
instance that registers before it can serve gets traffic it will fail. **Deregistration on shutdown
is explicit**, or callers keep routing to a dead instance until a TTL expires. And the registry is a
dependency whose own failure needs an answer — cached last-known-good beats failing closed.

## Load balancing is not free scaling

> Adding servers does not automatically make the system scale. You need load balancing to distribute
> traffic correctly.

Two instances behind a balancer that sends 90% of traffic to one is not double capacity. Distribution
strategy is the thing that converts instances into throughput:

- **Round robin** — even by request count, oblivious to request cost. Fine for uniform work.
- **Least connections** — better under variable request duration; a slow instance receives less.
- **Weighted** — for heterogeneous instance sizes, or shifting traffic during a deploy.
- **Consistent hashing** — routes a given key to the same instance. Necessary for cache locality;
  and note it deliberately creates uneven load when keys are skewed, which is the
  [hot shard](replication-and-sharding.md) problem in another guise.

The balancer must also **stop sending traffic to unhealthy instances** — which is the whole reason
readiness must be honest, and why passive health checking (observing real request failures) catches
things active probes miss.

Two additions that matter under real failure: **outlier detection**, ejecting an instance whose error
rate diverges even if its probe passes; and **load shedding**, where returning `503` quickly beats
queueing work nobody will wait for.

## State is what breaks it

Load balancing works cleanly only if any instance can serve any request. In-process state breaks
that:

- **Sessions in local memory** — a user's next request lands elsewhere and they are logged out. Sticky
  sessions paper over it and reintroduce uneven load and a failure blast radius. Externalize to Redis
  or use signed stateless tokens.
- **Local caches** — each instance warms independently, so hit rates fall as you scale out, and
  invalidation must reach every instance. This is where consistent hashing earns its complexity.
- **Local file uploads and temp files** — a follow-up request on another instance cannot find the
  file. Use object storage; see [large-file-upload](large-file-upload.md).
- **In-process schedulers** — every instance runs the cron job. Needs leader election or an atomic
  claim; see [distributed locks](distributed-locks.md).
- **WebSockets and SSE** — long-lived connections pin a client to an instance, so deploys drop
  connections and broadcasts need a shared pub/sub path.

## Review checklist

- [ ] Liveness and readiness are separate endpoints with different semantics
- [ ] Liveness checks nothing external — a restart could plausibly fix what it reports
- [ ] Readiness checks the dependencies this instance needs, cheaply
- [ ] A startup probe exists for slow-initializing services
- [ ] Health checks use a reserved or separate connection, not the request pool
- [ ] Probe timeouts and failure thresholds tolerate a GC pause
- [ ] Readiness reports false before shutdown, with draining before exit
- [ ] Readiness is not transitively dependent on other services' readiness
- [ ] Instances register only once ready, and deregister explicitly on shutdown
- [ ] Registry failure has a fallback (cached last-known-good)
- [ ] Load balancing strategy suits request-cost variability, not just round robin by default
- [ ] Outlier detection or passive health checking supplements active probes
- [ ] No session, cache, uploaded file, or scheduled job depends on in-process state
- [ ] Sticky sessions, if used, are a known trade rather than an accident

---

*Synthesized from TechCraft's Backend Internals series, parts P17–P19
([collection](https://www.patreon.com/techcraft_official)). The zombie-service framing, the
liveness-versus-readiness distinction, "adding servers does not automatically scale", and the stateful
challenge are TechCraft's; the health-check-caused-outage analysis and checklist are this
repository's.*
