# Back-of-the-Envelope Estimation

**Rule: estimate to choose between designs, not to predict the future.** You only need to be
right within an order of magnitude. That's enough to tell one box from a cluster, RAM from disk,
or sync from async.

## Contents

- [The conversions you use every time](#the-conversions-you-use-every-time)
- [A worked estimate](#a-worked-estimate)
- [Powers of two](#powers-of-two)
- [Latency numbers](#latency-numbers)
- [Availability in nines](#availability-in-nines)

## The conversions you use every time

| Conversion        | Value                                                              |
|-------------------|--------------------------------------------------------------------|
| Seconds per month | ~2.5 million                                                       |
| 1 request/s       | ~2.5 M requests/month                                              |
| 40 requests/s     | ~100 M requests/month                                              |
| 400 requests/s    | ~1 B requests/month                                                |
| Seconds per day   | ~86,400 ≈ 10^5                                                     |
| Peak vs average   | assume 2–3× average unless told otherwise (typical, not universal) |

## A worked estimate

The shape every case study follows, here for a paste service. The figures are the primer's
assumptions:

```
10 M writes/month            → 10M / 2.5M s      ≈ 4 writes/s
100 M reads/month (10:1)     → 100M / 2.5M s     ≈ 40 reads/s
avg paste ~1 KB + metadata   → ~1.27 KB × 10M    ≈ 12.7 GB/month
retain 3 years               → 12.7 GB × 36      ≈ ~450 GB, 360 M rows
```

What the numbers decide here: 4 writes/s fits on one relational primary; 40 reads/s is
trivial, but the 10:1 ratio says cache and replicas pay off first; 450 GB says the index fits
on one machine and the blobs belong in an object store.

Estimate in this order: **requests/s → bytes per request → storage per period × retention →
bandwidth (requests/s × bytes) → working set** (what fraction is hot enough to keep in memory).

## Powers of two

| Power | Exact value       | Approx     | Bytes |
|-------|-------------------|------------|-------|
| 2^10  | 1,024             | 1 thousand | 1 KB  |
| 2^16  | 65,536            |            | 64 KB |
| 2^20  | 1,048,576         | 1 million  | 1 MB  |
| 2^30  | 1,073,741,824     | 1 billion  | 1 GB  |
| 2^32  | 4,294,967,296     |            | 4 GB  |
| 2^40  | 1,099,511,627,776 | 1 trillion | 1 TB  |

Use 2^32 to check ID space: a signed 32-bit `INT` tops out at ~2.1 B and an unsigned one at
~4.3 B. Any table that might pass that needs `BIGINT`.

## Latency numbers

These are the classic figures (Jeff Dean / Peter Norvig). The absolute values have shifted with
hardware, especially NVMe SSDs and 10–100 Gbps networks. **The ratios between rows are still the
part to memorise.**

| Operation                          | Latency | Relative             |
|------------------------------------|---------|----------------------|
| L1 cache reference                 | 0.5 ns  |                      |
| Branch mispredict                  | 5 ns    |                      |
| L2 cache reference                 | 7 ns    | 14× L1               |
| Mutex lock/unlock                  | 25 ns   |                      |
| Main memory reference              | 100 ns  | 200× L1              |
| Compress 1 KB (Snappy)             | 10 µs   |                      |
| Send 1 KB over 1 Gbps              | 10 µs   |                      |
| Read 4 KB randomly from SSD        | 150 µs  |                      |
| Read 1 MB sequentially from memory | 250 µs  |                      |
| Round trip within a datacenter     | 500 µs  |                      |
| Read 1 MB sequentially from SSD    | 1 ms    | 4× memory            |
| HDD seek                           | 10 ms   | 20× DC round trip    |
| Read 1 MB over 1 Gbps network      | 10 ms   | 40× memory, 10× SSD  |
| Read 1 MB sequentially from HDD    | 30 ms   | 120× memory, 30× SSD |
| Packet CA → Netherlands → CA       | 150 ms  |                      |

Derived throughputs to reason with:

- Sequential read: HDD ~30 MB/s · 1 Gbps network ~100 MB/s · SSD ~1 GB/s · memory ~4 GB/s
- Round trips per second: ~2,000 inside a datacenter, ~6–7 worldwide

Takeaways that drive design decisions:

- **Memory is far faster than disk, and disk seeks are the worst.** Keep the working set in RAM;
  read sequentially.
- **A cross-continent round trip costs ~300 datacenter round trips.** Chatty protocols across
  regions are the bottleneck. See the
  [network & latency lesson](../../production-review/references/network-and-latency.md).
- **Compress before sending** over anything slower than memory.

## Availability in nines

| Availability  | Per year      | Per month   | Per week   | Per day    |
|---------------|---------------|-------------|------------|------------|
| 99.9% (three) | 8 h 45 m 57 s | 43 m 49.7 s | 10 m 4.8 s | 1 m 26.4 s |
| 99.99% (four) | 52 m 35.7 s   | 4 m 23 s    | 1 m 5 s    | 8.6 s      |

Composition:

```
In sequence:  A_total = A_foo × A_bar                 99.9% × 99.9%        = 99.8%
In parallel:  A_total = 1 − (1 − A_foo)(1 − A_bar)    two 99.9% in parallel = 99.9999%
```

Every synchronous dependency in a request path multiplies down your availability, and redundancy
multiplies it back up. The parallel formula assumes the failures are independent. Shared power,
a shared deploy or a shared config makes them correlated, and then the gain mostly vanishes.
That caveat is this repository's; see
[designing for failure](../../production-review/references/designing-for-failure.md).

*Tables and conversions from
[donnemartin/system-design-primer](https://github.com/donnemartin/system-design-primer)
(CC BY 4.0), appendix and solution estimates. The "what the numbers decide" commentary, ID-space
note and correlated-failure caveat are this repository's.*
