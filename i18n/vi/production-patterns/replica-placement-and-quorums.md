---
source_sha256: 8f0827060418b232be5f39cf6533087c16a8b3cc5690b3c6dfd0f9dc6ac8df1c
---

# Bố trí replica & Quorum

**Quy tắc: độ bền dữ liệu không phải là số bản sao bạn đã cấu hình — mà là số bản sao phải xác nhận
một lần ghi, chỉ tính trên những failure domain không hỏng cùng lúc với nhau.** Replication factor 3
mà chỉ cần một xác nhận, hoặc cả ba bản sao nằm chung một rack, thực chất là replication factor 1 với
chi phí gấp ba.

Thuộc nhóm storage: [storage engine](storage-engines.md) ·
[index & query plan](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) ·
**bố trí replica & quorum** (trang này) · [session consistency](session-consistency.md) ·
[connection pool & độ trễ](connection-pools-and-latency.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Ba con số, không phải một](#three-numbers-not-one)
- [Các failure mode](#failure-modes)
- [W + R > N, và những gì nó không hứa](#w--r--n-and-what-it-does-not-promise)
- [Bố trí: đếm failure domain, không đếm bản sao](#placement-count-failure-domains-not-copies)
- [Chọn các con số](#choosing-the-numbers)
- [Checklist review](#review-checklist)

## Anti-pattern

Một cấu hình đọc lên nghe như "ba bản sao, an toàn":

```properties
# Kafka topic
replication.factor=3
min.insync.replicas=1        # broker default
# producer
acks=1                       # "faster"
```

```java
// Cassandra keyspace RF=3, application code
session.execute(debit_stmt.setConsistencyLevel(ONE))   // "lowest latency"
```

```yaml
# Kubernetes StatefulSet, replicas: 3, no affinity rules
replicas: 3
```

Mỗi mảnh đều là giá trị mặc định hoặc một tùy chọn có trong tài liệu. Ghép lại, chúng có nghĩa là:
một lần ghi được báo thành công ngay khi **một** bản sao nhận được nó, và ba bản sao có thể nằm chung
một node, một rack hoặc một zone. Hệ thống mang chi phí của RF=3 nhưng chỉ chịu lỗi được như RF=1.

## Vì sao môi trường dev che giấu lỗi này

Ở local chỉ có một node, hoặc ba container trên cùng một laptop, và không có gì hỏng độc lập với
nhau. Mọi lần ghi đều tới mọi bản sao trong vòng một mili giây, nên `ONE` và `QUORUM` trả về kết quả
giống hệt nhau và `acks=1` không làm mất gì. Benchmark còn thưởng cho thiết lập yếu: càng ít xác nhận
thì độ trễ càng thấp và throughput càng cao, nên chính cấu hình làm mất dữ liệu lại thắng trong bài
load test.

Sự khác biệt chỉ lộ ra khi một bản sao **bị tụt lại hoặc biến mất** đúng lúc quan trọng — một node
chết giữa lúc đã xác nhận và lúc replicate, một zone hỏng cả khối, một replica đang lag đúng lúc có
lần đọc rơi vào nó. Không điều nào trong số đó xảy ra trong bộ test, trừ khi có người cố ý gây ra.

## Ba con số, không phải một

| Con số | Câu hỏi nó trả lời | Tên thường gặp |
|---|---|---|
| **N** — số bản sao | Rốt cuộc có bao nhiêu bản sao tồn tại? | `replication.factor`, RF của keyspace, số replica |
| **W** — số xác nhận ghi | Bao nhiêu bản sao phải giữ lần ghi trước khi client được báo "xong"? | `acks` + `min.insync.replicas`, consistency level khi ghi, `synchronous_standby_names` |
| **R** — số bên tham gia khi đọc | Bao nhiêu bản sao được hỏi trước khi một lần đọc trả về? | consistency level khi đọc; 1 với mọi lần đọc từ một replica |

N là con số mọi người cấu hình và nói tới. **W quyết định độ bền** — một lần ghi đã xác nhận sống sót
qua việc mất tối đa `W − 1` bản sao. **W và R cùng nhau quyết định lần đọc có mới hay không.** Một lần
review chỉ kiểm tra N là đã kiểm tra con số ít quan trọng nhất.

## Các failure mode

**1. Xác nhận trên một bản sao.** Với `W = 1`, client được báo lần ghi thành công trong khi nó chỉ tồn
tại trên một máy. Nếu máy đó chết trước khi replicate, failover sẽ promote một bản sao chưa từng thấy
lần ghi đó. Với một lần nạp tiền, đó là khách hàng đã bị ngân hàng trừ tiền nhưng ví vẫn hiện số dư
cũ. Không có gì "bị hỏng"; hệ thống làm đúng những gì `W = 1` cho phép.

**2. `acks=all` mà thực ra là một.** Trong Kafka, `acks=all` chờ mọi replica *đang đồng bộ*. Khi các
follower tụt lại, tập in-sync co lại — có thể chỉ còn mỗi leader — và lúc đó `acks=all` được thỏa mãn
bởi một bản sao. Chỉ `min.insync.replicas=2` (với RF=3) mới khiến broker từ chối những lần ghi mà nó
không thể đặt lên hai bản sao. Mặc định của broker là 1, nên `acks=all` với cấu hình mặc định âm thầm
tụt thành `acks=1` đúng vào lúc cluster đang có vấn đề.

**3. RF=2 không có biên dự phòng.** Sau khi mất một bản sao, mỗi byte chỉ còn tồn tại một lần cho đến
khi re-replication xong — với hàng terabyte ở tốc độ vài trăm MB/s là hàng giờ, trong lúc bản còn lại
gánh cả tải của chính nó lẫn lượng đọc phục vụ việc dựng lại. Một sự cố thứ hai trong khoảng thời gian
đó là mất dữ liệu. Và với quorum đa số, RF=2 cần cả hai bản sao: chỉ một sự cố là dừng hẳn việc ghi
quorum. RF=2 mua chi phí của replication mà không có khả năng chịu lỗi; phép tính giống hệt
[cái bẫy số node chẵn](consensus-and-leader-election.md#quorum).

**4. Các bản sao hỏng cùng nhau.** Ba replica được lập lịch lên cùng một node Kubernetes, một rack sau
cùng một top-of-rack switch, hoặc cùng một availability zone. Một sự cố duy nhất xóa sạch mọi bản sao
cùng lúc. Đây là kết quả mặc định khi không có quy tắc bố trí tường minh — scheduler tối ưu theo tài
nguyên, không phải theo việc trải đều replica của bạn.

**5. Sự cố có kế hoạch cộng dồn lên sự cố bất ngờ.** Một đợt nâng cấp node cuốn chiếu drain một replica
trong khi một replica khác vẫn đang dựng lại sau vụ hỏng đĩa hôm qua. Hai trên ba bản sao ngừng hoạt
động mà không ai "gặp sự cố" cả. `PodDisruptionBudget` và một bước kiểm tra replication khỏe mạnh trước
mỗi lần drain là thứ ngăn chuyện này.

**6. Các bản sao sao chép trung thành cả dữ liệu hỏng.** Replication chép bất cứ thứ gì primary đã ghi,
kể cả một lần ghi sai từ controller lỗi hay từ một bug. Càng nhiều bản sao thì lan càng nhanh. Page
checksum phát hiện được nó ở tầng storage (PostgreSQL chỉ bật mặc định từ bản 18), và chỉ có
[backup](replication-and-sharding.md#replication-is-not-a-backup) mới khôi phục được.

## W + R > N, và những gì nó không hứa

Trong các store không có leader (Cassandra, kiểu DynamoDB, Riak), nếu tập ghi và tập đọc buộc phải
giao nhau — `W + R > N` — thì mọi lần đọc đều chạm ít nhất một bản sao có lần ghi đã xác nhận mới nhất,
và phiên bản mới nhất thắng. Với N=3, ghi `QUORUM` và đọc `QUORUM` (2 + 2 > 3) giao nhau; `ONE`/`ONE`
(1 + 1 ≤ 3) thì không, và một lần đọc có thể trả về giá trị cũ ngay sau một lần ghi thành công.

Sự giao nhau đó yếu hơn vẻ ngoài. Nó **không** cho bạn:

- **Thứ tự của các lần ghi đồng thời.** "Mới nhất" thường được quyết định bằng timestamp — last write
  wins — nên clock skew giữa các bên ghi có thể khiến lần ghi sau bị thua. Hai lần trừ tiền đồng thời
  không được quorum sắp xếp tuần tự; việc đó cần một lần ghi có điều kiện hoặc một
  [lock](distributed-locks.md).
- **Rollback cho những lần ghi thất bại.** Một lần ghi tới được một bản sao nhưng chưa đủ W sẽ bị báo
  là thất bại và **không** bị hoàn tác. Những lần đọc sau có thể thấy nó hoặc không, tùy chúng chạm vào
  bản sao nào.
- **Đảm bảo khi có sloppy quorum.** Nếu store chấp nhận ghi lên các node thay thế trong lúc sự cố
  (hinted handoff), W node đã ghi có thể không nằm trong N node mà một lần đọc hỏi tới.

Vậy nên đọc và ghi quorum làm cho việc đọc phải dữ liệu cũ khó xảy ra; chúng không biến một store không
có leader thành một sổ cái. Với yêu cầu "số dư không bao giờ được sai", hãy dùng một store có một leader
hoặc dựa trên đồng thuận cùng một
[update có điều kiện](transaction-atomicity-and-durability.md), chứ không phải một consistency level.

Level này thường được đặt theo từng request, và đó là điểm hữu ích: `QUORUM` cho luồng thanh toán,
`ONE` cho số lượt xem, trong cùng một keyspace. Ở cluster nhiều datacenter, `LOCAL_QUORUM` giữ độ trễ
trong cùng region với cái giá là dữ liệu giữa các region kém mới hơn; xem
[session consistency](session-consistency.md).

## Bố trí: đếm failure domain, không đếm bản sao

Với mỗi bản sao, hãy hỏi "sự kiện đơn lẻ nào xóa bản này cùng với một bản khác?" và trải chúng ra cho
đến khi câu trả lời là "không có gì khả dĩ":

```
host         → podAntiAffinity (required, not preferred) on kubernetes.io/hostname
zone         → topologySpreadConstraints on topology.kubernetes.io/zone
rack         → rack-aware placement: Cassandra NetworkTopologyStrategy + snitch, Kafka broker.rack
region       → a different problem: latency and failover, not just placement
```

Anti-affinity dạng `preferredDuringScheduling` chỉ là một gợi ý mà scheduler bỏ qua khi cluster chật —
đúng lúc nó quan trọng nhất. Trên một cluster có ít node hơn số replica, anti-affinity bắt buộc sẽ để
một replica ở trạng thái `Pending`, và đó là kết quả trung thực: bạn không có ba failure domain, và cấu
hình nên nói rõ điều đó thay vì giả vờ.

## Chọn các con số

| Dữ liệu | N | W | Ghi chú |
|---|---|---|---|
| Tiền, đơn hàng, bất cứ thứ gì dùng để ra quyết định | 3 (hoặc 5) trải qua các zone | đa số (2 trên 3) | `min.insync.replicas=2`, `acks=all`; đọc từ leader hoặc dùng `R` với `W + R > N` |
| Dữ liệu ứng dụng thông thường | 3 trải qua các zone | đa số | mức mặc định đáng bảo vệ |
| Log, metric, cache, projection dựng lại được | 1–2 | 1 | mất một phần là chấp nhận được theo định nghĩa — hãy ghi rõ thành văn bản |

Ý nghĩa của bảng là mỗi dòng là một quyết định tường minh với mức chịu mất dữ liệu được nói rõ — cùng
loại quyết định theo từng loại dữ liệu như [CAP](replication-and-sharding.md#cap-concretely). Một
thiết lập chung cho cả cluster gần như luôn quá yếu với một số dữ liệu hoặc quá đắt với phần còn lại.

## Checklist review

- [ ] Với mỗi store có replication, biết rõ N, W và R — không chỉ N
- [ ] Dữ liệu dùng để ra quyết định (số dư, trạng thái thanh toán) được ghi với W đa số, không bao giờ `ONE` / `acks=1`
- [ ] Các Kafka topic mang sự kiện nghiệp vụ đặt `min.insync.replicas` ≥ 2 với RF=3 và producer dùng `acks=all`
- [ ] Không store production nào dựa vào RF=2 cho độ bền
- [ ] Những lần đọc phải thấy lần ghi mới nhất hoặc đi tới leader, hoặc thỏa `W + R > N`
- [ ] Các update đồng thời lên cùng một key được tuần tự hóa bằng ghi có điều kiện, không phải bằng quorum + timestamp
- [ ] Replica được trải ra bằng anti-affinity bắt buộc / topology spread / rack awareness, đã kiểm chứng trên các pod đang chạy
- [ ] Có `PodDisruptionBudget` để các lần drain có kế hoạch không gỡ quá một replica
- [ ] Biết thời gian re-replication sau khi mất một node, và đã tính đến sự cố thứ hai trong khoảng đó
- [ ] Page hoặc block checksum được bật, và backup tồn tại độc lập với replication

---

*Bản dịch tiếng Việt của bài học cùng tên. Lấy cảm hứng từ series Replication & Scaling phần P14
(Replication Factor) và P15 (Quorum) của TechCraft
([collection](https://www.patreon.com/collection/2351128)). Các bài viết cung cấp sự đánh đổi giữa
RF=1/2/3, các câu chuyện sự cố về bản sao chung rack và dữ liệu hỏng bị replicate, lập luận giao nhau
`W + R > N`, và lần nạp tiền biến mất khi failover với `W = 1`. Phần suy giảm `min.insync.replicas`
của Kafka, giới hạn của sự giao nhau quorum (ghi đồng thời, ghi dở không được rollback, sloppy quorum),
các quy tắc bố trí trên Kubernetes, trường hợp sự cố có kế hoạch cộng dồn sự cố bất ngờ, và cấu trúc
review được viết từ thực tiễn đã được kiểm chứng, không lấy từ các bài viết.*
