---
name: system-design
description: Design a system from requirements — scope it, size it with back-of-the-envelope numbers, sketch the high-level architecture, pick building blocks by their trade-offs, then scale the bottlenecks. Use when asked to "design X", to choose between SQL/NoSQL, cache strategies, load balancing, replication, sharding, queues, or RPC vs REST, to estimate traffic/storage/availability, or to prepare for or run a system design interview.
argument-hint: "[system-to-design | topic]"
allowed-tools: Read, Grep, Glob
---

# System Design

[production-review](../production-review/SKILL.md) checks a diff against known failure modes.
This skill works the other way round: **from requirements to a design**, picking building blocks
by what they cost and what they buy.

**Rule: everything is a trade-off, and a design is only as good as the numbers that justify it.**
Pick a component without saying which constraint it answers, and you've decorated the diagram,
not designed anything.

## References

Read the reference for the step you're on. These are compressed. They name each option,
when to pick it and what it costs. Where a production lesson covers the same block in depth,
the card links to it. Follow that link before committing to the block.

| Reference                                                         | Read it when                                                                                                                                                              |
|-------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [estimation.md](references/estimation.md)                         | Sizing traffic, storage, bandwidth, memory; latency budgets; availability targets and nines                                                                               |
| [trade-offs.md](references/trade-offs.md)                         | Performance vs scalability, latency vs throughput, CAP, consistency levels, fail-over and replication patterns                                                            |
| [edge-and-services.md](references/edge-and-services.md)           | DNS, CDN, load balancers, reverse proxies, web vs app tier, microservices, service discovery, HTTP/TCP/UDP, RPC vs REST, security basics                                  |
| [data-and-caching.md](references/data-and-caching.md)             | Scaling an RDBMS (replication, federation, sharding, denormalization, tuning), NoSQL families, SQL vs NoSQL, cache layers and update strategies, queues and back pressure |
| [case-studies.md](references/case-studies.md)                     | Worked designs: Pastebin/bit.ly, Twitter timeline, web crawler, Mint, social graph, query cache, sales rank, scaling on AWS                                               |
| [object-oriented-design.md](references/object-oriented-design.md) | Class-level design questions: hash map, LRU cache, call center, deck of cards, parking lot, chat server                                                                   |
| [practice-and-reading.md](references/practice-and-reading.md)     | More practice questions, and the foundational papers and company architectures worth studying                                                                             |

## The method

Four steps, in order. Most bad designs come from skipping step 1 or step 4.

**1. Scope: use cases, constraints, assumptions.** Agree on what's in and what's out before
drawing anything. Get these answered or assumed out loud:

- Who uses it, and how? Which use cases are in scope? List the out-of-scope ones explicitly
- How many users, how much data, how many requests per second?
- What's the read:write ratio? (It decides caching, replication and fan-out strategy)
- Which requirements are hard: latency, availability, consistency, durability?

Then turn those into numbers with [estimation.md](references/estimation.md): QPS (average and
peak), storage over the retention period, bandwidth, and how much of it is hot.

**2. High-level design.** Draw the main components and the connections between them: client →
DNS/CDN → load balancer → web/app tier → cache → datastore, plus any async workers. Justify each
box by the constraint it answers. Keep it end-to-end, not deep.

**3. Core components.** Go deep on the two or three parts that carry the problem: the data model
and schema, the key or ID scheme, the API (see the production lessons on
[resource modelling](../production-review/references/api-resource-modeling.md) and
[contracts](../production-review/references/api-contracts.md)), and the read and write paths.
State which datastore and why. For a URL shortener, for example, that means hashing (MD5 → Base62),
collision handling, SQL vs NoSQL, the schema, and the lookup path.

**4. Scale.** With the numbers from step 1, find the bottleneck, fix it, and find the next one.
Typical sequence: load balancer + horizontal scaling (stateless app tier) → cache → read replicas →
federation/sharding → async queues → CDN. **Say the trade-off for each move.** Then run the
relevant [production-review](../production-review/SKILL.md) lessons over the result. Every
scaling move introduces a failure mode: replicas bring lag, caches bring stampedes, queues bring
duplicates. Those lessons catalogue them.

## Running a design session

- **Lead it.** It's an open-ended conversation. Propose, justify, invite correction.
- **Assume out loud.** When the user hasn't specified a number, pick a plausible one, label it as
  an assumption, and keep going. Don't stall waiting for requirements.
- **Breadth first, then depth** where the problem is hard. A complete shallow design beats a
  half-finished deep one.
- **Start simple.** Begin from a single box and add components only when a number forces them.
  [case-studies.md](references/case-studies.md) — the AWS one especially — shows that progression.
- **Name the alternative you rejected** and why. That's the trade-off being made visible.

## Output shape

When producing a design, deliver it in the method's order:

1. Scope: in/out use cases and stated assumptions
2. Estimates: QPS, storage, bandwidth, with the arithmetic shown
3. High-level diagram (ASCII is fine) with one line per component on why it's there
4. Core components: data model, API, read/write paths
5. Scaling: bottleneck → fix → trade-off, in order
6. Risks: the production-review lessons that apply and what to watch

*The method, building blocks and case studies are compressed from
[donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0). The structure, wording, cross-links to the production lessons and the notes marked
"dated" are this repository's.*
