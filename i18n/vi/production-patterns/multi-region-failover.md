---
source_sha256: 907848267875eed01b6031241b03a11daefe1df93c7a5395518a9402a17a5afe
---

# Failover đa region

**Quy tắc: region thứ hai chỉ là một kế hoạch ứng phó thảm họa nếu ba điều đã được quyết định trước
thảm họa — bạn chấp nhận mất bao nhiêu dữ liệu, bạn chấp nhận ngừng hoạt động bao lâu, và ai được phép
nhận ghi khi các region không nhìn thấy nhau.** Thiếu những câu trả lời đó, region thứ hai chỉ thêm một
cách mới để làm hỏng dữ liệu, chồng lên đúng sự cố mà nó được mua về để vượt qua.

Thuộc nhóm hệ phân tán: [mạng & độ trễ](network-and-latency.md) ·
[timeout & retry](timeouts-and-retries.md) ·
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md) ·
[đồng thuận & bầu leader](consensus-and-leader-election.md) ·
[thiết kế cho sự cố](designing-for-failure.md) · **failover đa region** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Các failure mode](#failure-modes)
- [Quyết định RPO và RTO theo từng loại dữ liệu](#decide-rpo-and-rto-per-data-class)
- [Active-passive: promote phải là một quyết định, không phải phản xạ](#active-passive-make-promotion-a-decision-not-a-reflex)
- [Active-active: cho mỗi bản ghi một region gốc](#active-active-give-every-record-a-home)
- [Chứng minh nó](#prove-it)
- [Checklist review](#review-checklist)

## Anti-pattern

```
region A (primary)  ──── async replication ────▶  region B (standby)
   app + db                                          app + db
         ▲                                                ▲
         └────── health check: "can I reach A?" ──────────┘
                 no → promote B, flip DNS
```

Từng ô trong sơ đồ đều hợp lý, và sơ đồ nói rằng "sống sót qua sự cố cấp region". Thứ nó thực sự mã hóa
là: B tự promote bất cứ khi nào *B* không nhìn thấy A — kể cả trường hợp A vẫn khỏe và chỉ có đường kết
nối giữa hai bên bị đứt — và B bắt đầu phục vụ từ một bản sao bị chậm hơn đúng bằng độ trễ replication
lúc đó, con số mà không ai ghi lại.

## Vì sao môi trường dev che giấu lỗi này

Môi trường dev chỉ có một region, staging có một, và load test thường cũng chỉ có một. Độ trễ
replication khác region trên một đường kết nối khỏe là dưới một giây, nên bản standby lúc nào trông cũng
cập nhật. Failover, nếu có được thực hành, thì là trong một buổi diễn tập có kế hoạch: primary được tắt
gọn gàng, replication được xả hết trước, và DNS được chuyển bởi một người đang theo dõi. Một sự cố
region thật thì chẳng giống thế chút nào — nó xảy ra đột ngột, đường kết nối thường suy giảm trước khi
đứt hẳn, và primary có thể vẫn đang chạy.

## Các failure mode

**1. Mọi bản sao đều nằm trong một region.** Ba replica trải trên ba zone thì sống sót khi mất một
zone; nhưng một sự cố control plane, mạng hay nguồn điện ở cấp region sẽ cuốn đi cả ba, và một quorum
bằng không thì không bầu được ai. Đếm failure domain ở đây cũng là bài toán y như trong
[replica placement](replica-placement-and-quorums.md#placement-count-failure-domains-not-copies),
chỉ là lên một cấp.

**2. Gọi backup là kế hoạch DR.** Một bản dump hằng đêm gửi sang region khác giới hạn lượng dữ liệu mất
ở mức một ngày, và thời gian hồi phục bằng thời gian restore vài terabyte — hàng giờ — *nếu* môi trường
được restore khởi động được. Điều bất ngờ thường gặp là nó không khởi động được: cấu hình mạng, secret,
IAM và DNS chưa bao giờ được dựng lại ở region thứ hai. Backup bảo vệ bạn khỏi
[việc xóa nhầm row](replication-and-sharding.md#replication-is-not-a-backup); nó không giữ cho việc kinh
doanh tiếp tục chạy.

**3. Failover sang một bản sao bị tụt lại.** Replication khác region trên thực tế là bất đồng bộ, và độ
trễ của nó không cố định: khi đường kết nối suy giảm — khúc dạo đầu quen thuộc của một sự cố region —
độ trễ tăng từ vài mili giây lên vài phút. Promote standby vào lúc đó thì những lần ghi mới nhất sẽ biến
mất. Trong một ví điện tử, đó là một lần nạp tiền mà ngân hàng đã trừ còn ví thì không còn hiển thị.
Những lần ghi đó cũng không mất hẳn: chúng nằm trong primary cũ, và quay lại dưới dạng xung đột khi nó
trở lại. Đây là
[mất dữ liệu khi failover](replication-and-sharding.md#failover-loses-data-by-design) với một cửa sổ
lớn hơn.

**4. Cả hai region cùng promote.** Nếu mỗi region tự mình quyết định rằng bên kia đã chết, thì một
đường kết nối bị cắt giữa hai region khỏe mạnh sẽ sinh ra hai primary, cả hai cùng nhận ghi từ người
dùng ở gần mình. Hai tiếng sau đường kết nối lành lại và có hai lịch sử với hàng chục nghìn row xung đột,
không thuật toán nào merge đúng được. Hai site không thể tạo thành đa số; quyết định phía nào được ghi
cần một lá phiếu thứ ba.

**5. Active-active trên dữ liệu có bất biến.** Hai region cùng nhận ghi cho một tài khoản sẽ cùng duyệt
một lệnh rút tiền trên cùng một số dư. Giải quyết chuyện đó sau bằng last-write-wins sẽ âm thầm bỏ đi
một lần ghi, và quyết định bỏ *lần nào* dựa trên timestamp đồng hồ thực vốn lệch nhau giữa các máy.
Counter, set và log chỉ-ghi-thêm thì merge được; số dư, tồn kho và các claim duy nhất thì không.

**6. Người dùng rơi sang region kia.** Cơ chế định tuyến chuyển người dùng giữa các region — vì tải, vì
một health check lỗi, vì đổi mạng di động. Họ đổi mật khẩu ở A, và B từ chối mật khẩu mới; họ thanh toán
ở A, và B hiển thị đơn hàng chưa thanh toán. Xem
[chuyển giao giữa các region](session-consistency.md#5-cross-region-handoff).

**7. Bản thân việc chuyển đổi chậm và nhiễu.** Failover dựa trên DNS phải chờ TTL, và các resolver cùng
client cache lâu hơn TTL sẽ tiếp tục gửi lưu lượng tới region đã chết trong nhiều phút. Một health check
không phân biệt được "region sập" với "một cú chập chờn trên chính đường đi của health check" sẽ
failover vì báo động giả — và mỗi lần failover, kể cả lần failback, lại là một cơ hội nữa cho failure
mode 3 và 4.

**8. Standby phụ thuộc vào chính region đã chết.** Standby kéo image từ một registry ở region primary,
đọc secret từ một vault ở đó, dùng một control plane hay identity service đặt ở đó, hoặc báo cáo trạng
thái qua nó. Nó không khởi động được, hoặc không ai ra lệnh được cho nó. Trong sự cố S3 năm 2017 ở
`us-east-1`, các service có server của riêng mình vẫn khỏe mạnh vẫn sập vì chúng phụ thuộc vào S3 ở
đúng region đó — trong số đó có cả trang trạng thái của AWS.

**9. Đồng thuận kéo dài qua nhiều region.** Một cluster Raft hoặc Patroni duy nhất trải qua nhiều
region sẽ chèn một round trip 100–300 ms vào mọi lần ghi và mọi heartbeat. Với timeout được tinh chỉnh
cho một datacenter, nó sẽ bầu chọn liên tục — xem
[leader thay đổi liên tục](consensus-and-leader-election.md#leader-churn).

## Quyết định RPO và RTO theo từng loại dữ liệu

**RPO** — được phép mất bao nhiêu dữ liệu đã xác nhận. **RTO** — service được phép ngừng bao lâu.
Đây là những con số kinh doanh, và chúng quyết định kiến trúc chứ không phải ngược lại:

| RPO / RTO | Cần gì |
|---|---|
| vài giờ / một ngày | Backup được restore ở region khác — đã test, với môi trường được dựng bằng script |
| vài phút / dưới một giờ | Replication bất đồng bộ tới một warm standby, promote bởi con người theo runbook |
| vài giây / vài phút | Replication bất đồng bộ tới một hot standby, promote bởi một quorum có site thứ ba |
| bằng không / vài phút | Ghi đồng bộ hoặc ghi quorum qua nhiều region — mọi lần ghi đều trả giá round trip giữa các region |

RPO = 0 giữa các region nghĩa là mỗi lần ghi phải chờ một region khác: hàng chục đến hàng trăm mili
giây, lần nào cũng vậy. Hãy dành nó cho dữ liệu thực sự cần, cùng kiểu quyết định theo từng loại dữ liệu
như với [CAP](replication-and-sharding.md#cap-concretely).

## Active-passive: promote phải là một quyết định, không phải phản xạ

- **Bên promote không phải là một trong hai region.** Hãy dùng một kho đồng thuận có thành viên ở một
  vị trí thứ ba, hoặc một con người. "Tôi không nhìn thấy primary" từ phía standby không phải bằng chứng
  rằng primary đã sập.
- **Fence primary cũ** trước hoặc trong lúc promote standby: thu hồi vai trò ghi của nó, cắt lưu lượng
  của nó, hoặc làm cho storage từ chối các lần ghi của nó — xem
  [fencing](consensus-and-leader-election.md#fencing). Hãy giả định nó vẫn đang chạy.
- **Đọc vị trí replication tại thời điểm promote** và ghi lại. Con số đó *chính là* lượng dữ liệu bị
  mất, và là điểm khởi đầu của việc đối soát.
- **Lên kế hoạch đối soát** cho những lần ghi bị kẹt lại trong primary cũ: đó là thao tác thật của khách
  hàng, cần được phát lại, bù trừ hoặc hoàn tiền, không phải vứt bỏ.
- **Failback một cách có chủ đích**, như một lần failover có kế hoạch thứ hai, sau khi primary cũ đã được
  dựng lại từ primary mới — không bao giờ để nó tự nhập lại với lịch sử đã phân kỳ của mình.

## Active-active: cho mỗi bản ghi một region gốc

Dạng active-active khả thi là **geo-partitioning**: mỗi người dùng, tài khoản hoặc tenant có một region
gốc, và chỉ region đó nhận ghi cho nó; các region khác phục vụ đọc và chuyển tiếp lệnh ghi. Khi đó một
region hỏng sẽ chuyển các *region gốc* mà nó sở hữu — tức là active-passive theo từng partition, với
cùng các quy tắc promote như trên. Dữ liệu thực sự merge được (lượt thích, số lượt xem, trạng thái
online) có thể ghi ở mọi nơi với một kiểu dữ liệu hội tụ; dữ liệu có bất biến thì không.

## Chứng minh nó

Hãy chạy sự cố thật, không phải một lần tắt gọn gàng: cắt mạng giữa các region trong khi cả hai đang
chạy, giết region primary giữa lúc có lưu lượng, làm suy giảm đường kết nối trước khi cắt hẳn. Đo RPO
thực tế (độ trễ replication lúc promote) và RTO (lần ghi thành công đầu tiên sau đó). Một kế hoạch
failover mới chỉ được chạy dưới dạng chuyển đổi có kế hoạch là đã được test cho đúng cái trường hợp
không bao giờ xảy ra.

## Checklist review

- [ ] RPO và RTO được ghi lại cho từng loại dữ liệu, và chế độ replication khớp với từng loại
- [ ] Replica trải qua nhiều region ở những chỗ RTO đòi hỏi — chỉ nhiều zone thì chưa phải kế hoạch cấp region
- [ ] Việc promote cấp region do một site thứ ba hoặc con người quyết định, không bao giờ do riêng một region
- [ ] Primary cũ bị fence khi promote, với giả định rằng nó vẫn đang chạy
- [ ] Độ trễ replication được giám sát, và giá trị của nó lúc promote được ghi lại như lượng dữ liệu bị mất
- [ ] Các lần ghi bị kẹt trong primary cũ có quy trình đối soát
- [ ] Không bản ghi nào mang bất biến (số dư, tồn kho, tính duy nhất) được phép ghi ở hai region
- [ ] Định tuyến khác region có câu trả lời cho read-after-write và monotonic read
- [ ] Các dependency của region standby — registry, secret, identity, DNS, control plane — không nằm ở region primary
- [ ] Cluster đồng thuận không trải qua nhiều region với timeout dành cho một datacenter
- [ ] Failover và failback đã được thực hành như sự cố không báo trước, có đo RPO và RTO

---

*Bản dịch tiếng Việt của bài học cùng tên. Được gợi ý từ Replication & Scaling P11 (Active-Passive vs
Active-Active) và P20 (Multi-Region Design) của TechCraft
([collection](https://www.patreon.com/collection/2351128)). Các bài viết cung cấp lập luận về một
region duy nhất và AZ so với region, RPO/RTO như một cách đặt vấn đề kinh doanh, câu chuyện hồi phục chỉ
dựa vào backup, lần failover giữa lúc độ trễ tăng vọt khiến ví hiển thị trống, split brain giữa hai
region, ví dụ định tuyến khi đổi mật khẩu, DNS TTL và health check báo động giả, bầu chọn liên tục khi
trải qua nhiều region, và sự cố S3 năm 2017. Các quy tắc promote (trọng tài ở site thứ ba, fencing, ghi
lại độ trễ, đối soát các lần ghi bị kẹt, failback có chủ đích), region gốc theo geo-partitioning,
failure mode standby phụ thuộc region primary, và cấu trúc review được viết từ thực tiễn đã được kiểm
chứng, không phải từ các bài viết.*
