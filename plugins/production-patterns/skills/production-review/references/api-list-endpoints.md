# List Endpoints: Pagination, Filtering & Sorting

**Rule: a list endpoint is a cost-control mechanism first and a feature second.** Unbounded
`LIMIT`, arbitrary filters, and arbitrary sorts each hand a client the ability to take down your
database.

Part of the API set: [contracts & versioning](api-contracts.md) ·
[resource modeling](api-resource-modeling.md) · **list endpoints** (this file) ·
[async operations](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Pagination is a contract, not a convenience](#pagination-is-a-contract-not-a-convenience)
- [Offset drifts and gets slower](#offset-drifts-and-gets-slower)
- [Keyset pagination](#keyset-pagination)
- [Sorting is a promise about the database](#sorting-is-a-promise-about-the-database)
- [Filtering and search](#filtering-and-search)
- [What the contract must state](#what-the-contract-must-state)
- [Review checklist](#review-checklist)

## The anti-pattern

```python
@app.get("/orders")
def list_orders(sort_by: str = "created_at", order: str = "desc",
                limit: int = None, **filters):
    q = f"SELECT * FROM orders WHERE 1=1"
    for k, v in filters.items():
        q += f" AND {k} = '{v}'"                    # arbitrary columns
    q += f" ORDER BY {sort_by} {order}"             # arbitrary sort
    if limit: q += f" LIMIT {limit}"                # optional limit
    return db.query(q)
```

Ignore the injection for a moment — the *design* is the problem. A client can request every order
ever created, sorted by an unindexed column, filtered on a field with no index. One call becomes a
full table scan plus a disk sort.

> Business logic is not wrong by a single line. Architecturally, the system died of its own success.

At 500 rows this endpoint is excellent. At 50 million it is a denial-of-service vector you shipped
yourself and documented as a feature.

## Why development hides it

The dev table has hundreds of rows. Every scan is instant, every sort fits in memory, `SELECT *` with
no limit returns a small payload, and every filter combination is fast.

**Every one of those properties is a function of row count**, and none of them is tested. The
endpoint is not merely slow later — it is a different endpoint later, because the query plan changes
with the data. See [indexes-and-query-plans](indexes-and-query-plans.md).

## Pagination is a contract, not a convenience

Pagination does three things, only one of which is user-facing:

1. **Bounds query cost** — the database reads and sorts a bounded set.
2. **Bounds response size** — memory, serialization, and transfer are bounded. See
   [memory-and-gc](memory-and-gc.md).
3. Lets a consumer walk large data predictably.

So a list endpoint without a **server-enforced maximum** page size is unbounded by design:

```python
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
limit = min(int(request.args.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
```

Clamp rather than error, and document the clamp. And note that both the default and the maximum are
part of the contract — changing them later is a behavioural break, per
[api-contracts](api-contracts.md).

## Offset drifts and gets slower

`LIMIT 20 OFFSET 100000` has two independent defects.

**It gets slower with depth.** The database must produce and discard 100,000 rows to return 20. Cost
grows linearly with page number, so the last page of a large list is the most expensive request in
your system.

**It drifts when data changes.** Offset addresses a *position in a result set*, not a place in the
data:

```
page 1  →  rows 1–20 of newest-first
           a new row is inserted
page 2  →  OFFSET 20  →  the row previously #20 is now #21 → returned twice
```

A client paging through 10,000 records while writes happen will see duplicates and miss records —
silently. For a UI that is annoying; for a reconciliation job or an export it is a correctness bug
that produces wrong totals with no error. See
[payment-reconciliation](payment-reconciliation.md).

Offset is acceptable for small, bounded, human-browsed lists where jumping to page 7 matters. It is
wrong for anything a machine consumes.

## Keyset pagination

Page by *where you got to*, not by how many you skipped:

```sql
SELECT * FROM orders
WHERE (created_at, id) < (:last_created_at, :last_id)   -- strict, tie-broken
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

```json
{ "data": [...], "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wMy0xNVQxMDozMDowMFoiLCJpZCI6MTIzfQ" }
```

Constant cost at any depth (it seeks the index), and stable under concurrent writes — an inserted row
does not shift the boundary.

Three implementation details that are each a bug if missed:

- **The sort key must be unique**, or use a composite with a tiebreaker (`created_at, id`). A
  non-unique cursor either skips or repeats rows at page boundaries.
- **Opaque cursors.** Encode the position; do not document its structure. A client that parses your
  cursor has bound itself to your sort implementation, and now you cannot change it.
- **Match the index.** `ORDER BY created_at DESC, id DESC` needs an index in that order — otherwise
  keyset pagination is a sort of the whole table on every page.

## Sorting is a promise about the database

Every sortable field is a commitment to sort a growing table efficiently. Without a matching index,
the database sorts in memory until it exceeds the sort buffer, then **spills to disk** — and disk
sorts on a large table are the classic 2am page.

So: **allowlist sortable fields, and index every one.**

```python
SORTABLE = {"created_at", "updated_at", "total"}     # each indexed
if sort_by not in SORTABLE:
    raise BadRequest(f"sort_by must be one of {sorted(SORTABLE)}")
```

Two further points that catch people out. **Always append a unique tiebreaker** — sorting by a
non-unique column gives an unstable order, so equal rows can appear in different positions across
pages and a row is duplicated or skipped even with keyset pagination. And **the default sort is part
of the contract**: consumers rely on it whether you documented it or not, so "improving" it is a
breaking change.

## Filtering and search

> A search API is the second most dangerous endpoint in production.

The danger is combinatorial. Ten optional filters produce over a thousand shapes, and you cannot
index them all — so some combination will be a table scan, and you will not know which until a client
finds it.

What to do:

- **Allowlist filterable fields**, and know the plan for each. Never build predicates from arbitrary
  client keys.
- **Cap the combination space** — require at least one selective filter (a tenant, a date range, an
  owner) so no query is unbounded.
- **Bound ranges.** An open-ended date filter is a full scan; require a range and cap its width.
- **Do not offer substring search over a large table from your primary database.** `LIKE '%x%'` cannot
  use a B-tree — see [indexes-and-query-plans](indexes-and-query-plans.md). Use a trigram index, a
  real search engine, or do not offer it.
- **Set a `statement_timeout`** for list endpoints, so a bad combination fails fast instead of
  occupying a connection. See [connection-pools-and-latency](connection-pools-and-latency.md).

The promise worth designing toward: *however large the data grows and however filters combine, this
endpoint answers within its budget.* If you cannot make that promise for a query shape, do not expose
that shape — offer it as an [async operation](api-async-operations.md) instead.

## What the contract must state

Consumers depend on all of this whether you write it down or not:

- Default and maximum page size
- Pagination style, and whether cursors are stable across pages
- Default sort order, and the exact set of sortable fields
- The exact set of filterable fields and their semantics (exact match, range, prefix)
- Whether totals are exact, approximate, or absent — **an exact `total_count` on a large filtered
  table is a second expensive query**, and often the most costly part of the response
- Whether results can shift between pages under concurrent writes

## Review checklist

- [ ] Every list endpoint has a server-enforced maximum page size
- [ ] Machine-consumed lists use keyset pagination, not offset
- [ ] Cursors are opaque and include a unique tiebreaker
- [ ] The pagination ORDER BY matches an existing index
- [ ] Sortable fields are allowlisted and each is indexed
- [ ] Sorts include a unique tiebreaker for stable ordering
- [ ] The default sort is documented and treated as part of the contract
- [ ] Filterable fields are allowlisted; no predicate is built from arbitrary client keys
- [ ] At least one selective filter is required; open-ended ranges are capped
- [ ] No substring search against a large primary-database table
- [ ] `statement_timeout` bounds list queries
- [ ] `total_count` is only returned when its cost is justified, and its exactness is documented

---

*Synthesized from TechCraft's API Design Patterns series, parts P4–P6
([collection](https://www.patreon.com/techcraft_official)). The pagination-as-cost-control framing,
"the system died of its own success", "search is the second most dangerous endpoint", and the sort
buffer / external merge sort analysis are TechCraft's; the keyset details and checklist are this
repository's.*
