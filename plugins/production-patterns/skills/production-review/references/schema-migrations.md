# Schema Migrations Under Load

**Rule: a migration's danger is how long it holds its lock, not how long it runs.** A lock request
that waits also blocks every query queued behind it.

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [The lock queue: the part that surprises people](#the-lock-queue-the-part-that-surprises-people)
- [Which DDL is safe](#which-ddl-is-safe)
- [Expand and contract](#expand-and-contract)
- [Backfills](#backfills)
- [Seatbelts](#seatbelts)
- [Review checklist](#review-checklist)

## The anti-pattern

```sql
-- Migration 004
ALTER TABLE orders ADD COLUMN region varchar(32) NOT NULL DEFAULT 'us-east';
CREATE INDEX idx_orders_region ON orders (region);
UPDATE orders SET region = derive_region(shipping_country);
```

On a development database with 500 rows this completes instantly and the migration is approved.
On 80 million rows in production it takes an `ACCESS EXCLUSIVE` lock, rewrites the table, builds
an index while blocking writes, and runs a full-table `UPDATE` in one transaction — during which
every read and write to `orders` is blocked.

The migration is *correct*. It produces exactly the intended schema. It is also an outage.

## Why development hides it

The three variables that make DDL dangerous are all absent locally: table size, concurrent
traffic, and lock queueing. A migration against an empty table with no other connections cannot
demonstrate any of them, so the local run tells you only that the SQL parses and the result is
right.

Staging usually fails to catch it too, unless staging holds production-scale data *and* has load
running against it during the migration. Most do not.

## The lock queue: the part that surprises people

This is the mechanism that turns a slow migration into a total outage, and it is not obvious.

PostgreSQL grants lock requests in order. A pending `ACCESS EXCLUSIVE` request does not step aside
for readers that would otherwise be compatible with each other — **they queue behind it**:

```
T1  long-running SELECT, holds ACCESS SHARE ..................... running
T2  ALTER TABLE, wants ACCESS EXCLUSIVE .......................... waiting on T1
T3  SELECT (would be compatible with T1) ......................... waiting on T2
T4  SELECT ......................................................... waiting on T2
...  every subsequent query on the table ......................... waiting
```

So an `ALTER TABLE` that would have taken 2 milliseconds blocks the entire table for as long as
**one unrelated slow query** takes to finish. A single forgotten analytics query, or one
connection idle in transaction, is enough.

The consequence for review: it is not sufficient that the DDL itself is fast. You must also bound
how long it might *wait*, which is what `lock_timeout` is for.

## Which DDL is safe

Behaviour is version-specific — verify against your engine's release notes. For modern PostgreSQL
(11+):

**Safe: metadata-only, no table rewrite**

- `ADD COLUMN` with a `NULL` default, or with a constant `DEFAULT` (11+ stores it in the catalog)
- `DROP COLUMN` (marks the column dead; space reclaims later)
- `ADD CONSTRAINT ... NOT VALID`, then `VALIDATE CONSTRAINT` separately
- `RENAME` a column or table — instant, but see the compatibility warning below
- `CREATE INDEX CONCURRENTLY`, `DROP INDEX CONCURRENTLY`
- `ALTER TABLE SET STATISTICS`, most storage-parameter changes

**Dangerous: rewrites the table or blocks writes**

- `ADD COLUMN ... NOT NULL` without a default on an older version — full rewrite
- `ALTER COLUMN TYPE` — rewrite, except a few widening cases (e.g. `varchar(n)` to `varchar(m)`
  for `m > n`, or to `text`)
- `SET NOT NULL` — scans the whole table to verify, holding `ACCESS EXCLUSIVE`. Since PG 12 an
  existing validated `CHECK (col IS NOT NULL)` lets it skip the scan; add the check `NOT VALID`,
  validate it, then `SET NOT NULL`.
- `CREATE INDEX` without `CONCURRENTLY` — blocks writes for the whole build
- `ADD FOREIGN KEY` without `NOT VALID` — scans and locks both tables
- Adding a `DEFAULT` on old versions (pre-11) — full rewrite

`CREATE INDEX CONCURRENTLY` costs two table scans and cannot run inside a transaction — so it must
be its own migration, and many migration frameworks wrap each file in a transaction by default and
need an explicit opt-out. It can also fail and leave an `INVALID` index behind, which must be
dropped and rebuilt; check `pg_index.indisvalid` afterwards.

**`RENAME` deserves its own warning.** The DDL is instant and safe. The *deploy* is not: old
application code referencing the old name breaks the moment it commits. Renames must go through
expand/contract like any other incompatible change.

MySQL differs meaningfully — many `ALTER`s support `ALGORITHM=INPLACE, LOCK=NONE`, and tools like
`gh-ost` and `pt-online-schema-change` exist because the ones that do not are so painful. Check
per statement rather than assuming.

## Expand and contract

Any change that is not backward compatible must be split so that old and new application code can
run simultaneously — which they always do, during a rolling deploy.

Splitting `users.name` into `first_name` / `last_name`:

1. **Expand.** Add the new columns, nullable. Deploy. Schema now supports both shapes.
2. **Dual-write.** Application writes both old and new. Deploy. Nothing reads the new columns yet.
3. **Backfill.** Populate the new columns for existing rows, in batches (below).
4. **Migrate reads.** Switch reads to the new columns. Deploy. Verify.
5. **Contract.** Stop writing the old column. Deploy. Then drop it, in a later release.

Slow and unglamorous, and it is the only approach that survives a rolling deploy and permits a
rollback at every step. The common failure is compressing steps 4 and 5 into one deploy: reads
move to a column the previous version does not populate, and rolling back loses data written in
between.

**Keep the drop in a separate, later release from the last write.** Once the column is gone, the
rollback path is gone with it.

## Backfills

Never `UPDATE` an entire large table in one statement. It holds row locks on everything it
touches, generates a huge amount of WAL, blocks vacuum for its duration, and — if it fails at 90%
— rolls all of it back.

Batch it, committing between chunks:

```sql
-- Repeat until zero rows affected. Keyset, not OFFSET.
UPDATE orders SET region = derive_region(shipping_country)
WHERE id IN (
  SELECT id FROM orders
  WHERE region IS NULL
  ORDER BY id
  LIMIT 5000
);
```

- Run it **outside** the schema migration, as a separate job. Schema changes should be fast;
  data changes are long-running work with different failure characteristics.
- Make it resumable and idempotent — the `WHERE region IS NULL` predicate does both here.
- Index the predicate the backfill scans on, or each batch degrades into a full scan. A partial
  index on `WHERE region IS NULL` is ideal: it shrinks as the backfill progresses.
- Pause between batches under load, and watch replication lag — a fast backfill can push read
  replicas minutes behind, which is its own user-visible outage.

## Seatbelts

Set these for every migration session:

```sql
SET lock_timeout = '3s';        -- fail fast rather than queueing behind a slow query
SET statement_timeout = '30s';  -- bound the statement itself
```

With `lock_timeout`, a migration that cannot get its lock promptly **fails instead of blocking the
table**. Retry it in a loop; a failed migration attempt is a non-event, a locked table is an
incident. This single setting prevents the most common way migrations cause outages.

Also worth having: a rule that migrations do not deploy simultaneously with a long-running
analytics query, and a check for open long transactions before starting.

## Review checklist

- [ ] Every DDL statement classified against the engine's rewrite/lock behaviour for its version
- [ ] `lock_timeout` and `statement_timeout` set for the migration session
- [ ] `CREATE INDEX` uses `CONCURRENTLY`, in its own non-transactional migration
- [ ] `NOT NULL` and foreign keys added via `NOT VALID` then `VALIDATE`, not in one blocking step
- [ ] Backfills are separate from schema changes, batched, resumable, and indexed on their predicate
- [ ] Backfill batching watches replication lag
- [ ] Incompatible changes follow expand/contract; old and new code can both run against the schema
- [ ] Column drops and renames ship in a later release than the code change that stopped using them
- [ ] Rollback is possible at every intermediate step
- [ ] The migration was timed against production-scale data, not the dev database

---

*Written from established practice. TechCraft's Database Internals series
([collection](https://www.patreon.com/collection/2115129)) prompted the topic area but does not cover
schema migrations — of its 20 parts, none addresses DDL under load, so nothing here is drawn from it.*
