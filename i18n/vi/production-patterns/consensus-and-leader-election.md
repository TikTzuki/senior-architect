---
source_sha256: cdc2ffb3d9d7cd7cbe614a0fbea23a3ac3c6282d13a4fced34ff795f44b41e49
---

# Đồng thuận & Bầu leader

**Quy tắc: đồng thuận đòi hỏi đa số, không phải một điều phối viên.** Bất kỳ cơ chế nào để một node
đơn lẻ quyết định — hoặc để phía thiểu số vẫn hành động được — sớm muộn cũng sinh ra hai node cùng
chắc chắn rằng mình đang nắm quyền.

Thuộc nhóm hệ phân tán: [mạng & độ trễ](network-and-latency.md) ·
[timeout & retry](timeouts-and-retries.md) ·
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md) ·
**đồng thuận & bầu leader** (trang này) ·
[thiết kế cho sự cố](designing-for-failure.md) ·
[failover đa region](multi-region-failover.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Câu hỏi mà đồng thuận trả lời](#the-question-consensus-answers)
- [Quorum](#quorum)
- [Bầu leader thực sự diễn ra thế nào](#how-leader-election-actually-works)
- [Split brain](#split-brain)
- [Fencing](#fencing)
- [Leader thay đổi liên tục](#leader-churn)
- [Cái giá, và khi nào nên trả](#the-cost-and-when-to-pay-it)
- [Đừng tự xây cái này](#do-not-build-this)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# "Instance nào chiếm được cờ trước thì là leader."
if redis.set("leader", instance_id, nx=True, ex=30):
    is_leader = True
    start_scheduled_jobs()          # chỉ leader mới được chạy các job này

# Một thread nền làm mới key mỗi 10s để giữ vai trò leader.
```

Có ba lỗi nằm trong đoạn này:

- **TTL có thể hết hạn mà leader không hề biết** — một lần GC pause hoặc nghẽn mạng, và instance thứ
  hai trở thành leader một cách hợp lệ trong khi instance đầu vẫn tin mình là leader. Xem
  [distributed-locks](distributed-locks.md).
- **Một node Redis đơn lẻ là một nguồn sự thật duy nhất.** Failover của nó có thể làm mất hẳn key, và
  khi đó không ai là leader — hoặc có hai leader.
- **Không có gì ngăn leader cũ tiếp tục hành động.** Nó vẫn chạy job, vẫn ghi vào database, và vẫn
  tin rằng mình có thẩm quyền.

Kết quả là hai leader, cả hai đều đúng theo thông tin của riêng mình, và cả hai cùng ghi.

## Vì sao môi trường dev che giấu lỗi này

Chỉ có một instance thì nó luôn là leader, và logic bầu chọn không bao giờ được chạy thật. Không có
node thứ hai để cạnh tranh, không có phân vùng mạng, không có lần pause nào đủ dài để lease hết hạn.

Lỗi này cũng **hiếm và không xác định** trên production — nó cần một lần phân vùng mạng hoặc một lần
pause vào đúng thời điểm xấu — nên nó sống sót qua rất nhiều lần deploy trước khi lộ ra, và khi lộ ra
thì trông giống dữ liệu bị hỏng chứ không giống lỗi bầu chọn.

## Câu hỏi mà đồng thuận trả lời

Đồng thuận là việc các node thống nhất về một giá trị duy nhất — entry tiếp theo trong log, leader
hiện tại, danh sách thành viên của cluster — *bất chấp* sự cố và phân vùng mạng.

Yêu cầu khiến nó khó: sự thống nhất phải đúng ngay cả khi các node không phân biệt được "node kia đã
chết" với "tôi không liên lạc được với node kia". **Hai tình huống này trông y hệt nhau nhưng đòi hỏi
hai phản ứng ngược nhau**, và không có cách tinh chỉnh timeout khéo léo nào giải quyết được điều đó.

Vì vậy đồng thuận không cố đoán đó là trường hợp nào. Nó sắp đặt sao cho **chỉ một phía của phân vùng
mạng có thể hành động.**

## Quorum

Cơ chế: một quyết định cần **đa số** của một tập thành viên cố định.

```
5 nodes, quorum = 3

partition:  [A B C] | [D E]
            majority ──┘   └── minority: cannot form quorum, must not act
```

Vì sao phải là đa số: hai tập rời nhau không thể cùng chứa hơn một nửa của cùng một tập thành viên.
Chính tính chất duy nhất đó khiến split brain trở nên bất khả — không phải nhờ cảnh giác, mà nhờ số
học.

Những hệ quả đáng nhớ khi review:

- **Dùng số node lẻ.** 3 node chịu được 1 lỗi; 5 node chịu được 2. Bốn node cũng chỉ chịu được một lỗi
  như ba node, trong khi tốn hơn và dễ bị chia đôi đều nhau hơn.
- **Phía thiểu số phải từ chối phục vụ**, không phải chạy ở chế độ suy giảm. Một phía thiểu số vẫn
  tiếp tục nhận ghi chính là split brain mà ta đang ngăn chặn.
- **Một lần ghi quorum tốn một vòng round trip tới đa số.** Đó là mức sàn của độ trễ, và là lý do các
  hệ đồng thuận không được dùng cho đường dữ liệu lưu lượng cao.

## Bầu leader thực sự diễn ra thế nào

Trong Raft và các họ hàng của nó, đại khái:

1. Các node khởi đầu là follower, chờ heartbeat từ một leader.
2. Follower nào hết **election timeout** sẽ trở thành candidate cho một **term** mới (một con số tăng
   đơn điệu) và xin phiếu bầu.
3. Mỗi node chỉ bỏ một phiếu cho mỗi term. Candidate nào giành được **đa số** sẽ trở thành leader.
4. Leader gửi heartbeat. Node nào thấy một **term cao hơn** lập tức lui xuống.
5. Election timeout được **ngẫu nhiên hóa**, để các candidate không liên tục chia nhỏ phiếu bầu — cùng
   logic jitter như trong [timeouts-and-retries](timeouts-and-retries.md), và vì cùng một lý do.

Con số term là phần quan trọng: nó khiến quyền lãnh đạo có **thứ tự**. Một leader lỗi thời không bị
phát hiện bằng cách hỏi nó còn sống không; nó bị phát hiện vì term của nó đã cũ.

## Split brain

Hai node, mỗi node đều tin rằng mình nắm thẩm quyền, và cả hai cùng hành động. Trên thực tế nó xảy
ra khi:

- Quyền lãnh đạo được cấp bởi **một** node hoặc kho lưu trữ duy nhất, và failover của nó làm mất quyền
  đã cấp.
- Một lease hết hạn mà bên đang giữ không biết (vấn đề pause ở trên).
- Phía thiểu số của phân vùng mạng vẫn tiếp tục phục vụ.
- Ai đó "tốt bụng" promote một replica bằng tay trong lúc xử lý sự cố, trong khi bản gốc vẫn còn sống.

Thiệt hại không phải là downtime mà là **sự phân kỳ**: hai nơi có thẩm quyền cùng chấp nhận những lần
ghi mâu thuẫn nhau, và khi phân vùng được nối lại thì không có cách merge nào đúng. Bạn buộc phải chọn
bỏ đi một lịch sử.

Hãy để ý điều này tệ hơn nhiều so với việc không sẵn sàng. Từ chối ghi trong 30 giây thì phục hồi
được; chấp nhận hai tập ghi mâu thuẫn thì không. Sự bất đối xứng đó là lý do các hệ đồng thuận chọn
tính nhất quán thay vì tính sẵn sàng khi có phân vùng mạng — xem
[replication-and-sharding](replication-and-sharding.md) về CAP.

## Fencing

Đồng thuận quyết định *ai* là leader. Fencing ngăn một leader **cũ** tiếp tục hành động — và đó là phần
thường bị thiếu.

Leader nhận một token tăng đơn điệu (term, hoặc epoch), và **mọi tài nguyên được bảo vệ đều từ chối
những lần ghi mang token cũ**:

```
leader A, term 7 → paused
leader B, term 8 → writes with term 8 → accepted; resource records 8
A resumes        → writes with term 7 → REJECTED
```

Sự đảm bảo nằm ở **tài nguyên**, nơi duy nhất có thể ép buộc nó, vì tài nguyên là nơi nhìn thấy các
lần ghi. Không thể trông cậy vào việc một leader lỗi thời tự biết mình đã lỗi thời.

Trong thực tế: một cột version hoặc epoch được kiểm tra khi ghi, một row lock trong database, hoặc một
hệ lưu trữ hỗ trợ ghi có điều kiện. Không có fencing, bầu leader chỉ còn là *thường thì* có một leader —
đủ tốt cho một cron job, nhưng không đủ cho tiền.

## Leader thay đổi liên tục

Cơ chế phát hiện lỗi đằng sau một cuộc bầu chọn là một timeout, và timeout không phân biệt được leader
đã chết với leader chỉ đang chậm. Đặt nó chặt hơn những lúc xấu của hệ thống, và cluster sẽ phế truất
cả những leader khỏe mạnh:

- **Jitter mạng hoặc mất gói** làm vài heartbeat đến trễ quá election timeout.
- **Một lần stop-the-world pause** — GC, VM bị steal CPU, container bị throttle CPU — làm leader đứng
  hình đủ lâu để các follower bỏ cuộc.
- **Một ổ đĩa chậm.** Leader Raft phải ghi bền từng entry trước khi tính phiếu của chính nó, và mọi
  follower cũng vậy trước khi xác nhận. Trên storage dùng chung hoặc storage qua mạng, một lần `fsync`
  có thể mất hàng trăm mili giây, và các heartbeat xếp hàng phía sau sẽ trễ hạn.

Mỗi trường hợp trên sinh ra một cuộc bầu chọn không thay đổi gì ngoài danh tính của leader — và mỗi lần
như vậy đều tốn một khoảng ngừng ghi trong lúc nó diễn ra, các kết nối client bị cắt, và nếu dữ liệu
được replicate bất đồng bộ phía sau leader, có thể mất vài lần ghi cuối đã được xác nhận (xem
[replica placement & quorum](replica-placement-and-quorums.md)). Cluster trông khỏe mạnh trên mọi
dashboard chỉ lấy mẫu trạng thái thay vì đếm số lần chuyển đổi.

Các timeout là một ngân sách, và ngân sách đó phải bao phủ trường hợp xấu nhất còn được coi là bình
thường, không phải trường hợp trung bình:

```
etcd       heartbeat-interval ≈ round-trip time; election-timeout ≈ 10× that
           (defaults 100 ms / 1000 ms); WAL fsync p99 should stay under ~10 ms
Patroni    loop_wait + 2 × retry_timeout ≤ ttl — the defaults 10 + 2×10 = 30 sit exactly on
           the limit, so one slow DCS call during a loop can cost the primary its leader key
```

Hai chi phí liên quan. **Độ trễ ghi do thành viên chậm nhất mà quorum cần quyết định**, không phải
thành viên nhanh nhất: với ba node, leader chờ follower nhanh hơn trong hai follower, nên một node chậm
bị che đi còn hai node chậm thì không. Và **chính ổ đĩa của kho đồng thuận nằm trên đường đi quan trọng**
của mọi lần ghi — đó là lý do etcd yêu cầu SSD riêng, và vì sao một control plane Kubernetes trên
storage chậm biểu hiện thành timeout ở API server từ rất lâu trước khi có ai nhìn vào etcd.

Tín hiệu cần theo dõi là **tần suất leader thay đổi**, không phải việc có leader hay không. Một leader
tồn tại ở mọi lần scrape vẫn có thể thay đổi hai mươi lần một ngày. Hãy cảnh báo trên số lần thay đổi
(`etcd_server_leader_changes_seen_total`, lịch sử failover của Patroni, số lần đổi controller của
broker), và coi mức nền khác không là một lỗi cần tìm, không phải hệ thống đang chạy đúng.

## Cái giá, và khi nào nên trả

Đồng thuận rất đắt: một vòng round trip tới đa số cho mỗi quyết định, throughput bị giới hạn bởi
leader, và độ phức tạp vận hành khi thay đổi thành viên và khi giám sát.

**Hãy trả khi tính đúng đắn phụ thuộc vào một giá trị duy nhất được thống nhất:** danh sách thành viên
cluster, danh tính leader, cấu hình không được phép phân kỳ, dịch vụ distributed lock, và metadata của
các hệ thống sharding.

**Đừng trả cho** đường dữ liệu lưu lượng cao. Các hệ đồng thuận lưu *ai là người quyết định* và *cấu
hình là gì* — vài kilobyte trạng thái quan trọng — chứ không lưu bản ghi của ứng dụng.

Và phần lớn yêu cầu "chúng ta cần một leader" yếu hơn vẻ ngoài. Hãy hỏi **hiệu quả hay đúng đắn**:

| Nhu cầu                                 | Câu trả lời                                                             |
|-----------------------------------------|-------------------------------------------------------------------------|
| Một instance chạy job theo lịch          | Một dòng job được claim, hoặc một lease. Thỉnh thoảng chạy trùng là chấp nhận được. |
| Một consumer xử lý mỗi message          | `FOR UPDATE SKIP LOCKED`, hoặc cơ chế phân phối của chính broker         |
| Đúng một bên được ghi vào một bản ghi    | Một update có điều kiện hoặc cột version — không phải một leader        |
| Metadata của cluster không được phân kỳ | **Đồng thuận thật sự**                                                  |

## Đừng tự xây cái này

Các thuật toán đồng thuận nổi tiếng là dễ sai một cách tinh vi: đếm phiếu khi thay đổi thành viên, cắt
log, xử lý term, và các trường hợp pause đều là những chỗ mà một cài đặt trông hợp lý vẫn sai theo cách
mà test không phát hiện được.

Hãy dùng **etcd, ZooKeeper hoặc Consul**, hoặc một database đã có sẵn tính năng này. Nếu thấy logic bầu
chọn tự viết khi review, kết luận không phải là "hãy tinh chỉnh nó" — mà là "hãy thay nó bằng một hệ
thống mà tính đúng đắn đã được người khác chứng minh."

Và dù dùng hệ nào, hãy kiểm tra các câu hỏi vận hành: quorum có kích thước bao nhiêu, chuyện gì xảy ra
khi mất quorum, việc mất quorum có được cảnh báo không, và failover leader đã thực sự được test chưa?
Một failover chưa từng test chỉ là một giả định, giống hệt một bản backup chưa từng restore.

## Checklist review

- [ ] Không có thuật toán đồng thuận hay bầu chọn tự viết
- [ ] Quyền lãnh đạo được cấp bởi một hệ thống dựa trên quorum, không phải một node hay một key đơn lẻ
- [ ] Số thành viên là số lẻ; kích thước quorum được biết rõ
- [ ] Phía thiểu số của phân vùng mạng từ chối hành động thay vì chạy suy giảm
- [ ] Quyền lãnh đạo mang một term/epoch tăng đơn điệu
- [ ] Tài nguyên được bảo vệ từ chối những lần ghi mang token cũ (fencing)
- [ ] Lease không được coi là đảm bảo đúng đắn cho tiền hoặc tồn kho
- [ ] Mỗi yêu cầu "chỉ một leader" được phân loại là hiệu quả hay đúng đắn
- [ ] Việc mất quorum được giám sát và cảnh báo
- [ ] Số lần leader *thay đổi* được đếm và cảnh báo, không chỉ việc có leader
- [ ] Election timeout và lease timeout được suy ra từ p99 đo được của mạng, pause và ghi đĩa, có biên dự phòng
- [ ] Failover leader đã được test, không chỉ được cấu hình
- [ ] Đồng thuận lưu metadata, không phải đường dữ liệu lưu lượng cao
- [ ] Việc promote thủ công trong lúc xử lý sự cố có kiểm soát để không tạo ra leader thứ hai

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Distributed Systems phần P16 của
TechCraft ([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề "ai quyết định sự
thật tiếp theo" và kịch bản sự cố là của TechCraft, cũng như các ý trong Replication & Scaling P10 và
P18 ([collection](https://www.patreon.com/collection/2351128)) rằng mạng chập chờn gây bầu chọn lặp
lại, timeout phải được tinh chỉnh theo độ trễ p99, và một thành viên quorum chậm quyết định độ trễ ghi;
phép tính quorum, phần mô tả Raft, cách xử lý fencing, các con số ngân sách cho leader churn, và
checklist là của repository này.*
