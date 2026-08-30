# Dev Insider Roadmap — Progress

Path from developer to senior engineer. Structure:
[dev-insider-roadmap.md](plugins/production-patterns/skills/roadmap/references/dev-insider-roadmap.md).

**States:** `[ ]` not started · `[~]` learned · `[x]` captured as a review lesson

Ordering is `Runtime → Correctness → Distributed → Production → Architecture → Leadership`.
Follow it in order — the roadmap's own thesis is that learning the right thing at the wrong time
is what stalls people at mid-level.

---

## Start here

- [ ] **AI-Proof Engineer** — what is losing and gaining value in the industry

## Tier 1 — Foundations

- [x] Backend Internals — *6 lessons captured from P2–P21 (P1 not recoverable)*
- [x] Database Internals — *reconciled + 3 new lessons from P1–P20*
- [x] Data Modeling Patterns — *5 lessons captured from P1–P17 + Idempotency*
- [x] Transaction & Consistency — *5 lessons captured from P1–P20*

## Tier 2 — Distributed Thinking

- [x] API Design Patterns — *6 lessons captured from P1–P15*
- [x] Distributed Systems Explained — *5 lessons + 6 reconciliations from P1–P19*
- [x] Replication & Scaling — *studied P1–P7; session-consistency captured, replication-and-sharding extended*
- [x] Event Sourcing & CQRS — *1 lesson captured: event-sourcing-and-cqrs*

## Tier 3 — Production Data Systems

- [ ] Data Architecture
- [ ] Production Data Operation — *partial lesson: schema-migrations*
- [ ] Production Database Design
- [ ] Database Optimization — *partial lesson: indexes-and-query-plans*

## Tier 4 — Production System Design

- [x] Payment System — *7 lessons captured from P1–P10 (P11–P15 unpublished)*
- [ ] Wallet System
- [ ] Banking System
- [ ] Order System
- [ ] Inventory System
- [ ] Chat System
- [ ] Notification System
- [ ] News Feed
- [ ] Search Engine
- [ ] Recommendation System
- [ ] Design Mindset

## Tier 5 — Technical Leadership

- [ ] Architecture Fundamentals
- [ ] Reliability Engineering
- [ ] Scaling Engineering
- [ ] Engineering Decision Making
- [ ] Software Evolution
- [ ] Senior Engineering Mindset
- [ ] Tech Lead Playbook
- [ ] Engineering Leadership

---

## Lessons captured so far

**42 lessons across 9 of 31 series.** They cluster into seven sets, and those sets are
published as sidebar groups and tags on the site. `knowledge-map.yaml` at the repo root is
the machine-readable source for both, so this table and the site cannot disagree.

| Set                        | Source series                              | Lessons |
|----------------------------|--------------------------------------------|---------|
| Backend runtime            | Backend Internals                          | 7       |
| Storage & databases        | Database Internals · Replication & Scaling | 7       |
| Transactions & consistency | Transaction & Consistency                  | 5       |
| Data modeling              | Data Modeling Patterns                     | 4       |
| API design                 | API Design Patterns                        | 6       |
| Distributed systems        | Distributed Systems Explained              | 5       |
| Payments                   | Payment System                             | 8       |

Three lessons predate the roadmap and sit in the set they most resemble rather than one
captured from a series: `large-file-upload` (runtime), `lock-contention` (storage) and
`schema-migrations` (storage — its own series, Production Data Operation, is otherwise
untouched).

Marking a series `[x]` requires studying it and capturing what it taught. A series carrying
a single lesson is noted as such rather than claimed as complete.
