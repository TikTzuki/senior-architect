# Deployment & Configuration

**Rule: config takes production down faster than code, because it skips every safeguard code must
pass.** And a deploy is a design problem, not a command.

Part of the backend set: [concurrency model](server-concurrency-model.md) ·
[memory & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed locks](distributed-locks.md) ·
[health checks & load balancing](health-checks-and-load-balancing.md) ·
**deployment & config** (this file)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Why config is more dangerous than code](#why-config-is-more-dangerous-than-code)
- [Config discipline](#config-discipline)
- [Feature flags and zombie flags](#feature-flags-and-zombie-flags)
- [A deploy is a design, not a command](#a-deploy-is-a-design-not-a-command)
- [The strategies](#the-strategies)
- [What deploys actually break](#what-deploys-actually-break)
- [Review checklist](#review-checklist)

## The anti-pattern

```yaml
# One-character change. No tests. Straight to production.
database:
  pool_size: 5          # was 50
  timeout_ms: 100       # was 5000
```

No code changed, so nothing ran: no unit tests, no type checker, no code review checklist, no canary.
The pool starves instantly and the 100 ms timeout triggers a retry storm that finishes the job.

And the deploy equivalent:

```bash
kubectl set image deploy/api api=myapp:v2      # "the deploy"
```

Treated as a command rather than a design. In-flight requests are dropped, the new version's
migration has not run — or has run and is incompatible with the old pods still serving — and there is
no defined way back.

## Why development hides it

Config in development is a committed file with values that work, changed rarely and by the person who
understands them. There is no separate production value to diverge, and no runtime override.

Deploys locally mean restarting a process with no traffic. Nothing is in flight, no connection
drains, no schema is shared with a previous version, and rollback is `git checkout`. **Every property
that makes production deploys hard is absent by construction.**

## Why config is more dangerous than code

> Not code — config is what can take down production fastest.

Code passes through compilation, type checking, tests, review, CI, and staged rollout. Config
typically passes through none of them, yet it controls the same behaviour:

- **Timeouts and retries** — a timeout set too low turns a slow dependency into a retry storm;
  retries set too high amplify an incident instead of surviving it.
- **Pool sizes and limits** — see [connection-pools-and-latency](connection-pools-and-latency.md)
  and [server-concurrency-model](server-concurrency-model.md).
- **Feature flags** — flip application behaviour instantly, for everyone, with no deploy.
- **Endpoints and credentials** — point production at the wrong database, or the wrong environment
  entirely.

It is also **global and instant**. A bad code deploy rolls out pod by pod and can be caught by a
canary. A bad config value can apply everywhere in the time it takes to save, with no gradual
exposure and often no audit trail.

## Config discipline

- **Never hardcode; always inject** — environment variables, a config service, or mounted secrets, so
  the same artifact runs in every environment. That is the point of the separation.
- **Validate at startup, and fail fast.** Parse and range-check every value when the process boots. A
  pool size of `5` when the minimum viable is `20` should refuse to start, not degrade mysteriously
  under load. This single practice catches most config incidents.
- **Treat config changes as changes.** Version control, review, and staged rollout — the same path as
  code. "It's just a config change" is the sentence that precedes the incident.
- **Never log or expose secrets.** Redact at the logging boundary; a config-dump endpoint or a
  debug log of the loaded config is a credential leak.
- **Own defaults deliberately.** A default that is safe in development (small pool, short timeout,
  verbose logging) is frequently wrong in production, and the absence of an override is invisible.
- **Keep the blast radius in mind for dynamic config.** Runtime-reloadable values are powerful and
  bypass deployment safety entirely; the ones that can cause an outage deserve the same rollout care
  as code.

## Feature flags and zombie flags

Flags decouple deploy from release, which is genuinely valuable: ship dark, enable gradually, disable
without a rollback.

The cost is **zombie flags** — flags that outlive their purpose and are never removed. Each one is a
live conditional branch, so `n` flags mean up to `2ⁿ` reachable paths, most never tested together.
Old flags also become a reliability hazard in themselves: nobody remembers what the "off" side does,
and no test covers it.

Treat every flag as having an expiry: an owner, a removal date, and cleanup as part of the work rather
than a follow-up ticket that never gets done. A flag still present a year after full rollout is
technical debt with a switch attached.

## A deploy is a design, not a command

The requirement that shapes everything: during a rolling deploy, **two versions of your application
run simultaneously against one database**. So:

- The schema must be compatible with both versions — which is exactly
  [expand and contract](schema-migrations.md).
- Messages emitted by the new version must be readable by consumers still on the old one, and vice
  versa. See [message-delivery-semantics](message-delivery-semantics.md).
- Any cached value written by one version must be interpretable by the other, or keys must be
  versioned. See [caching](caching.md).

## The strategies

| Strategy       | How                                               | Cost                                                                                     |
|----------------|---------------------------------------------------|------------------------------------------------------------------------------------------|
| **Rolling**    | Replace instances in batches                      | Both versions live at once; slow rollback                                                |
| **Blue-green** | Two full environments, switch traffic             | Instant rollback; double infrastructure, and shared-database compatibility still applies |
| **Canary**     | Route a small % to the new version, watch, expand | Best risk/exposure ratio; needs real metrics and automation                              |

Canary is usually the right default for a service under load, and its dependency is honest signals:
error rate, latency, and a business metric compared between versions. A canary nobody measures is a
rolling deploy with extra steps.

Whatever the strategy, **rollback must be tested.** A rollback path that has never been exercised is
an assumption — the same category as an untested backup or an unexercised failover in
[replication-and-sharding](replication-and-sharding.md).

## What deploys actually break

The parts that live outside the container image, and are therefore the parts people forget:

- **In-flight requests.** The process must catch `SIGTERM`, fail readiness, drain, then exit — and
  the orchestrator's grace period must exceed the longest request. Otherwise every deploy sheds
  errors.
- **Long-lived connections.** WebSocket and SSE clients are dropped on every deploy; they need
  reconnect-with-backoff on the client, or connection draining.
- **Background jobs and consumers.** A worker killed mid-message must have its work redelivered
  safely — which requires the idempotency in
  [message-delivery-semantics](message-delivery-semantics.md).
- **Migrations.** Must run in an order safe for both versions, and must not lock tables during peak
  traffic. See [schema-migrations](schema-migrations.md).
- **Scheduled tasks.** A deploy mid-execution can skip or double-run a job.
- **Cold start.** Fresh instances have empty local caches, cold JIT, and unwarmed pools. Deploying
  everything at once means every instance is slow simultaneously — and if the cache tier was also
  restarted, that is a [cache avalanche](caching.md).

## Review checklist

- [ ] No hardcoded config; all injected per environment
- [ ] Every config value is validated at startup, failing fast on invalid or missing values
- [ ] Timeout, retry, and pool values are reviewed as carefully as code
- [ ] Config changes are version-controlled, reviewed, and rolled out in stages
- [ ] Secrets are never logged or exposed through an endpoint
- [ ] Production defaults are explicit, not inherited from development
- [ ] Every feature flag has an owner and a removal date; stale flags are deleted
- [ ] The deploy assumes two versions running against one database
- [ ] Schema changes follow expand/contract; migrations are safe for both versions
- [ ] Message and cache formats are compatible in both directions during rollout
- [ ] `SIGTERM` triggers readiness-false, then drain, then exit — within the grace period
- [ ] Grace period exceeds the longest expected request
- [ ] Long-lived connections reconnect with backoff
- [ ] Rollback has been tested, not just documented
- [ ] Canary decisions are driven by measured error rate and latency
- [ ] Cold-start cost is considered; instances are not all replaced simultaneously

---

*Synthesized from TechCraft's Backend Internals series, parts P20–P21
([collection](https://www.patreon.com/techcraft_official)). The "config is faster than a code bug"
framing, the deployment-as-design argument, the rolling/blue-green/canary comparison, and the
zombie-flags hazard are TechCraft's; the review structure and checklist are this repository's.*
