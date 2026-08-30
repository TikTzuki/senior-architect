# API Contracts & Versioning

**Rule: an API is not an endpoint, it is a promise you cannot withdraw.** Internal code can be
rewritten overnight; a published contract cannot, because someone else's system depends on it.

Part of the API set: **contracts & versioning** (this file) ·
[resource modeling](api-resource-modeling.md) · [list endpoints](api-list-endpoints.md) ·
[async operations](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The contract is the product](#the-contract-is-the-product)
- [Contract-first](#contract-first)
- [What counts as breaking](#what-counts-as-breaking)
- [Versioning](#versioning)
- [Deprecation is a process](#deprecation-is-a-process)
- [Governance, when good engineers are not enough](#governance-when-good-engineers-are-not-enough)
- [Review checklist](#review-checklist)

## The anti-pattern

```diff
  {
    "id": "ord_123",
-   "user_id": "usr_9",
+   "customer_id": "usr_9",     // "clearer, avoids confusion with other IDs"
    "total": 1500
  }
```

Internal integration tests pass — they were regenerated from the new schema. Thirty minutes after
deploy, a partner's entire payment module is down, because their parser reads `user_id` and now gets
`null`.

Nothing was wrong with the *reasoning*. `customer_id` genuinely is clearer. The mistake is believing
a field name is an implementation detail once it has been published.

## Why development hides it

You control every consumer in development, so a rename is a project-wide find-and-replace and the
tests you own are the only tests that exist.

In production the consumers are **other people's systems**, on their release cycles, some of which
you cannot enumerate and none of which you can deploy. The property that makes API changes dangerous
— unknown consumers you cannot update — is entirely absent locally.

## The contract is the product

> An API is not an endpoint. An API is a promise: *I will provide this data, in this shape, and keep
> it stable, so your system can depend on it.*

Consumers do not read your architecture diagrams, know your framework, or care about your schema.
The contract is the entire surface they experience, and it is the only thing they can build on.

That inverts the usual priority. Internal code quality is recoverable — refactor it and move on. A
contract mistake is permanent for as long as any consumer remains, which in practice means years.

Writing an API is easy. **Writing an API you can still support in three years** is the actual skill,
and it means the contract must answer questions that only appear later: how do consumers page
through growth, filter without unbounded cost, retry safely, learn about failures, and migrate when
you change.

## Contract-first

The failure mode is designing the API as the tail end of the database:

```
tables  →  ORM models  →  serializers  →  endpoints
```

The result exposes your storage decisions as public commitments. Normalize a table later and the API
shape changes with it, which is a breaking change caused by an internal refactor — the exact
coupling a contract is supposed to prevent.

Contract-first inverts it: **agree the contract, then build behind it.** Practical consequences worth
checking in review:

- The schema (OpenAPI, protobuf, GraphQL SDL) is written and reviewed **before** implementation, and
  is the artifact consumers and teams argue about.
- Clients can be generated and mock servers stood up immediately, so frontend, mobile, and partner
  work proceeds in parallel instead of waiting.
- Storage is free to change, because nothing in the contract mirrors it.
- The contract is checked in CI: a diff that breaks it fails the build, rather than being discovered
  by a partner.

Contract-first is also a negotiation between people with different priorities — the backend lead
wants a clean model, the DBA wants efficient storage, the product owner wants it next week. Having
one artifact to disagree about is the point.

## What counts as breaking

The distinction consumers actually experience:

**Safe (additive):**

- Adding a new optional response field
- Adding a new optional request parameter with a default
- Adding a new endpoint
- Adding a value to an enum **only if** consumers are documented to tolerate unknown values

**Breaking:**

- Renaming or removing any field
- Changing a type (`"1500"` → `1500`, integer → string ID)
- Making an optional request field required
- Tightening validation that previously accepted input
- Changing default sort order, pagination size, or default filtering
- Changing an error code, or the shape of an error response
- Changing timezone, precision, or units of an existing field

The last three are the ones teams miss, because none of them changes the schema. **Behaviour is part
of the contract**, not just structure. A consumer that relies on default ordering — see
[list endpoints](api-list-endpoints.md) — breaks when you "improve" it, and no schema diff will
catch that.

## Versioning

Versioning is not "add `/v2`". It is choosing who absorbs the cost of change.

| Strategy                                                  | Notes                                                                                               |
|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| **URL path** (`/v2/orders`)                               | Most visible and cache-friendly; encourages whole-API versions, which are expensive to maintain     |
| **Header** (`Accept: application/vnd.api+json;version=2`) | Clean URLs; easy for a client to omit, and harder to debug                                          |
| **Field-level / additive-only**                           | No versions at all — only additive change, forever. Lowest consumer cost, highest design discipline |

**Additive-only is the goal**; versioning is what you do when you failed to achieve it. Every live
version is a branch you support, test, and secure, and versions do not retire on their own — that is
why "we'll just bump to v3" is usually an admission of a permanent cost.

Two rules that prevent most versioning pain: **never version the whole API when one resource
changed** (version the resource), and **never let a shared internal model serialize two versions** —
that is how a v1 fix silently changes v2.

## Deprecation is a process

Announcing a removal is not deprecation. A workable sequence:

1. **Announce** with a date, in changelog, docs, and directly to identified consumers.
2. **Instrument** — log usage per consumer of the deprecated field or endpoint. You cannot retire
   what you cannot measure, and usage data is what turns an argument into a plan.
3. **Signal in-band** — `Deprecation` and `Sunset` headers, so a client sees it without reading docs.
4. **Contact the remaining users** identified in step 2.
5. **Brownout** — return errors for short scheduled windows before permanent removal, so silent
   dependencies surface while someone is watching.
6. **Remove**, with a rollback path ready.

Without step 2 you are guessing, and the removal becomes an incident.

## Governance, when good engineers are not enough

> Good people do not save you at 10,000 endpoints.

Individual skill produces locally excellent, mutually inconsistent APIs: three date formats, four
pagination styles, five error shapes, two auth schemes. Every inconsistency is a permanent tax on
every consumer.

The point of governance is not to constrain design but to make the boring decisions **once**:
error shape, pagination style, date and money representation, naming, auth, versioning policy, and
what counts as breaking.

Make it cheap and automatic — a linter in CI, a shared schema library, generated clients — because
governance enforced by review meetings decays the moment delivery pressure arrives. And keep an
escape hatch with a named owner: a standard nobody can deviate from with justification gets
circumvented instead of amended.

## Review checklist

- [ ] The contract is designed and reviewed before implementation, not derived from tables
- [ ] Response shapes do not mirror database schema
- [ ] The schema is checked in CI; breaking diffs fail the build
- [ ] No field renames, type changes, or newly required request fields on a published API
- [ ] Behavioural changes (default sort, page size, error codes, units) are treated as breaking
- [ ] Additive-only change is the default; a new version is justified, not routine
- [ ] Versions are scoped to resources, not the whole API
- [ ] No shared internal model serializes two API versions
- [ ] Deprecated fields and endpoints have per-consumer usage instrumentation
- [ ] Deprecation announces a date and signals in-band (`Deprecation` / `Sunset`)
- [ ] Cross-team conventions exist for errors, pagination, dates, money, and auth
- [ ] Conventions are enforced by tooling, not by reviewer memory

---

*Synthesized from TechCraft's API Design Patterns series, parts P1–P2 and P13–P15
([collection](https://www.patreon.com/techcraft_official)). The "API is a promise, not an endpoint"
framing, the contract-first argument against database-driven design, and "good people do not save you
at 10,000 endpoints" are TechCraft's; the breaking-change taxonomy, deprecation sequence, and
checklist are this repository's.*
