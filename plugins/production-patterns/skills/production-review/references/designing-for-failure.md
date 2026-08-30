# Designing for Failure

**Rule: failure is the default state, not the exception.** Large systems are not designed for the
good day — they are designed so the bad day degrades instead of collapsing.

Part of the distributed set: [network & latency](network-and-latency.md) ·
[timeouts & retries](timeouts-and-retries.md) ·
[circuit breakers & bulkheads](circuit-breakers-and-bulkheads.md) ·
[consensus & leader election](consensus-and-leader-election.md) ·
**designing for failure** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Two philosophies](#two-philosophies)
- [Enumerate the failures](#enumerate-the-failures)
- [Degrade, do not collapse](#degrade-do-not-collapse)
- [Blast radius](#blast-radius)
- [Know that it is happening](#know-that-it-is-happening)
- [Practise failing](#practise-failing)
- [The uncomfortable questions](#the-uncomfortable-questions)
- [Review checklist](#review-checklist)

## The anti-pattern

Not a code snippet — a design review that never happened.

```python
def order_page(order_id):
    order     = order_service.get(order_id)
    invoice   = billing_service.get_invoice(order_id)
    tracking  = shipping_service.get_tracking(order_id)
    loyalty   = loyalty_service.get_points(order.user_id)
    reviews   = review_service.get_prompts(order_id)
    return render(order, invoice, tracking, loyalty, reviews)
```

Five dependencies, and the page requires all five. If any one is unavailable — including the loyalty
points display and the review prompt — the customer cannot see their order.

The availability arithmetic is unforgiving: five dependencies at 99.9% give a page at **99.5%**, which
is roughly 3.6 hours of downtime a month, caused mostly by things nobody would miss.

Nobody decided this. It is what you get when each dependency is added without asking what happens when
it is gone.

## Why development hides it

In development, dependencies are either wired up or absent — never *sick*. Nothing partially fails, so
the question "what does this page look like without loyalty points?" is never forced.

More fundamentally: **the failure modes are not in the code, so they cannot be reviewed by reading
it.** A missing timeout is invisible. An unhandled dependency outage is invisible. The absence of a
fallback looks exactly like a decision that no fallback is needed.

## Two philosophies

> In a distributed system, everything will definitely break.

Two ways to respond, and they produce different architectures:

- **Avoid failure** — treat it as an enemy to eliminate with more testing, more reliable
  infrastructure, more careful code. This works until it doesn't, and then there is no plan.
- **Accept failure** — treat it as a normal operating condition and design for what happens next.

The second is not fatalism; it is the only one that survives. You cannot prevent a dependency's
incident, a network partition, a bad deploy, or a disk filling up. You *can* decide in advance what
your system does when they happen.

The shift in the design question is from *"how do we make this not fail?"* to **"when this fails, what
does the user experience?"** — a question with a concrete answer that can be reviewed.

## Enumerate the failures

For each dependency, the answers should exist before the code ships:

| Failure             | Question                                                                                                                          |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| **Slow**            | What is the timeout, and what happens at it? (Usually the most damaging case — see [network-and-latency](network-and-latency.md)) |
| **Down**            | Fail the request, or degrade?                                                                                                     |
| **Wrong**           | Bad or malformed data — is it validated, or does it corrupt state?                                                                |
| **Partial**         | It succeeded and the response was lost. Is retry safe? See [timeouts-and-retries](timeouts-and-retries.md)                        |
| **Overloaded**      | It returns `429`/`503`. Do you back off, or amplify?                                                                              |
| **Slow to recover** | After the outage, does your retry backlog knock it over again?                                                                    |

That last row is the one most often missed. A recovering dependency meets every client's accumulated
retries at once — which is why jitter and retry budgets are recovery mechanisms, not politeness.

## Degrade, do not collapse

Classify every dependency, and make the classification visible in the code:

- **Essential** — the operation is meaningless without it. Payment authorization for a purchase; the
  order record itself. Fail the request clearly.
- **Enhancing** — improves the experience. Recommendations, loyalty points, review prompts. **Omit it
  and serve the rest.**

For the order page above:

```python
order = order_service.get(order_id)                 # essential — fail if unavailable
invoice = safe(billing_service.get_invoice, order_id, default=None)
tracking = safe(shipping_service.get_tracking, order_id, default=None)
loyalty = safe(loyalty_service.get_points, order.user_id, default=None)
reviews = safe(review_service.get_prompts, order_id, default=[])
return render(order, invoice, tracking, loyalty, reviews)   # renders with gaps
```

Availability is now that of `order_service`, and the page shows what it can.

Degradation must be **explicit and visible**, though. A silently missing section teaches users the data
is wrong; a section saying "temporarily unavailable" keeps their trust. And two categories must
**never** degrade toward permissiveness: authorization must fail closed, and money must never fake
success. See
[circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).

## Blast radius

Failures spread through what is shared. Reducing the radius means reducing sharing:

- **Shared pools** — one dependency's slowness exhausts capacity for everything. Bulkhead them.
- **Shared database** — one tenant's heavy query degrades all tenants. Separate pools, read replicas,
  or per-tenant limits.
- **Shared cache** — a cache outage becomes a database stampede. See
  [caching](caching.md).
- **Synchronous chains** — A→B→C→D means D's incident is A's incident. Break the chain with queues
  where the work tolerates asynchrony.
- **Deploy scope** — canary and staged rollout so a bad release affects a fraction. See
  [deployment-and-config](deployment-and-config.md).
- **Config scope** — a global config change has a blast radius of everything, instantly.

The reviewable question for a new dependency: *what else fails when this fails?* If the answer is
"most things," the coupling is the finding, not the dependency.

## Know that it is happening

Designing for failure includes noticing it. The specific gap to look for: **degradation is silent by
construction** — the system is working as designed, so nothing errors.

- **Alert on symptoms, not just crashes.** Error rate, p99 latency, saturation. A service can be
  fully "up" and useless.
- **Instrument the fallback paths.** If the loyalty section has been failing for a week, someone should
  know. A fallback nobody monitors becomes permanent.
- **Alert on circuit breaker state, retry rate, queue depth, and dead-letter depth.** Each is an early
  indicator that fires before users complain.
- **Make the degraded state observable** — a metric per dependency for "served without this."
- **Correlate with a request id across services**, or debugging a partial failure means reading five
  unrelated logs.

## Practise failing

A recovery path that has never been executed is an assumption:

- **Failover** — promote a replica deliberately. See
  [replication-and-sharding](replication-and-sharding.md).
- **Restore from backup** — the only thing that turns a backup into a backup.
- **Rollback** — exercise it, not just document it.
- **Dependency failure** — block a dependency in staging and see what the user gets. This is where
  fault injection earns its keep; chaos engineering is the disciplined version.
- **Load shedding** — verify it engages before the queue is deep.

The value is not proving the system survives. It is discovering the specific thing nobody thought of,
while it is cheap.

## The uncomfortable questions

Worth asking in any design review, and most systems answer at least one badly:

- If our largest dependency is down for an hour, what still works?
- If the database is read-only, can we serve anything?
- If the cache is empty, do we survive the load?
- If a deploy is bad, how long until we know, and how long to roll back?
- If we lose a whole availability zone, what happens?
- If a queue consumer has been down for six hours, does the backlog recover or make things worse?
- If two instances both think they are the leader, what breaks? See
  [consensus-and-leader-election](consensus-and-leader-election.md)
- What is silently degraded right now that we would not notice?

## Review checklist

- [ ] Every dependency is classified essential or enhancing
- [ ] Enhancing dependencies degrade without failing the request
- [ ] Auth fails closed; money never fakes success
- [ ] Degradation is visible to the user, not silent
- [ ] Combined availability of the dependency chain has been calculated
- [ ] Each dependency has answers for slow, down, wrong, partial, and overloaded
- [ ] Recovery-after-outage load (retry backlog) is considered
- [ ] Shared pools, caches, and databases are bulkheaded or per-tenant limited
- [ ] Synchronous chains are broken with queues where asynchrony is acceptable
- [ ] Fallback paths are instrumented and alerted on
- [ ] Circuit breaker state, retry rate, queue depth, and dead-letter depth are monitored
- [ ] Request ids correlate across services
- [ ] Failover, restore, and rollback have each been exercised
- [ ] Dependency failure has been tested by actually breaking it in a safe environment

---

*Synthesized from TechCraft's Distributed Systems series, part P19
([collection](https://www.patreon.com/techcraft_official)). The "everything will definitely break" and
avoid-versus-accept-failure framing are TechCraft's; the failure enumeration, blast-radius analysis,
uncomfortable questions, and checklist are this repository's.*
