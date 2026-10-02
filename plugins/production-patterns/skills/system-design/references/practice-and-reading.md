# Practice Questions & Reading List

**Rule: read real architectures for the principles they share, not their details.** For each one,
ask what problem each component solves, where it works and where it doesn't, and what the team
learned.

## Practice questions

Work each one through the four-step method in [SKILL.md](../SKILL.md). The hint column names the
building block that carries the problem. Hints are this repository's.

| Design…                                                           | The hard part                                                   | Starting point                                                                                                                                       |
|-------------------------------------------------------------------|-----------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| File sync (Dropbox)                                               | Chunking and dedup, metadata vs blob split, conflict resolution | [How we've scaled Dropbox](https://www.youtube.com/watch?v=PE4gwstWhmc)                                                                              |
| Search engine (Google)                                            | Inverted index sharding, ranking, crawl freshness               | [The anatomy of a search engine](http://infolab.stanford.edu/~backrub/google.html)                                                                   |
| Collaborative editor (Google Docs)                                | Concurrent edits: differential sync / OT / CRDTs                | [Differential synchronization](https://neil.fraser.name/writing/sync/)                                                                               |
| Key-value store (Redis/Dynamo)                                    | Partitioning, replication, quorum, conflict resolution          | [Dynamo paper](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)                                                                |
| Distributed cache (Memcached)                                     | Consistent hashing, eviction, stampedes                         | [Scaling memcache at Facebook](https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170_update.pdf)                                     |
| Recommendations (Amazon)                                          | Offline batch models vs online serving                          | [Hulu recommendation system](https://web.archive.org/web/20170406065247/http://tech.hulu.com/blog/2011/09/19/recommendation-system.html)             |
| Chat (WhatsApp)                                                   | Long-lived connections, delivery receipts, offline queues       | [WhatsApp architecture](http://highscalability.com/blog/2014/2/26/the-whatsapp-architecture-facebook-bought-for-19-billion.html)                     |
| Photo sharing (Instagram)                                         | Object storage + CDN, feed fan-out, ID generation               | [Instagram architecture](http://highscalability.com/blog/2011/12/6/instagram-architecture-14-million-users-terabytes-of-photos.html)                 |
| News feed / timeline (Facebook)                                   | Fan-out on write vs read; denormalised timelines                | [Facebook timeline via denormalization](http://highscalability.com/blog/2012/1/23/facebook-timeline-brought-to-you-by-the-power-of-denormaliza.html) |
| Trending topics (Twitter)                                         | Sliding-window counts over a stream, top-k                      | [Real-time trending topics in Storm](http://www.michael-noll.com/blog/2013/01/18/implementing-real-time-trending-topics-in-storm/)                   |
| Unique ID generation                                              | Time-ordered, coordination-free 64-bit IDs                      | [Snowflake](https://github.com/twitter/snowflake/)                                                                                                   |
| Top-k requests in a time window                                   | Approximate counting (count-min sketch, space-saving)           | [Efficient computation of frequent elements](https://www.cs.ucsb.edu/sites/default/files/documents/2005-23.pdf)                                      |
| Serving from multiple datacenters                                 | Replication topology, consistency across regions                | [How Google serves data from multiple datacenters](http://highscalability.com/blog/2009/8/24/how-google-serves-data-from-multiple-datacenters.html)  |
| API rate limiter                                                  | Token bucket vs sliding window, distributed counters            | [Stripe: rate limiters](https://stripe.com/blog/rate-limiters) · [rate limiting lesson](../../production-review/references/rate-limiting.md)         |
| Stock exchange                                                    | Single-threaded matching engine, sequencing, determinism        | [Jane Street talk](https://youtu.be/b1e4t2k2KJY)                                                                                                     |
| Also: CDN, graph search, multiplayer card game, garbage collector |                                                                 | primer's [additional questions](https://github.com/donnemartin/system-design-primer#additional-system-design-interview-questions)                    |

## Foundational systems

The papers that later designs keep borrowing from:

| Area             | System → idea to take away                                                                                                                                                          |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Batch processing | [MapReduce](https://static.googleusercontent.com/media/research.google.com/en//archive/mapreduce-osdi04.pdf): move compute to data, re-execute failed tasks · Spark: in-memory DAGs |
| Streaming        | Storm, and Kafka as a replayable log: decouple producers from consumers in time                                                                                                     |
| Wide-column      | [Bigtable](https://static.googleusercontent.com/media/research.google.com/en//archive/bigtable-osdi06.pdf): sorted row keys, tablets, SSTables → HBase, Cassandra                   |
| Dynamo-style KV  | [Dynamo](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf): consistent hashing, sloppy quorum, vector clocks → DynamoDB, Cassandra                             |
| Global SQL       | [Spanner](https://research.google.com/archive/spanner-osdi2012.pdf): TrueTime-bounded clocks give external consistency                                                              |
| File systems     | [GFS](https://static.googleusercontent.com/media/research.google.com/en//archive/gfs-sosp2003.pdf) → HDFS: one metadata master, chunked replicated data                             |
| Coordination     | [Chubby](https://static.googleusercontent.com/media/research.google.com/en//archive/chubby-osdi06.pdf), ZooKeeper: a small consistent core for locks, leader election, config       |
| Tracing          | [Dapper](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/36356.pdf): propagate trace context across every hop, sample cheaply                       |
| Caching          | Memcached (shared-nothing LRU), Redis (data structures + persistence)                                                                                                               |

The "idea to take away" column is this repository's summary. The primer lists the systems.

## Company architectures

The primer collects writeups for Amazon, Dropbox, Facebook (memcache, TAO, photo storage, Live),
Flickr, Instagram, Netflix, Pinterest, Salesforce, Stack Overflow, TripAdvisor, Tumblr, Twitter
(timelines, MySQL at 250 M tweets/day, 3,000 images/s), Uber, WhatsApp and YouTube. Most are on
highscalability.com. The full linked table is in the primer's
[company architectures](https://github.com/donnemartin/system-design-primer#company-architectures)
section, and engineering blogs are indexed at
[kilimchoi/engineering-blogs](https://github.com/kilimchoi/engineering-blogs).

Before a design interview with a specific company, read its engineering blog. Its questions often
come from its own domain.

*Question list, systems and company list from
[donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0), appendix. The hard-part hints and take-away summaries are this repository's.*
