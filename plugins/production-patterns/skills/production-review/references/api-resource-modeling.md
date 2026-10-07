# API Resource Modeling

**Rule: an API manages the lifecycle of resources; it does not expose your actions or your tables.**
Most bad APIs come from modelling the database or the current sprint's verbs, not the business.

Part of the API set: [contracts & versioning](api-contracts.md) ·
**resource modeling** (this file) · [list endpoints](api-list-endpoints.md) ·
[async operations](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Ask what the resource is](#ask-what-the-resource-is)
- [Lifecycle, not actions](#lifecycle-not-actions)
- [When a verb is the right answer](#when-a-verb-is-the-right-answer)
- [One endpoint is one permission](#one-endpoint-is-one-permission)
- [Tables are not resources](#tables-are-not-resources)
- [State transitions as sub-resources](#state-transitions-as-sub-resources)
- [Review checklist](#review-checklist)

## The anti-pattern

```
POST /createOrder
POST /updateOrderStatus
POST /cancelOrderAndRefund
POST /markOrderAsShippedAndNotifyCustomer
POST /approveOrderForVipCustomerWithDiscount
```

Each endpoint was a reasonable response to a ticket. Together they are the whole problem: the API is
a list of **things the last sprint needed done**, with no model underneath.

The consequences compound. There is no way to ask "what is the state of this order?" Every new
requirement adds an endpoint, so the surface grows without bound. Two endpoints mutate the same
state with different validation. And no consumer can predict what exists — they must read a list.

## Why development hides it

Each endpoint works, is tested, and satisfies its ticket. Nothing fails.

The cost is **structural and only visible in aggregate** — at endpoint 5 the API looks fine; at
endpoint 60 it is unlearnable and every change risks a behaviour that some other endpoint also
implements. There is no moment where a test goes red, which is why this survives indefinitely.

## Ask what the resource is

The question that prevents most of it: *"What actually is an `Order` in this system?"*

It sounds trivial and usually is not. Is an order the customer's intent, or the fulfilment record?
Is a cancelled order still an order? Is an order with three shipments one resource or four? Does the
order own its payment, or reference it — see
[payment-order-consistency](payment-order-consistency.md), where treating them as one thing is the
defect.

Answering this before writing endpoints is what turns a verb list into a model. Answering it *after*
means the model is whatever the endpoints happened to imply.

## Lifecycle, not actions

> An API is not a place to expose actions. It is a place to manage the lifecycle of a resource.

So model the noun and the states it can be in, then let operations move it between them:

```
POST   /orders                     create      → pending
GET    /orders/{id}                read
PATCH  /orders/{id}                modify allowed fields
POST   /orders/{id}/cancellation   pending     → cancelled
POST   /orders/{id}/shipment       paid        → shipped
GET    /orders/{id}/shipments      the shipments that exist
```

What this buys, concretely:

- **The state machine is visible.** A consumer can ask what state a resource is in and what
  transitions exist — see [payment-state-and-idempotency](payment-state-and-idempotency.md) for why
  that state machine matters.
- **New requirements add fields or transitions**, not endpoints. Growth is bounded.
- **One place validates each transition**, so two paths cannot disagree.
- **It is predictable.** A consumer who knows one resource can guess the next.

## When a verb is the right answer

Purity here is a trap, and forcing every operation into CRUD produces worse APIs than accepting
verbs where they fit:

- **Genuine actions with no resource** — `POST /orders/{id}/refund-estimate` computes something and
  stores nothing.
- **Multi-resource operations** — a transfer touches two accounts and belongs to neither;
  `POST /transfers` models it as its own resource, which is also the honest answer.
- **Batch and bulk** — `POST /orders/bulk-import` is clearer than pretending it is a collection
  write.
- **Expensive computations** — `POST /reports` creating a job resource is right; see
  [async operations](api-async-operations.md).

The test is not "is this a noun" but **"can a consumer predict this exists, and does it have one
clear effect?"** `POST /orders/{id}/cancellation` and `POST /orders/{id}/cancel` are both fine.
`POST /cancelOrderAndRefundAndNotify` is not — not because of grammar, but because it bundles three
effects that will need to vary independently.

## One endpoint is one permission

The bundling argument above is about code that has to change together. There is a sharper version
of it that is a security property, and it is the reason a `type` field is not a neutral choice:

```
POST /wallets/transactions   { "type": "DEPOSIT",  "amount": 500000 }
POST /wallets/transactions   { "type": "WITHDRAW", "amount": 500000 }
```

Same method, same path. **Every layer that makes decisions on method and path now sees one
operation**: the gateway, the RBAC policy, the WAF rule, the rate limit, the audit log. Grant a
support agent the ability to credit a customer and you have granted the ability to drain one —
the only thing between the two is an `if` inside the handler, maintained by whoever last added a
`type` value.

Development hides this completely. One route is less code, one test, one place to look, and the
permission model is usually a single `@requires_auth` that looks entirely correct. The gap only
opens when the second `type` arrives, which is typically after the authorization design is
finished and nobody revisits it.

What it costs in production:

- **Escalation by editing a field.** The authorization boundary is a string in a request body,
  checked by application code rather than by the policy layer that was audited.
- **Policy tooling goes blind.** You cannot write "this role may deposit but not withdraw" in any
  gateway, service mesh, or IAM policy, because they match on paths. The rule has to be
  reimplemented inside the handler, where no one reviews it as authorization.
- **The audit log cannot answer "who withdrew".** Access logs record `POST /wallets/transactions`.
  Reconstructing intent means parsing archived request bodies, assuming you stored them — and
  bodies are exactly what gets redacted.
- **Rate limits cannot be per-operation.** A cheap, high-frequency operation and a dangerous,
  rare one share one budget.
- **Validation drifts across branches.** One request schema serves every type, so a field that is
  required for one is optional-and-ignored for another, and a rule written for the first silently
  applies to the second.

Split the path, even when one handler serves all of them: `POST /wallets/deposits`,
`/wallets/withdrawals`, `/wallets/transfers`. Shared implementation is fine; a shared *path* is
not. The test is not "is this one piece of code" but **"should these be grantable, auditable, and
rate-limitable separately?"** If yes, they are separate resources no matter how similar the code.

## Tables are not resources

Storage and contract answer different questions, and the mapping between them should be deliberate:

- **One resource can span several tables.** An `Order` with lines, addresses, and totals is one thing
  to a consumer.
- **One table can serve several resources.** A `users` table may back `/customers` and `/staff` with
  different fields and permissions.
- **Some resources have no table.** A dashboard summary, a computed availability, an aggregate.
- **Some tables must never surface.** Join tables, audit rows, outbox and inbox tables, internal
  flags. Exposing an outbox is exposing a mechanism your consumers should never see.

Two signals in review that storage has leaked into the contract: an endpoint named after a join
table, and a response containing a field that only makes sense given your schema (`is_deleted`,
`version`, `partition_key`, `outbox_status`).

**Do not expose internal identity or state as contract.** A soft-delete flag from
[data-retention-and-history](data-retention-and-history.md) is an implementation choice; consumers
should see the resource as absent, not receive a flag and be trusted to filter it.

## State transitions as sub-resources

The pattern that scales best for anything with a lifecycle. Instead of a mutable status field driven
by whatever endpoint got called:

```
POST /orders/{id}/cancellation     { "reason": "customer_request" }
  → creates a cancellation record, moves the order to cancelled
GET  /orders/{id}/cancellation     → who cancelled it, when, and why
```

Three properties fall out for free: the transition is **idempotent-able** (a second POST can return
the existing cancellation rather than cancelling twice — see
[payment-state-and-idempotency](payment-state-and-idempotency.md)); it is **auditable**, because the
record carries actor, time, and reason instead of being lost in a status overwrite (see
[data-retention-and-history](data-retention-and-history.md)); and it is **queryable**, so "why was
this cancelled" is an endpoint rather than a support ticket.

Compare with `PATCH /orders/{id} {"status": "cancelled"}`, which lets a client drive the state
machine directly, silently permits illegal transitions unless you re-validate, and records nothing
about why.

## Review checklist

- [ ] Resources are named for business concepts, not tables or join tables
- [ ] The question "what is this resource?" has an explicit answer before endpoints exist
- [ ] The resource's states and legal transitions are defined
- [ ] New requirements add fields or transitions, not new verb endpoints
- [ ] No endpoint bundles multiple independent effects
- [ ] No endpoint selects its operation from a `type` or `action` field in the body
- [ ] Operations that should be granted separately have separate paths
- [ ] Audit log and rate limit can tell operations apart without parsing a request body
- [ ] Verb-shaped endpoints exist only where genuinely warranted, and are predictable
- [ ] No internal field (`is_deleted`, `version`, partition or outbox state) appears in responses
- [ ] Clients cannot set status directly to drive the state machine
- [ ] Lifecycle transitions are modelled as sub-resources where auditability matters
- [ ] Each transition is validated in exactly one place

---

*Synthesized from TechCraft's API Design Patterns series, part P3
([collection](https://www.patreon.com/techcraft_official)). The "API manages resource lifecycles, not
actions" framing, the "what actually is an Order?" question, and the type-dispatched wallet
endpoint behind "One endpoint is one permission" are TechCraft's; the verb-exception analysis,
transition-as-sub-resource treatment, the authorization/audit/rate-limit consequences worked out
for that endpoint, and the checklist are this repository's.*
