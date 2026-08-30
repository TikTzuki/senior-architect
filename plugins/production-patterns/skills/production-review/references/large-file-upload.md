# Large File Upload

**Rule: user files must never stream through the application server.** The client uploads
directly to object storage; the backend only authorizes the upload and reacts to its completion.

## Contents

- [The anti-pattern](#the-anti-pattern)
- [Why development hides it](#why-development-hides-it)
- [How it fails in production](#how-it-fails-in-production)
- [The pattern: direct upload](#the-pattern-direct-upload)
- [Multipart, for genuinely large files](#multipart-for-genuinely-large-files)
- [Making it survive retries](#making-it-survive-retries)
- [Security conditions on the grant](#security-conditions-on-the-grant)
- [Downloads have the same problem](#downloads-have-the-same-problem)
- [Review checklist](#review-checklist)

## The anti-pattern

```
Client  ──upload 2GB video──▶  App server  ──write──▶  Object storage
                               (holds the whole
                                request in RAM/disk)
```

The file crosses the application process. Every byte occupies a request handler, a connection,
and either memory or scratch disk on a machine whose real job is serving requests.

Signals in code: a multipart form handler that binds an entire file to memory or a temp path,
then re-uploads it to storage. `MultipartFile` bound to a controller argument, `multer` disk
storage followed by an SDK `putObject`, `request.files[...]` read into a buffer, a handler that
does `read()` then `upload()`.

## Why development hides it

One developer, one upload at a time, a loopback network, a fast local disk, and no proxy in
front. Under those conditions the flow is correct — it just isn't load-bearing. Nothing in the
local environment applies the pressure that breaks it.

## How it fails in production

- **Worker starvation.** Each in-flight upload pins a worker or connection for the duration of
  the transfer. Uploads are slow because client uplinks are slow; a handful of users on poor
  connections can saturate the pool and stall unrelated requests. This is usually the first
  symptom, and it looks like a mysterious latency spike on endpoints that have nothing to do
  with uploads.
- **Memory and disk exhaustion.** Buffering strategies that are fine for a 2 MB avatar fall over
  on a 2 GB video, especially when several arrive at once. Containers get OOM-killed; scratch
  disks fill and stay full when cleanup is skipped on the error path.
- **Proxy and gateway timeouts.** Load balancers and reverse proxies cap request duration and
  body size — commonly 60 s and 1–100 MB by default. A slow upload is killed mid-flight and the
  client sees a 504 with no partial progress preserved.
- **Retries multiply the load.** A client that retries a failed 2 GB upload sends 2 GB again.
  Retry storms during an incident turn a degraded service into a saturated one.
- **Out-of-order and concurrent arrival.** Chunked schemes that assume sequential delivery
  corrupt files when parts arrive reordered, and two concurrent uploads to the same key
  interleave into a file that is neither.
- **No resumability.** A failure at 95% restarts at 0%, which on a mobile connection means the
  upload may never complete at all.

## The pattern: direct upload

```
1. Client ──"I want to upload"──▶ Backend        authorize, record intent (status: pending)
2. Backend ──presigned URL──────▶ Client          short-lived, tightly scoped grant
3. Client ──────PUT bytes───────▶ Object storage  never touches the app server
4. Storage ──event──▶ Queue ──▶ Worker            validate, then mark ready
```

**Step 1 — authorize and record intent.** The backend checks the caller may upload, generates
the object key itself (never accepts one from the client), and writes a row in `pending` state.
That row is the thing the rest of the system references; the file does not exist yet.

**Step 2 — issue a scoped grant.** Return a presigned URL valid for minutes, not hours,
constrained as described under [security conditions](#security-conditions-on-the-grant).

**Step 3 — client uploads directly.** Bytes go to object storage. The app server is not in the
path and its worker pool is untouched.

**Step 4 — react to completion.** Two options, and the choice matters:

- *Storage event → queue → worker* (`s3:ObjectCreated:*` → SQS/SNS, or a bucket notification).
  Authoritative: it fires because the object genuinely exists. Prefer this.
- *Client calls back* (`POST /uploads/{id}/complete`). Simpler, but a client that crashes or
  goes offline after uploading leaves the record `pending` forever. If you rely on it, add a
  reconciliation job that lists storage and repairs orphaned records.

The worker validates what the client could lie about — real size, real content type sniffed from
magic bytes rather than the declared header, virus scan, dimension or duration limits — then
flips the record to `ready`. Only then is the file visible to the rest of the product.

## Multipart, for genuinely large files

Above roughly 100 MB, a single PUT is a poor unit of work. Multipart upload splits the object
into parts that upload in parallel and retry individually.

- Part size 5–100 MB. Smaller means more requests and more overhead; larger means a retry costs
  more. 8–16 MB is a reasonable default.
- Parts upload concurrently and out of order by design — storage assembles them by part number,
  so reordering is not a correctness concern the way it is in a hand-rolled chunking scheme.
- A failed part retries alone. This is what makes large uploads survive flaky mobile networks.
- **Set a lifecycle rule to abort incomplete multipart uploads** (7 days is typical). Without it,
  abandoned parts accumulate as storage you pay for and cannot see in a normal object listing.
  This is a genuinely common and invisible source of cost.

Presign each part URL, or issue scoped credentials and let a client SDK drive the transfer.

## Making it survive retries

Retries are normal, not exceptional. Design so a duplicate is harmless:

- **Deterministic keys.** Derive the object key from the upload record's ID, so retrying writes
  the same key rather than creating a second object.
- **Idempotency key on the completion call.** A repeated completion must be a no-op returning the
  same result, not a second processing run — otherwise one upload produces two transcode jobs and
  two charges.
- **Guard the state transition.** Move `pending → ready` with a conditional update, so two
  concurrent completions cannot both proceed.
- **Never trust client-reported success alone.** Confirm the object exists and its size matches
  before marking anything ready.

## Security conditions on the grant

A presigned URL is a bearer credential. Anyone holding it has exactly the access it encodes, so
encode as little as possible:

- **Short TTL** — minutes. Long-lived URLs leak through logs, referrer headers, and screenshots.
- **Pin content length** with a size range condition. Without it, a grant for a 5 MB avatar
  accepts a 5 GB file and your storage bill is the vulnerability.
- **Pin content type**, and still sniff the real type server-side afterward. The declared type is
  a client assertion.
- **Server-generated keys, namespaced per user** (`uploads/{user_id}/{uuid}`). Accepting a
  client-supplied key is a path-traversal and overwrite primitive — a client that names its key
  `uploads/other-user/avatar.png` has just overwritten someone else's file.
- **Never echo the client filename into the key.** Sanitize it into metadata if you need it for
  the download filename.
- **Block public bucket access.** Serve reads through presigned GETs or a CDN with signed URLs.

## Downloads have the same problem

Streaming a file back through the application server has the same failure profile in reverse —
a worker pinned for the length of a slow download. Redirect to a presigned GET, or put a CDN in
front with signed URLs. The app server decides *whether* the caller may read the file; it should
not be the thing that transfers it.

## Review checklist

- [ ] File bytes never pass through an application request handler
- [ ] Upload grant is short-lived and scoped: size range, content type, server-generated key
- [ ] Object key is namespaced per user and never derived from client input
- [ ] Multipart used above ~100 MB, with an abort-incomplete lifecycle rule configured
- [ ] Completion is driven by a storage event, or by a client callback plus a reconciliation job
- [ ] Completion handling is idempotent and the state transition is guarded against races
- [ ] Real content type is sniffed server-side; declared type is not trusted
- [ ] Downloads go via presigned URL or CDN, not through the app server
- [ ] Proxy/gateway body-size and timeout limits are consistent with the flow's actual needs
- [ ] Temp files are cleaned up on the error path, not only on success

---

*Topic prompted by a [TechCraft post](https://www.facebook.com/techcraft.official) on large file
uploads in production. The post states the problem and names direct-to-object-storage as the answer;
the detail here is written from established practice, not transcribed from it. The Database Internals
and Data Modeling Patterns series since obtained do not cover upload architecture, so this lesson
remains independent work.*
