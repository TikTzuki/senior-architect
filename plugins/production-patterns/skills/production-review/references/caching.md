# Caching & Cache Stampede

**Rule: the worst thing a cache can do is let every request rebuild it at the same moment.** A cache
does not just reduce load — it concentrates it, and expiry is when the bill arrives.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
[memory & GC](memory-and-gc.md) · **caching** (this file) ·
[distributed locks](distributed-locks.md) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
[deployment & config](deployment-and-config.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Cache stampede](#cache-stampede)
- [Fixing it: lock, or serve stale](#fixing-it-lock-or-serve-stale)
- [Jitter the TTL](#jitter-the-ttl)
- [Staleness is the trade you are making](#staleness-is-the-trade-you-are-making)
- [Invalidation](#invalidation)
- [The other two herds](#the-other-two-herds)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
def get_product(pid):
    cached = redis.get(f"product:{pid}")
    if cached:
        return json.loads(cached)
    product = db.query_expensive_product(pid)          # 800 ms
    redis.setex(f"product:{pid}", 300, json.dumps(product))
    return product
```

Textbook cache-aside, and it is a loaded gun. At 5,000 requests per second on a popular product, the
moment that key expires **all 5,000 concurrent requests miss simultaneously**, and all 5,000 run the
800 ms query.

The cache was absorbing 5,000 rps. On expiry it delivers all of it to the database in one instant.

## Why development hides it

One request at a time means a miss is always served by exactly one rebuild. The pattern is correct
and looks complete.

The failure needs **concurrency at the moment of expiry** — many in-flight requests for the same key
inside the rebuild window. That is a product of traffic and rebuild time, and locally both are near
zero. A load test can miss it too, unless it happens to align with a TTL boundary on a hot key.

## Cache stampede

Also called dog-piling or thundering herd. The mechanism:

```
t=0      key expires
t=0..800ms   5,000 requests miss  →  5,000 identical expensive queries
             database CPU saturates
             connection pool exhausted        ← see connection-pools-and-latency
             every other endpoint slows or fails
t=800ms  5,000 responses each write the same key
```

Note where the damage lands: not on the cached endpoint but **everywhere**, because the database and
the connection pool are shared. A stampede on one unimportant key can take down checkout.

Then the cascade closes the loop: slow responses trigger client retries, retries add load, and
requests already timed out are still being computed — capacity spent on answers nobody will read.

The compounding version is worse. If many keys were populated together — a deploy warming the cache,
a batch import, a restart — they share a TTL and **expire together**, so the stampede covers the
whole key space at once.

## Fixing it: lock, or serve stale

**Option 1 — rebuild under a lock.** One request rebuilds; the rest wait or serve stale.

```python
def get_product(pid):
    key = f"product:{pid}"
    cached = redis.get(key)
    if cached:
        return json.loads(cached)

    # Only one winner rebuilds. SET NX is atomic.
    if redis.set(f"lock:{key}", "1", nx=True, ex=10):
        try:
            product = db.query_expensive_product(pid)
            redis.setex(key, 300, json.dumps(product))
            return product
        finally:
            redis.delete(f"lock:{key}")
    else:
        time.sleep(0.05)              # brief wait, then re-read
        cached = redis.get(key)
        if cached:
            return json.loads(cached)
        return db.query_expensive_product(pid)   # last resort
```

The lock **must** have a TTL, or a crashed holder blocks the key forever — the same hazard as
[distributed locks](distributed-locks.md), and the reason `SET NX EX` exists as one atomic
operation.

**Option 2 — stale-while-revalidate, and usually the better answer.** Store a logical expiry
*inside* the value and keep the physical TTL longer:

```python
entry = redis.get(key)          # physical TTL 3600s
if entry:
    if entry["fresh_until"] < now():
        trigger_async_refresh(key)     # one refresh, in the background
    return entry["value"]              # serve immediately, slightly stale
```

No request ever waits for a rebuild. The rebuild happens off the request path, and a failed rebuild
degrades to slightly older data rather than an outage. *Slightly stale beats broken* — for most read
paths that is the correct trade.

## Jitter the TTL

Cheap, and it prevents synchronized expiry:

```python
redis.setex(key, 300 + random.randint(0, 60), value)
```

Without jitter, keys written together expire together. This single line converts a cliff into a
slope, and it should be the default for any bulk-populated cache.

## Staleness is the trade you are making

A cache is a deliberate decision to serve data that may be wrong. Make it explicit rather than
incidental, and set the TTL from **how wrong the data is allowed to be**, not from a habit:

| Data                               | Tolerance                                                                       |
|------------------------------------|---------------------------------------------------------------------------------|
| Product description, category tree | Minutes to hours                                                                |
| Stock level shown while browsing   | Seconds — and re-check authoritatively at checkout                              |
| Account balance, payment status    | Do not cache; read authoritative                                                |
| Permissions and roles              | Short, with explicit invalidation on change — a stale grant is a security issue |

This is the same decision as [consistency-boundaries](consistency-boundaries.md): what must be true
right now, and what may lag.

## Invalidation

- **TTL only** — simplest, and everything is stale for up to the TTL.
- **Write-through / explicit invalidation** — delete or update the key when the source changes.
  Precise, and easy to miss a write path; the one place someone forgets serves wrong data
  indefinitely.
- **Versioned keys** — include a version or updated-at in the key, so a change makes old entries
  unreachable rather than requiring deletion. Avoids the missed-invalidation class entirely, at the
  cost of leaving garbage to expire.

Two failure modes to check for specifically: **invalidate-then-write races** (a concurrent reader can
repopulate the old value between the delete and the write — invalidate *after* the write commits),
and **negative caching**, where an absent row is not cached, so a stream of requests for missing keys
hits the database every time.

## The other two herds

Stampede has relatives worth naming, because the mitigations differ:

- **Cache penetration** — requests for keys that do not exist, so nothing is ever cached and every
  request reaches the database. Common as an attack. Cache the negative result briefly, or use a
  Bloom filter to reject known-absent keys.
- **Cache avalanche** — the cache tier itself fails or restarts empty. *All* traffic hits the
  database at once. Mitigations: request coalescing, a local in-process tier in front of the shared
  one, and a load shedder so the database degrades rather than dies. A cache your system cannot
  survive losing is a dependency, not an optimization — and it should be capacity-planned as one.

## Review checklist

- [ ] Hot-key rebuilds are protected by a lock or stale-while-revalidate, not left to race
- [ ] Any rebuild lock has a TTL and is acquired atomically (`SET NX EX`)
- [ ] TTLs carry jitter, especially for bulk-populated keys
- [ ] TTL length is derived from tolerable staleness, stated per data class
- [ ] Authoritative data (balances, payment status, stock at checkout) is not served from cache
- [ ] Permission and role caches invalidate explicitly on change
- [ ] Invalidation happens after the write commits, not before
- [ ] Missing keys are negative-cached or filtered
- [ ] The system survives total cache loss — degradation is planned, not hypothetical
- [ ] Cache hit rate and rebuild rate are monitored; a hit-rate drop is alerted on
- [ ] Cached entries are bounded in memory (see [memory & GC](memory-and-gc.md))

---

*Synthesized from TechCraft's Backend Internals series, parts P9–P10
([collection](https://www.patreon.com/techcraft_official)). The "worst mistake is letting all
requests rebuild at once" framing, the miss-to-collapse domino, and the cache-lock versus stale-cache
options are TechCraft's; the review structure and checklist are this repository's.*
