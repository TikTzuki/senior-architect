# Edge, Routing & Service Communication

**Rule: every box between the client and the data is a single point of failure until it's
doubled, and a source of latency until it's measured.** Add one only when a number calls for it.

## Contents

- [DNS](#dns)
- [CDN](#cdn)
- [Load balancer](#load-balancer)
- [Reverse proxy](#reverse-proxy)
- [Web tier vs application tier](#web-tier-vs-application-tier)
- [Protocols: HTTP, TCP, UDP](#protocols-http-tcp-udp)
- [RPC vs REST](#rpc-vs-rest)
- [Security baseline](#security-baseline)

## DNS

Translates names to IPs through a hierarchy, starting from a few authoritative servers at the
top. Browsers, operating systems and resolvers cache each answer for its **TTL**, so a change
spreads only as old caches expire.

| Record  | Points to                                                 |
|---------|-----------------------------------------------------------|
| `A`     | A name → an IP                                            |
| `CNAME` | A name → another name (`example.com` → `www.example.com`) |
| `NS`    | The DNS servers for a domain/subdomain                    |
| `MX`    | Mail servers for the domain                               |

Managed DNS (Route 53, Cloudflare) can also **route**. Weighted round robin covers maintenance
drains, uneven cluster sizes and A/B tests. Latency-based and geolocation-based routing send
users to the nearest region.

Costs: a lookup adds latency (caching hides most of it). DNS is a DDoS target (Dyn, 2016). And
**a low TTL is a cost too**: more lookups, and some clients ignore TTLs anyway. Don't rely on
DNS for fast fail-over.

## CDN

A global network of proxies that serves content from near the user. It takes load off your
origin and cuts latency. Mostly used for static assets; some CDNs (CloudFront) also serve dynamic
content.

|                | **Push CDN**                                      | **Pull CDN**                                                                   |
|----------------|---------------------------------------------------|--------------------------------------------------------------------------------|
| How            | You upload on change and rewrite URLs to the CDN  | The CDN fetches from your origin on the first request, then caches for the TTL |
| Storage        | High: everything sits on the CDN                  | Low: only what's been requested recently                                       |
| Origin traffic | Minimal: content is uploaded only when it changes | Re-fetches on expiry, even when nothing changed                                |
| First request  | Fast                                              | Slow (cache miss)                                                              |
| Fits           | Low traffic, or content that rarely changes       | Heavy traffic, spread evenly                                                   |

Costs: CDN bills scale with traffic. Content stays stale until the TTL expires (version your
asset URLs so a deploy doesn't have to wait on a purge). URLs need rewriting.

## Load balancer

Spreads requests across a pool of servers. It keeps traffic away from unhealthy servers, stops
any one from being overloaded, and removes the single server as a point of failure. Hardware
appliances are expensive; software (HAProxy, NGINX, Envoy, cloud LBs) is the norm.

| Layer  | Decides on                                  | Mechanism                                                              | Trade-off                                                                                                    |
|--------|---------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| **L4** | Source/destination IP and port, not content | Forwards packets with NAT                                              | Cheaper and faster; no content routing                                                                       |
| **L7** | Headers, path, cookies, body                | Terminates the connection, reads the request, opens a new one upstream | Route `/video` to media servers and billing to hardened ones; costs more CPU (negligible on modern hardware) |

- **Algorithms:** random, round robin / weighted, least loaded, session/cookie affinity.
- **Extras:** TLS termination (certificates live in one place) and session persistence.
- **Don't let the LB become the SPOF it removes.** Run a pair in active-passive or active-active.

**Horizontal scaling** (many commodity machines behind an LB) beats vertical scaling on cost
and availability. It has two prerequisites:

1. **Stateless app servers.** No sessions, uploads or profile pictures on local disk. Keep
   sessions in a shared store (Redis, a database).
2. **The downstream can take the fan-in.** Twenty app servers × a pool of 50 = 1,000 database
   connections. See [connection pools](../../production-review/references/connection-pools-and-latency.md).

Deep dive on probes, draining and in-process state:
[health checks & load balancing](../../production-review/references/health-checks-and-load-balancing.md).

## Reverse proxy

A web server that fronts your internal services and gives the public one interface.

What it buys: it hides backend topology, blocks IPs and caps connections per client; clients
only ever see one IP, so the backends can change freely; it terminates TLS, compresses
responses, caches responses and serves static files directly.

**LB vs reverse proxy:** an LB earns its place once you have several servers doing the same job.
A reverse proxy is useful even in front of a single server. NGINX and HAProxy do both. Cost:
complexity, and it's a SPOF unless doubled.

## Web tier vs application tier

Separate the **web layer** (HTTP, TLS, static content) from the **application/platform layer**
(business logic) so each can scale and be configured on its own. A new API means more app
servers, not more web servers. Application-layer workers also enable asynchronous processing.

**Microservices:** independently deployable, small services, each in its own process, talking
over a lightweight interface, each serving one business capability. Pinterest's example is
user profile, follower, feed, search and photo upload. **Service discovery** (Consul, etcd,
ZooKeeper) keeps a registry of names, addresses and ports, gated by health checks, and often
doubles as a config key-value store.

Cost: a different architecture, operating model and process from a monolith, and every
in-process call becomes a network call that can fail. Split along consistency boundaries,
not org charts. See [consistency boundaries](../../production-review/references/consistency-boundaries.md)
and [network & latency](../../production-review/references/network-and-latency.md).

## Protocols: HTTP, TCP, UDP

**HTTP** is a request/response application protocol over TCP. HTTP/3 runs over QUIC, which is
UDP; that note is this repository's. It's self-contained, so intermediaries can load-balance,
cache, encrypt and compress it.

| Verb     | Use                           | Idempotent | Safe | Cacheable                |
|----------|-------------------------------|------------|------|--------------------------|
| `GET`    | Read                          | Yes        | Yes  | Yes                      |
| `POST`   | Create, or trigger processing | **No**     | No   | Only with freshness info |
| `PUT`    | Create or replace             | Yes        | No   | No                       |
| `PATCH`  | Partial update                | **No**     | No   | Only with freshness info |
| `DELETE` | Delete                        | Yes        | No   | No                       |

Idempotency decides what is safe to retry. A retried `POST` creates duplicates unless you add
idempotency keys. See [timeouts & retries](../../production-review/references/timeouts-and-retries.md)
and [payment state & idempotency](../../production-review/references/payment-state-and-idempotency.md).

|              | **TCP**                                                                                         | **UDP**                                                                                      |
|--------------|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Model        | Connection (handshake), ordered, checksummed, acked, retransmitted; flow and congestion control | Connectionless datagrams; may arrive out of order or not at all; no congestion control       |
| Cost         | Handshake and retransmit delay; memory per open connection                                      | You build any reliability you need                                                           |
| Choose when  | All data must arrive intact; you want automatic best use of throughput                          | Lowest latency; late data is worse than lost data; custom error correction; broadcast (DHCP) |
| Typical uses | Web, databases, SMTP, FTP, SSH                                                                  | VoIP, video chat, streaming, real-time games                                                 |

Lots of open TCP connections from web threads to something like memcached gets expensive. Pool
them, or use UDP where losing a packet is acceptable. Facebook's memcache uses UDP for gets.

## RPC vs REST

|              | **RPC** (gRPC/Protobuf, Thrift, Avro)                                                        | **REST**                                                                                                                                                                                                            |
|--------------|----------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Exposes      | **Behaviours**: `POST /resign {personid}`                                                    | **Resources**: `DELETE /persons/1234`                                                                                                                                                                               |
| Coupling     | Client is tightly bound to the server's procedures                                           | Loose: uniform URIs, verbs, status codes; stateless                                                                                                                                                                 |
| Strengths    | Performance; native calls hand-fitted to the use case; you control access and error handling | Generic, cacheable by standard infrastructure, scales out and partitions easily                                                                                                                                     |
| Weaknesses   | A new endpoint per operation; harder to debug; standard HTTP caches don't understand it      | Awkward for things that aren't resource-shaped ("archive expired docs", "records changed in the last hour matching X"); nested views need several round trips (bad on mobile); responses bloat as fields accumulate |
| Typical home | Internal service-to-service                                                                  | Public APIs                                                                                                                                                                                                         |

The four REST qualities: a resource has one URI whatever the operation; you change it through
representations (verbs, headers, body); errors are self-descriptive status codes; and HATEOAS
(the API is navigable by links). The over-fetching and round-trip weaknesses are the gap that
GraphQL and BFF layers fill (this repository's note). Deep dives:
[resource modelling](../../production-review/references/api-resource-modeling.md) ·
[API contracts](../../production-review/references/api-contracts.md) ·
[list endpoints](../../production-review/references/api-list-endpoints.md).

## Security baseline

The primer only covers the minimum, and so does this file:

- Encrypt in transit and at rest
- Sanitise every user-controlled input against XSS and injection; use **parameterised queries**
  for SQL
- **Least privilege** for every service account, token and role

Abuse control and quotas are in [rate limiting](../../production-review/references/rate-limiting.md).
For anything deeper, start from the OWASP Top Ten.

*Compressed from [donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0): DNS, CDN, load balancer, reverse proxy, application layer, communication and
security sections. The fan-in arithmetic, DNS fail-over caveat, asset versioning, HTTP/3, memcache-over-UDP, GraphQL
and idempotency-key notes and the cross-links are this repository's.*
