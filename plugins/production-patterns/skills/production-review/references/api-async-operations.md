# Async & Long-Running Operations

**Rule: when the answer does not exist yet, do not pretend the request can return it.** Model the
operation as a resource the client can poll, and the timeout stops being your problem.

Part of the API set: [contracts & versioning](api-contracts.md) ·
[resource modeling](api-resource-modeling.md) · [list endpoints](api-list-endpoints.md) ·
**async operations** (this file) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The assumption that breaks](#the-assumption-that-breaks)
- [Operation as a resource](#operation-as-a-resource)
- [Polling that does not hurt](#polling-that-does-not-hurt)
- [Recovery is the actual requirement](#recovery-is-the-actual-requirement)
- [Notifying instead of polling](#notifying-instead-of-polling)
- [Where the boundary is](#where-the-boundary-is)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
@app.post("/reports/statement")
def generate_statement(req):
    rows = db.query_five_years_of_transactions(req.customer_id)  # 90s
    xlsx = build_excel(rows)  # 40s
    url = s3.upload(xlsx)  # 25s
    return {"download_url": url}  # ~155s later
```

The load balancer cuts the connection at 60 seconds. The client sees a 504 and retries — starting a
second 155-second job while the first still runs. Three retries in, five identical jobs are competing
for the same database.

The work usually *succeeds*. The client never learns that, because the only channel for the answer
was a connection that no longer exists.

## Why development hides it

Locally the dataset is small, so the report takes 800 ms and returns cleanly. There is no proxy
enforcing a timeout, no client retry, and no concurrency.

The failure scales with **customer data size**, so it appears for your largest and most important
accounts first — the VIP customer requesting five years of statements is precisely the request that
breaks.

## The assumption that breaks

> "An API, when called, must return the final result immediately."

For most endpoints that is fine. For anything whose duration depends on data volume or an external
system, it is a **lie with a deadline**: every layer between client and server enforces a timeout you
do not control — browser, CDN, load balancer, service mesh, client library.

The reframing that fixes it: the response to a long operation is not the result. It is an
**acknowledgement that the work has been accepted**, plus a way to find out how it went.

Once the client holds a durable handle, a dropped connection is no longer a lost result. That is the
whole point — not speed, but **recoverability**.

## Operation as a resource

Give the work an identity, exactly as in
[api-resource-modeling](api-resource-modeling.md):

```http
POST /reports
Idempotency-Key: 8f2c...          ← so a retry does not start a second job

202 Accepted
Location: /operations/op_7c1a
{
  "id": "op_7c1a",
  "status": "pending",
  "created_at": "2026-08-31T10:00:00Z"
}
```

```http
GET /operations/op_7c1a

200 OK
{
  "id": "op_7c1a",
  "status": "running",           # pending | running | succeeded | failed | cancelled
  "progress": { "percent": 40 },
  "retry_after": 5
}
```

On completion the operation carries its result — or its error:

```json
{
  "status": "succeeded",
  "result": {
    "download_url": "https://..."
  },
  "completed_at": "...",
  "expires_at": "..."
}

{
  "status": "failed",
  "error": {
    "code": "source_unavailable",
    "message": "...",
    "retryable": true
  }
}
```

Three properties make this work, and each is a review item:

- **`202` with a `Location`** — the client has a durable handle before anything can go wrong.
- **An idempotency key on submission** — a retried POST returns the *existing* operation rather than
  starting a duplicate. This is the single most important detail; without it, retries multiply
  expensive work. See
  [payment-state-and-idempotency](payment-state-and-idempotency.md).
- **A terminal state that persists.** The operation record outlives the worker, so a client that
  disconnects for an hour can still collect its result.

## Polling that does not hurt

Naive polling replaces one long request with thousands of short ones:

- **Tell the client how often to poll** — `retry_after` in the body, or a `Retry-After` header. Do
  not leave the interval to the client's imagination.
- **Suggest backoff** — fast at first (the operation may finish quickly), then slower.
- **Make the status endpoint cheap.** It will be called far more than the submit endpoint; it should
  read one row, never recompute progress. A status endpoint that queries the underlying data defeats
  the purpose.
- **Support `ETag` / `304`** so unchanged polls are nearly free.
- **Rate limit it separately** from the submission endpoint — see
  [rate limiting](rate-limiting.md) — with a higher allowance, since polling is expected.

## Recovery is the actual requirement

An async API is a **stateful workflow**, so the design questions are recovery questions:

- **Where does state live?** In a durable store, not worker memory. A worker restart mid-operation
  must not lose the operation.
- **What happens when a worker dies?** The operation must be reclaimable — a lease with a timeout, or
  a stuck-detection sweep. Otherwise it sits in `running` forever, which is the most common bug in
  these systems. See [distributed-locks](distributed-locks.md).
- **Is the work resumable or restartable?** Either is fine; "neither" is not. A five-minute job that
  cannot resume must at least be safely re-runnable, which means idempotent side effects.
- **Are partial results visible?** If a job writes as it goes, a failure leaves partial output that
  someone may read. Either write atomically at the end, or mark output incomplete.
- **How long do results live?** State `expires_at`, and enforce it — otherwise operation records and
  generated files accumulate forever.
- **Can it be cancelled?** If yes, cancellation is itself a transition with a state, not a fire-and-
  forget signal.

**Stuck operations need detection, not hope.** Alert on operations in a non-terminal state past a
threshold; it is the same reasoning as the stuck-`pending` alert in
[payment-reconciliation](payment-reconciliation.md).

## Notifying instead of polling

Polling is the reliable default; notification is the optimization:

- **Webhooks** — you call the client when the operation finishes. Removes polling entirely, and now
  you own delivery guarantees. See [webhook-provider-design](webhook-provider-design.md).
- **Server-sent events / WebSocket** — good for live progress, and they pin the client to an instance,
  which complicates deploys. See
  [health-checks-and-load-balancing](health-checks-and-load-balancing.md).

**Always keep the polling endpoint** even when you offer webhooks. A client that misses a webhook, or
cannot receive one at all, needs a way to find out — and support needs it too.

## Where the boundary is

| Duration              | Shape                                                |
|-----------------------|------------------------------------------------------|
| < 1s                  | Synchronous                                          |
| 1–10s                 | Synchronous, with a timeout the client is told about |
| 10s – minutes         | **Async operation resource**                         |
| Minutes – hours       | Async operation + webhook, with progress             |
| Unbounded / scheduled | A job resource with its own lifecycle and history    |

Two things that force async regardless of measured duration: **the work depends on a third party**
whose latency you do not control, and **duration scales with customer data**, meaning your largest
customer defines the worst case. Both mean the fast path today is the timeout tomorrow.

## Review checklist

- [ ] Operations whose duration scales with data or depends on a third party are async
- [ ] Submission returns `202` with a durable operation handle
- [ ] Submission accepts an idempotency key; a retry returns the existing operation
- [ ] Operation state is durable, surviving worker restart
- [ ] Terminal states persist long enough for a disconnected client to collect the result
- [ ] Failures carry a machine-readable code and a retryable flag
- [ ] The status endpoint is cheap and does not recompute progress
- [ ] Clients are told the poll interval (`Retry-After` / `retry_after`)
- [ ] Status polling is rate limited separately from submission
- [ ] Dead workers release their operations (lease or stuck-detection sweep)
- [ ] Operations stuck in non-terminal states are alerted on
- [ ] Result retention (`expires_at`) is stated and enforced
- [ ] A polling path exists even when webhooks are offered

---

*Synthesized from TechCraft's API Design Patterns series, parts P8–P9
([collection](https://www.patreon.com/techcraft_official)). The "must return the final result
immediately" assumption as a trap, the shift from managing a request to managing a workflow, and the
resilience framing are TechCraft's; the operation-resource mechanics and checklist are this
repository's.*
