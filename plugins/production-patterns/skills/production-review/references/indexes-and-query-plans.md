# Indexes & Query Plans

**Rule: an index that exists is not an index that is used.** A query is fast in development
because the table is small, not because the plan is good.

Part of the storage set: [storage engines](storage-engines.md) ·
**indexes & query plans** (this file) · [replication & sharding](replication-and-sharding.md) ·
[session consistency](session-consistency.md) · [connection pools & latency](connection-pools-and-latency.md)

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [Why an existing index goes unused](#why-an-existing-index-goes-unused)
- [Composite indexes and the leading-column rule](#composite-indexes-and-the-leading-column-rule)
- [N+1 queries](#n1-queries)
- [Reading EXPLAIN](#reading-explain)
- [The cost of an index](#the-cost-of-an-index)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
-- The index exists.
CREATE INDEX idx_users_email ON users (email);

-- None of these can use it.
SELECT * FROM users WHERE lower(email) = 'a@b.com';       -- function on the column
SELECT * FROM users WHERE email LIKE '%@b.com';           -- leading wildcard
SELECT * FROM users WHERE email::text = 'a@b.com';        -- type coercion
```

Each falls back to a sequential scan. The index is present, the query returns correct results, and
nothing anywhere reports a problem — the query is simply reading the whole table every time.

## Why development hides it

A sequential scan over 200 rows takes under a millisecond. The same scan over 20 million rows
takes seconds and saturates disk I/O. **The plan is identical; only the row count changed.**

Worse, the planner chooses differently at different table sizes. Below a few thousand rows a
sequential scan genuinely *is* faster than an index lookup, so PostgreSQL correctly ignores your
index in development. You cannot tell "the planner is being sensible about a small table" from
"this index can never be used" by observing behaviour locally. Only `EXPLAIN` on
production-like data distinguishes them.

This is why a query can pass review, pass CI, pass staging, and take down production on the day a
table crosses a threshold nobody was watching.

## What an index actually is

Not a row-finding structure — **a device for reducing how many pages come off disk.** The engine
reads fixed-size pages, never individual rows, so the only thing an index changes is page count.
See [storage-engines](storage-engines.md).

That reframing explains the behaviours below. A sequential scan on a small table is *correct*
because streaming a few pages beats random single-page reads. A covering index is fast because it
answers from index pages and never touches the table. And "the index is not being used" is usually
the planner estimating that the index would cost more pages than the scan.

## Why an existing index goes unused

**A function or expression wraps the column.** `WHERE lower(email) = ?` cannot use an index on
`email`. Either index the expression — `CREATE INDEX ON users (lower(email))` — or store the
normalized value in its own column. The same applies to `date(created_at)`, `CAST(...)`, and
string concatenation.

**Implicit type coercion.** Comparing a `varchar` column to an integer parameter, or a `bigint`
column to a numeric literal, can force a cast on the column side and disable the index. This one
is nearly invisible in review because the SQL looks clean; it shows up in `EXPLAIN` as a filter
rather than an index condition. Common with IDs sent as strings by a JSON client.

**Leading wildcard in LIKE.** `LIKE 'prefix%'` uses a B-tree; `LIKE '%suffix'` cannot, because a
B-tree is ordered by prefix. For substring or fuzzy search use a trigram index (`pg_trgm`) or a
real full-text index.

**`OR` across different columns.** `WHERE a = 1 OR b = 2` often cannot use an index on either.
Rewriting as `UNION` of two indexed queries is usually dramatically faster.

**Low selectivity.** An index on a boolean, or on a status column where 95% of rows share one
value, will be ignored for the common value — correctly, since reading most of the table via
random index lookups is slower than scanning it. A **partial index** fixes exactly this:
`CREATE INDEX ON jobs (created_at) WHERE status = 'pending'` is small, hot, and always used by the
query that matters.

**Stale statistics.** The planner chooses from estimates. After a bulk load or a large delete the
estimates can be wildly wrong, producing a good plan for a table shape that no longer exists.
`ANALYZE` after bulk changes.

**`NULL` semantics.** `WHERE col != 'x'` does not match rows where `col IS NULL`, and `NOT IN`
with a `NULL` anywhere in the list returns no rows at all. Both are correctness bugs before they
are performance bugs.

## The planner works from statistics, not data

The chain that decides your plan: **statistics → estimated cost → chosen plan.** The optimizer never
looks at your data. It looks at a statistical summary — row counts, distinct values, most common
values, histograms — and picks the cheapest plan *according to that summary*.

Two consequences worth carrying into review:

- **Stale statistics produce confidently wrong plans.** After a bulk load, a large delete, or a
  sudden distribution change, the summary describes a table that no longer exists. The plan is
  optimal for a fiction. `ANALYZE` is the fix, and autovacuum's analyze threshold is worth checking
  on high-churn tables.
- **Data skew defeats a single plan.** With `status` 95% `'completed'`, one plan cannot be right for
  both `status = 'completed'` (scan) and `status = 'pending'` (index). Extended statistics, a partial
  index on the rare value, or increased histogram resolution (`ALTER TABLE ... SET STATISTICS`) are
  the levers. This is also why a query can be fast for months and then pathologically slow for one
  parameter value.

`EXPLAIN` shows estimates; `EXPLAIN ANALYZE` shows both, and the gap between them is the fastest
route to the real problem.

## Composite indexes and the leading-column rule

An index on `(a, b, c)` is sorted by `a`, then `b` within equal `a`, then `c`. That ordering
determines what it can serve:

| Query predicate             | Uses `(a, b, c)`?                                |
|-----------------------------|--------------------------------------------------|
| `a = ?`                     | yes                                              |
| `a = ? AND b = ?`           | yes                                              |
| `a = ? AND b = ? AND c = ?` | yes, fully                                       |
| `b = ?`                     | **no** — `b` is not the leading column           |
| `a = ? AND c = ?`           | partially — seeks on `a`, filters `c`            |
| `a > ? AND b = ?`           | seeks on `a` only; a range stops further seeking |

Two rules follow, and they cover most composite-index mistakes:

1. **Equality columns first, range columns last.** Once a range predicate is used, columns after
   it in the index can only filter, not seek. `(status, created_at)` serves
   `status = 'x' AND created_at > y`; `(created_at, status)` serves it far worse.
2. **A separate index on the leading column is redundant.** If `(a, b)` exists, an index on `(a)`
   alone adds write cost for nothing. Reviewing for *redundant* indexes is as valuable as
   reviewing for missing ones.

**Covering indexes.** If the index contains every column the query needs, the database answers
from the index alone and never touches the table — an index-only scan. `INCLUDE (...)` in
PostgreSQL adds payload columns without affecting ordering. This is the difference between fast
and very fast on hot queries.

## N+1 queries

```python
orders = db.query("SELECT * FROM orders WHERE user_id = ?", user_id)   # 1 query
for order in orders:
    order.items = db.query("SELECT * FROM items WHERE order_id = ?", order.id)   # N queries
```

Each query is individually fast and correctly indexed. The problem is the count: 50 orders is 51
round trips. At 1 ms of network latency each that is 51 ms; across a network boundary at 5 ms it
is a quarter of a second, for a page that should take 10 ms.

This is the most common database performance defect in application code, and ORMs generate it by
default through lazy loading — the loop looks like plain attribute access, so nothing in the code
reads as a query at all.

Fix by loading in one round trip: an eager-load / `JOIN FETCH` directive, or fetch the children
for all parent IDs at once (`WHERE order_id = ANY(:ids)`) and group in memory. Note that eager
loading a to-many association via `JOIN` multiplies rows — most ORMs handle this with a second
query rather than one join, which is correct and still O(1) round trips.

To catch it: assert on query count in tests for hot endpoints. It is the only reliable way, since
N+1 is invisible in code review by construction.

The reason it hurts more than it looks: the cost is **round trips**, not query complexity. Each
query is properly indexed and genuinely fast — the defect is the *count*, multiplied by network
latency. That multiplier is a deployment property, so the same code is fine on localhost and
catastrophic across availability zones. See
[connection-pools-and-latency](connection-pools-and-latency.md).

## Reading EXPLAIN

Use `EXPLAIN (ANALYZE, BUFFERS)` — plain `EXPLAIN` shows estimates only, and estimates are exactly
what is wrong when a plan is bad.

The two things to look at first:

1. **Estimated vs actual rows.** `rows=10` with `actual rows=45000` means the planner is working
   from a false premise, and every choice above that node is suspect. Usually stale statistics, a
   correlation the planner cannot see, or a predicate it cannot estimate.
2. **`loops=N` on an inner node.** The node's reported time is *per loop*. A 0.5 ms node with
   `loops=10000` is five seconds. This is where N+1-shaped work hides inside a single query.

Then: `Seq Scan` on a large table with a selective filter means a missing or unusable index.
`Filter: ...` with a large `Rows Removed by Filter` means rows are being read and thrown away —
the predicate is not in the index condition. `Nested Loop` over a large outer set is often a bad
plan from bad estimates.

`BUFFERS` shows pages read; comparing `shared hit` to `shared read` tells you whether a slow query
is slow because of I/O or because of the plan.

## The cost of an index

Indexes are not free, and reviews that only ever add them make the system worse:

- Every `INSERT`, `UPDATE`, and `DELETE` must maintain every index on the table. A table with
  twelve indexes has slow writes, and it is usually nobody's job to notice.
- Indexes consume storage and cache. An unused index evicts pages a used one needs.
- `UPDATE`s that touch an indexed column cost more; PostgreSQL's HOT optimization only applies
  when no indexed column changes.

Find unused indexes in `pg_stat_user_indexes` (`idx_scan = 0` over a representative period),
and drop them. Check `pg_stat_statements` for what actually runs before adding one.

## Review checklist

- [ ] Every `WHERE`, `JOIN`, and `ORDER BY` column on a large table is indexed, or deliberately not
- [ ] No function, cast, or expression wraps an indexed column in a predicate
- [ ] Parameter types match column types — no implicit coercion on the column side
- [ ] Composite indexes lead with equality columns; range predicates come last
- [ ] No redundant index that is a prefix of another
- [ ] Selective-subset queries use a partial index rather than a low-selectivity full one
- [ ] No N+1: loops over a result set do not issue queries; hot paths assert query counts in tests
- [ ] `EXPLAIN (ANALYZE, BUFFERS)` was run on production-like data, not a dev table
- [ ] Estimated vs actual rows are close in the plan
- [ ] Queries returning many rows are paginated, using keyset pagination rather than large `OFFSET`
- [ ] `ANALYZE` runs after bulk loads or large deletes
- [ ] New indexes are justified against their write cost; unused ones are dropped

---

*Originally written from established practice, then reconciled against TechCraft's Database
Internals series, parts P2–P5 and P19
([collection](https://www.patreon.com/collection/2115129)). The index-as-page-reducer framing, the
statistics → cost → plan chain, and the data-skew analysis come from that series; the tables, fix
ordering, and checklist are this repository's.*
