# System Design Case Studies

**Every one of the eight designs starts simple and earns each new component by naming the bottleneck it removes:
estimate first, then cache the hot reads, push the slow work off the request path, and shard only what the numbers say
won't fit.**

All figures below are the primer's stated assumptions and its back-of-envelope arithmetic, not measurements. Conversion
it uses throughout: 2.5M s/month, so 1 req/s ≈ 2.5M req/month and 400 req/s ≈ 1B req/month.

## Contents

- [Pastebin / URL shortener](#pastebin--url-shortener)
- [Twitter timeline and search](#twitter-timeline-and-search)
- [Web crawler](#web-crawler)
- [Mint.com (personal finance aggregator)](#mintcom-personal-finance-aggregator)
- [Social graph (shortest friend path)](#social-graph-shortest-friend-path)
- [Query cache (key-value cache for search results)](#query-cache-key-value-cache-for-search-results)
- [Amazon sales rank by category](#amazon-sales-rank-by-category)
- [Scaling to millions of users on AWS](#scaling-to-millions-of-users-on-aws)
- [Patterns that recur](#patterns-that-recur)

## Pastebin / URL shortener

- **Scope:** in: anonymous user pastes text and gets a random link, optional expiry (default never), reads by link,
  monthly hit stats, expired-paste deletion, HA. Out: accounts, editing, visibility, custom shortlinks. Bit.ly is the
  same problem storing a URL instead of contents.
- **Numbers:** 10M users; 10M writes/mo → ~4 writes/s; 100M reads/mo → ~40 reads/s (10:1 read:write). Row ≈ 1 KB
  content + 7 B shortlink + 4 B expiry + 5 B created_at + 255 B path ≈ 1.27 KB → 12.7 GB/mo, ~450 GB and 360M shortlinks
  in 3 years.
- **Core design:**
  ```
  write: Client → Web (reverse proxy) → Write API → gen url, check dup in SQL → SQL `pastes` row + Object Store blob
  read:  Client → Web → Read API → SQL lookup by shortlink → Object Store fetch (else error)
  stats: web server logs → MapReduce ((yyyy-mm, url), 1) → sum → Analytics DB
  ```
    - `pastes(shortlink char(7) PK, expiration_length_in_minutes, created_at, paste_path)`; PK index enforces
      uniqueness, extra index on `created_at`.
    - Key = first 7 chars of `base62(md5(ip_address + timestamp))` (or MD5 of random data). Base62 is `[a-zA-Z0-9]`,
      URL-safe without escaping, unlike base64's `+` and `/`. 62^7 covers 360M links.
    - SQL acts as a big hash table url → path; a NoSQL KV store is the stated alternative. Expiry = periodic scan for
      expired rows, delete or mark.
- **Scaling moves:**
    - Hot pastes / uneven traffic and spikes → Memory Cache in front of the DB; SQL read replicas absorb misses.
    - Blob storage → managed Object Store (S3); 12.7 GB/mo is easy for it.
    - Analytics → data warehouse (Redshift/BigQuery) fed by batch jobs, since stats need not be realtime.
    - Writes (~4/s) → a single SQL write master-slave is fine; federation/sharding/denormalisation only if that changes.
- **The insight to steal:** split metadata (small, indexed, in SQL) from payload (blob in an object store); the URL key
  is a hash truncated to just enough entropy for the 3-year volume.

## Twitter timeline and search

- **Scope:** in: post tweet (fan out to followers, push notifications/email), user timeline, home timeline, keyword
  search, HA. Out: firehose/streams, visibility filtering (@reply hiding, hide retweets), analytics. Same shape as
  Facebook feed and search.
- **Numbers:** 100M active users; 500M tweets/day = 15B/mo → ~6,000 tweets/s. Avg fan-out 10 → 5B deliveries/day,
  150B/mo → ~60K deliveries/s. 250B reads/mo → ~100K reads/s. 10B searches/mo → ~4,000 searches/s. Tweet ≈ 8 B id + 32 B
  user_id + 140 B text + 10 KB avg media ≈ 10 KB → 150 TB/mo, 5.4 PB in 3 years.
- **Core design:**
  ```
  post:   Client → Web → Write API → SQL (author's user timeline)
                              └→ Fan Out Service → User Graph Service (followers, cached)
                                                 → append to each follower's home timeline (Memory Cache)
                                                 → Search Index Service; media → Object Store
                                                 → Notification Service via Queue (async)
  home:   Client → Web → Read API → Timeline Service → Memory Cache list (O(1))
                                  → multiget Tweet Info + User Info services (O(n))
  user:   Client → Web → Read API → SQL
  search: Client → Web → Search API → Search Service: parse/tokenise/normalise → scatter-gather Search Cluster (Lucene) → merge, rank, sort
  ```
    - Home timeline is a Redis list per user of 17-byte entries: `tweet_id (8 B) | user_id (8 B) | meta (1 B)`.
    - Fan-out on write is O(followers): 1,000 followers = 1,000 lookups and inserts. 60K deliveries/s is why a
      relational DB is ruled out for the home timeline.
- **Scaling moves:**
    - Fan Out Service is the bottleneck: a user with millions of followers takes minutes, racing their @replies →
      re-order tweets at serve time.
    - Celebrities → skip fan-out for highly followed users; at read time search their tweets and merge into the home
      timeline (hybrid push/pull).
    - Cache memory → keep only several hundred tweets per home timeline, and only users active in the last 30 days;
      rebuild inactive users from SQL via the User Graph Service.
    - Tweet Info keeps only a month of tweets; User Info only active users; Search Cluster kept in memory for latency.
    - SQL: read replicas alone won't cover misses and the write volume swamps one master → federation, sharding,
      denormalisation, NoSQL.
- **The insight to steal:** precompute the read (fan-out on write) because reads dominate, then special-case the heavy
  tail (fan-out on read for celebrities) instead of letting it set the design.

## Web crawler

- **Scope:** in: crawl a URL list, build a reverse index (word → pages) and static titles/snippets, user search over
  them (sketch only), HA. Out: search analytics, personalisation, PageRank. Constraint: no Solr/Nutch.
- **Numbers:** 1B links, re-crawled ~weekly → 4B crawls/mo. 500 KB stored per page → 2 PB/mo, 72 PB in 3 years. ~1,600
  writes/s; 100B searches/mo → ~40,000 searches/s.
- **Core design:**
  ```
  crawl loop: Crawler Service → pop top-ranked link from links_to_crawl (Redis sorted set)
              → similar signature already in crawled_links? → lower its priority, continue (breaks cycles)
              → else fetch; enqueue Reverse Index Service job + Document Service job (title/snippet)
                    add child urls to links_to_crawl; remove link; insert (url, signature) into crawled_links
  search:     Client → Web → Query API → parse → Reverse Index Service (match + rank top N) → Document Service (titles/snippets)
  ```
    - `links_to_crawl` and `crawled_links` in a KV NoSQL store; ranking via sorted sets; seeded by site popularity or
      link-heavy portals.
    - Dedup URLs with MapReduce (emit only keys with count 1, or `sort | unique` when small). Dedup content by page
      signature similarity (Jaccard index, cosine similarity).
    - Freshness: per-page `timestamp`, default weekly re-crawl, popular/fast-changing sites sooner; mine mean time
      between changes; honour `robots.txt`.
- **Scaling moves:**
    - Popular queries / uneven traffic → Memory Cache in front of Reverse Index and Document services.
    - Index and document data volume → heavy sharding and federation of those services.
    - DNS lookups throttle crawling → crawler keeps its own periodically refreshed DNS cache.
    - Connection overhead → connection pooling (UDP mentioned as a further boost); provision enough bandwidth.
- **The insight to steal:** model the crawl frontier as a priority queue and handle cycles by demoting near-duplicates
  rather than tracking a perfect visited set; indexing runs downstream off queues.

## Mint.com (personal finance aggregator)

- **Scope:** in: link financial accounts, extract transactions (daily, users active in last 30 days), categorise (manual
  override, no automatic re-categorisation), monthly spend by category, recommended + manual budgets, near/over-budget
  notifications (not instant), HA. Out: extra logging/analytics.
- **Numbers:** 10M users × 10 budget categories = 100M budget items; 50,000 sellers; 30M accounts; 5B
  transactions/mo → ~2,000 writes/s; 500M reads/mo → ~200 reads/s. Write-heavy, 10:1 write:read (people transact daily,
  rarely visit). Transaction ≈ 8 B user_id + 5 B created_at + 32 B seller + 5 B amount ≈ 50 B → 250 GB/mo, 9 TB in 3
  years.
- **Core design:**
  ```
  link:    Client → Web → Accounts API → SQL `accounts`
  extract: Accounts API → Queue (SQS/RabbitMQ) → Transaction Extraction Service
             → pull from bank, raw logs → Object Store
             → Category Service → Budget Service (→ Notification Service)
             → SQL `transactions`, `monthly_spending` → notify user via Queue
  read:    Client → Web → Read API → Memory Cache → (miss) SQL → fill cache   [cache-aside]
  ```
    - `transactions(id, created_at, seller, amount, user_id FK)` indexed on id, user_id, created_at;
      `monthly_spending(id, month_year, category, amount, user_id)`.
    - Category Service: seeded seller → category dict (50K × <255 B ≈ 12 MB in memory); unknown sellers fall back to
      crowdsourced user overrides, top override per seller from a heap.
    - Budget: template from income tiers (e.g. housing 40%, food 20%, gas 10%, shopping 20%); store only user overrides,
      not all 100M items.
    - Monthly aggregate via SQL query, or MapReduce over raw logs emitting `(user_id, yyyy-mm, category) → amount`,
      summed; reducer triggers budget notifications.
- **Scaling moves:**
    - Slow bank extraction on the request path → async Queue + workers.
    - Aggregation load on the OLTP DB → MapReduce on raw files; move `monthly_spending` to an Analytics DB (
      Redshift/BigQuery).
    - DB growth → keep ~1 month of `transactions` in SQL, older in warehouse/Object Store (250 GB/mo fits S3 easily).
    - Reads (~200/s) → cache sessions, category aggregates, recent transactions; replicas serve misses; static content
      via Object Store + CDN.
    - Writes (~2,000/s) likely too much for one master → federation, sharding, denormalisation, NoSQL.
- **The insight to steal:** for write-heavy ingest with rare reads, land raw data cheaply, then derive the small read
  model (monthly aggregates) in batch; store defaults implicitly and persist only overrides.

## Social graph (shortest friend path)

- **Scope:** in: search a person and see the shortest path to them, HA. Constraint: use traditional systems, no graph DB
  or GraphQL. Graph does not fit on one machine; edges unweighted.
- **Numbers:** 100M users × 50 friends avg = 5B friend relationships; 1B searches/mo → ~400 searches/s.
- **Core design:**
  ```
  Client → Web → Search API → User Graph Service
     loop BFS: Lookup Service (person_id → Person Server) → Person Server (person, friend_ids) → enqueue unvisited friends
  ```
    - Plain BFS with a `prev` map reconstructs the path; at scale the visited set lives in the service (`visited_ids`),
      not on the nodes.
    - Users sharded across Person Servers; every hop may need another Lookup Service call (flagged as the optimisation
      target).
- **Scaling moves:**
    - 400 reads/s and hot, well-connected users → Memory Cache for person data.
    - Repeated searches → cache complete or partial BFS traversals; or batch-compute them offline into NoSQL.
    - Cross-machine hops → batch friend lookups per Person Server; shard Person Servers by location (friends cluster
      geographically).
    - Search breadth → bidirectional BFS (from source and destination, merge); start from high-degree users; cap by time
      or hop count and ask the user before continuing.
    - Without the constraint: a graph DB (Neo4j) or graph query language.
- **The insight to steal:** when an algorithm is trivial on one box, the design problem becomes minimising network hops:
  co-locate likely neighbours and batch per shard.

## Query cache (key-value cache for search results)

- **Scope:** in: query hit served from cache, miss computed and cached, HA. Constraints: popular queries almost always
  cached, limited memory, millions of queries, fast lookups, low inter-machine latency.
- **Numbers:** 10M users; 10B queries/mo → ~4,000 req/s. Entry ≈ 50 B query + 20 B title + 200 B snippet = 270 B → 2.7
  TB/mo if every query were unique and kept, so eviction is mandatory.
- **Core design:**
  ```
  Client → Web → Query API → parse/normalise query (the cache key)
         → Memory Cache hit: move entry to LRU front, return
         → miss: Reverse Index Service (rank top) → Document Service (titles/snippets) → set in cache at front
  ```
    - LRU = hash map (query → node) + doubly linked list (head newest, tail evicted); O(1) get/set/evict.
    - Freshness: page content change, page add/remove, rank change → simplest answer is a TTL. Pattern is cache-aside.
- **Scaling moves:**
    - Load and memory beyond one machine → cache cluster, three options: each node its own cache (low hit rate), each
      node a full copy (wastes memory), or shard by `machine = hash(query)` (best; use consistent hashing).
- **The insight to steal:** normalising the query before lookup is what makes the cache hit; eviction policy plus TTL is
  the answer to "limited memory", and consistent hashing is the answer to "many machines".

## Amazon sales rank by category

- **Scope:** in: compute past week's most popular products per category, users view it, HA. Out: the rest of e-commerce.
  Items may be in several categories, never change category, no subcategories; refresh hourly (popular items maybe more
  often).
- **Numbers:** 10M products, 1,000 categories; 1B transactions/mo → ~400 writes/s; 100B reads/mo → ~40,000 reads/s (100:
  1). Transaction ≈ 5+8+4+8+8+4+5 B ≈ 40 B → 40 GB/mo, 1.44 TB in 3 years.
- **Core design:**
  ```
  compute: Sales API logs → Object Store → Sales Rank Service (MapReduce) → SQL `sales_rank`
  read:    Client → Web → Read API → SQL `sales_rank` (by category_id)
  ```
    - Two-step MapReduce: (1) filter to past week, emit `(category, product) → qty`, sum; (2) re-key as
      `(category, total) → product` so the shuffle's distributed sort produces the ranking; identity reducer.
    - `sales_rank(id, category_id, total_sold, product_id)` indexed on id, category_id, product_id.
- **Scaling moves:**
    - 40K reads/s → Memory Cache for popular products and their ranks; replicas may not cover misses →
      federation/sharding/denormalisation.
    - 400 writes/s may be too much for one master → same SQL scaling patterns, or NoSQL.
    - History → keep a limited window in SQL, rest in warehouse/Object Store; Analytics DB on Redshift/BigQuery.
- **The insight to steal:** "top N by group" over a large log is a batch job; encode the sort order in the MapReduce key
  and let the framework's shuffle do the distributed sort, then serve a precomputed table.

## Scaling to millions of users on AWS

- **Scope:** in: generic read/write service that must grow from one user to tens of millions with HA; relational data
  required. The point is the method: benchmark/load test → profile → fix the bottleneck weighing alternatives → repeat.
  AWS names are examples; the principles are general.
- **Numbers:** 10M users; 1B writes/mo → ~400 writes/s; 100B reads/mo → ~40,000 reads/s (100:1); 1 KB per write → 1
  TB/mo, 36 TB in 3 years.
- **Core design (the progression):**
  ```
  1 box:    DNS (Route 53) → Elastic IP → EC2 web server + MySQL; scale vertically, monitor CPU/mem/IO/net
  Users+:   static content → S3; MySQL → RDS on its own box; VPC public subnet (web) / private subnet (rest)
  Users++:  ELB (SSL termination) → many web servers across AZs; web tier split from app tier (read vs write APIs);
            MySQL master-slave failover; CDN (CloudFront) for static and some dynamic content
  Users+++: Memory Cache (ElastiCache) for hot MySQL data and sessions → stateless web tier; MySQL read replicas
            behind LBs, app logic splits reads/writes
  Users++++: Autoscaling groups per tier across AZs, triggered by time-of-day or CPU/latency/network/custom metrics;
            config management (Chef/Puppet/Ansible); host/aggregate/log/external monitoring, paging, error reporting
  Users+++++: old data → warehouse (Redshift); scale cache; federation/sharding/denormalisation/NoSQL (DynamoDB);
            async Queue + Workers for non-realtime work (e.g. upload → SQS → worker makes thumbnail → DB + S3)
  ```
- **Scaling moves:**
    - Vertical scaling gets expensive, has no failover, and couples web and DB → separate DB and static content first.
    - Single web server overloaded at peak, SPOF → load balancer + horizontal web tier across AZs.
    - Read-heavy DB load → try MySQL's own cache first, then Memory Cache, then read replicas.
    - Session state pins users to servers → move sessions to the cache so web servers are stateless and autoscalable.
    - Diurnal load and cost → autoscaling (downside: complexity and lag in scaling up or down).
    - Write volume and data growth → partition SQL, move suitable data to NoSQL, archive to a warehouse, queue batch
      work.
    - Every new component: encrypt in transit and at rest, open only needed ports (80/443, 22 from whitelisted IPs), no
      outbound from web servers.
- **The insight to steal:** each stage is triggered by a profiled bottleneck, and you may need only one or two of the
  moves at each stage; making the web tier stateless is the hinge that unlocks horizontal scaling and autoscaling.

## Patterns that recur

| Pattern                                                                       | Designs that use it                                                                               | Why                                                                                              |
|-------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Back-of-envelope estimate first (per-second rates, 3-year storage)            | all eight                                                                                         | Decides whether one SQL master, a replica set, or partitioning is needed before drawing boxes    |
| Memory cache in front of the DB, cache-aside                                  | Pastebin, Twitter, Crawler, Mint, Social graph, Query cache, Sales rank, AWS                      | Traffic is uneven; hot keys served from RAM (1 MB from memory ≈ 250 µs; SSD 4x, disk 80x slower) |
| SQL read replicas behind a write master                                       | Pastebin, Twitter, Mint, Sales rank, AWS                                                          | Read-heavy ratios (10:1, 100:1) are absorbed without touching the write path                     |
| Federation / sharding / denormalisation / NoSQL when one master can't keep up | Twitter, Mint, Sales rank, AWS, Crawler, Social graph                                             | Write rates of hundreds to thousands per second exceed a single master                           |
| Fan-out on write (precomputed timelines)                                      | Twitter                                                                                           | Reads vastly outnumber writes; makes the home timeline an O(1) list read                         |
| Fan-out on read for the heavy tail                                            | Twitter (celebrities)                                                                             | Avoids minutes-long fan-out for accounts with millions of followers                              |
| Batch precompute with MapReduce over raw logs                                 | Pastebin (hit counts), Crawler (URL dedup), Mint (monthly spend), Sales rank (top N)              | Non-realtime results computed offline keep aggregation load off the serving DB                   |
| Object store for blobs and raw logs                                           | Pastebin, Twitter, Mint, Sales rank, AWS                                                          | Cheap, durable, scales past GB-to-PB per month; DB keeps only metadata and pointers              |
| Queue to decouple slow or bursty work                                         | Twitter (notifications), Crawler (index jobs), Mint (extraction, notifications), AWS (thumbnails) | Request path returns fast; workers scale independently and absorb spikes                         |
| Data warehouse for analytics and cold history                                 | Pastebin, Mint, Sales rank, AWS                                                                   | Keeps OLTP tables small (e.g. one month hot) and analytics off the primary                       |
| Hash-based partitioning, consistent hashing                                   | Query cache, Social graph (Person Servers + lookup)                                               | Spreads keys across machines; consistent hashing limits reshuffling on resize                    |
| LRU eviction plus TTL                                                         | Query cache                                                                                       | Bounded memory with freshness when underlying data changes                                       |
| Stateless web/app tier behind a load balancer                                 | AWS (explicitly), implied in all                                                                  | Prerequisite for horizontal scaling, failover across AZs and autoscaling                         |
| Iterate: benchmark → profile → fix → repeat                                   | all eight (Step 4 of each)                                                                        | Prevents jumping to the final architecture without evidence                                      |

*Compressed from [donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer) (CC BY 4.0),
`solutions/system_design/{pastebin,twitter,web_crawler,mint,social_graph,query_cache,sales_rank,scaling_aws}/README.md`.
Structure and wording are this repository's; designs and estimates are the primer's.*
