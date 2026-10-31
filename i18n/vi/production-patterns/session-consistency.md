---
source_sha256: 3c25839f3a2af74c39bf8d7ebd1446b01c603a1b23ac43d022267e04c1e39ffc
---

# Session consistency: Read-your-writes, Monotonic reads & thứ tự nhân quả

**Quy tắc: "eventually consistent" không phải là một lời hứa bạn đưa được cho người dùng.** Mỗi luồng
đọc phải cam kết một trong ba đảm bảo theo từng client, và mặc định — định tuyến lượt đọc tới replica
bất kỳ — chẳng cam kết cái nào cả.

Thuộc nhóm storage: [storage engine](storage-engines.md) ·
[index & query plan](indexes-and-query-plans.md) ·
[replication & sharding](replication-and-sharding.md) · [bố trí replica & quorum](replica-placement-and-quorums.md) · **session consistency** (trang này) ·
[connection pool & độ trễ](connection-pools-and-latency.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Ba đảm bảo, ba triệu chứng khác nhau](#three-guarantees-three-different-symptoms)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Nó hỏng thế nào trên production](#how-it-fails-in-production)
- [Pattern đúng](#the-correct-pattern)
- [Mỗi cơ chế tốn gì](#what-each-mechanism-costs)
- [Checklist review](#review-checklist)

## Anti-pattern

```
writes  ──> primary
reads   ──> load balancer ──> replica A | replica B | replica C   (round robin)
```

> "Đọc thì sang replica. Dữ liệu eventually consistent, vậy là ổn — chẳng ai bận tâm vài trăm
> mili giây."

Có người bận tâm đấy, và với người vừa bấm Lưu thì nó chẳng bao giờ là "vài trăm mili giây". Ba lỗi
riêng biệt nấp sau đúng một câu đó, và sửa một lỗi không sửa được các lỗi còn lại.

Dấu hiệu trong một diff: một lượt đọc được định tuyến tới một pool replica mà **không có liên hệ gì với
lần ghi ngay trước nó** — không version token, không ghim session, không ép về primary. Code đúng.
Database đúng. Người dùng thì bị nói dối.

## Ba đảm bảo, ba triệu chứng khác nhau

Đây không phải các mức độ của cùng một thứ. Chúng là những lời hứa khác nhau với phạm vi khác nhau, và
thỏa mãn một đảm bảo nghe có vẻ mạnh hơn không kéo theo các đảm bảo kia.

| Đảm bảo                               | Lời hứa                                              | Triệu chứng khi thiếu                     | Phạm vi                             |
|-----------------------------------------|----------------------------------------------------------|------------------------------------------|-----------------------------------|
| **Read-after-write** (read-your-writes) | Nếu *bạn* ghi, *bạn* sẽ thấy                          | "Tôi lưu rồi mà không thấy đâu"          | một client, một lần ghi             |
| **Monotonic reads**                     | Đã thấy phiên bản N thì không bao giờ thấy bản cũ hơn N | "Con số bị lùi lại"              | một client, theo thời gian và qua các thiết bị |
| **Causal consistency**                  | Nếu A gây ra B, không ai thấy B mà không thấy A                   | "Câu trả lời hiện ra trước câu hỏi" | giữa các client                    |

Cái bẫy cần thấm: **read-after-write không kéo theo monotonic reads.** Ghim mọi lượt đọc sau khi ghi về
primary mà người dùng vẫn thấy thời gian chạy lùi — họ ghi trên điện thoại, rồi mở laptop, và lượt đọc
từ laptop rơi vào một replica đang lag. Cùng một người, cùng một tài khoản, hai session, hai vị trí khác
nhau trên dòng thời gian.

Và **monotonic reads không kéo theo causal consistency.** Bạn có thể đảm bảo một người dùng không bao giờ
thấy dữ liệu thụt lùi mà vẫn cho một người thứ ba thấy bình luận trước bài viết mà nó trả lời. Monotonic
reads cam kết với riêng *bạn*; causal consistency thì cam kết về *mối liên hệ giữa người này với người
kia*.

## Vì sao môi trường dev che giấu lỗi này

Mọi cơ chế sinh ra các bug này đều vắng mặt ở local.

- **Replication lag bằng không** vì chỉ có một node. Lần ghi và lần đọc chạm cùng một file dữ liệu.
- **Không có load balancer**, nên không có chuyện các lượt đọc liên tiếp rơi vào những replica ở các vị
  trí khác nhau.
- **Một trình duyệt, một tab.** Trường hợp đa thiết bị làm vỡ monotonic reads cần hai client, và không
  ai test theo cách đó.
- **Test seed dữ liệu rồi mới đọc.** Hiếm khi test ghi rồi đọc lại bên trong cửa sổ lag, vì ở local làm
  gì có cửa sổ nào.
- **Staging chỉ có một replica**, nên "đọc từ replica" được test với một node duy nhất, thường là rảnh
  rỗi, lag tính bằng micro giây.

Vì thế bug xuất hiện lần đầu trên production, ở luồng đọc, trong khi dashboard vẫn xanh — không 5xx,
không query chậm, không lỗi nào trong log. Database không sai. Chỉ có góc nhìn của người dùng về nó là
sai.

## Nó hỏng thế nào trên production

### 1. Lần ghi tưởng như chưa xảy ra

Một người dùng cập nhật địa chỉ giao hàng cho một đơn $2,000 và nhận được "Cập nhật thành công". Họ
refresh để kiểm tra; lượt đọc rơi vào một replica đang lag và hiện địa chỉ cũ.

Thiệt hại không nằm ở lần hiển thị dữ liệu cũ — mà ở việc con người làm gì tiếp theo. Họ cho rằng lưu
thất bại và **gửi lại lần nữa**. Giờ có hai luồng ghi chạy cho cùng một ý định: hai event đổi địa chỉ,
hai lần tính lại phí ship, hai email thông báo. Một chốt chặn gửi trùng dựa trên một record chưa kịp
replicate sẽ không bắt được, vì bước kiểm tra cũng đọc từ replica.

Đây là một race condition mà một trong các bên tham gia là con người. Logic idempotency đọc từ replica
không phải là logic idempotency — xem
[trạng thái thanh toán & idempotency](payment-state-and-idempotency.md).

### 2. Thời gian chạy lùi

Số dư ví hiện 100M sau khi nạp tiền, 50M ở lần refresh kế tiếp, rồi lại 100M. Dữ liệu không có gì sai;
các lượt đọc liên tiếp rơi vào những replica ở vị trí khác nhau, vì load balancer chạy round-robin và
các replica lag không đều nhau.

Chỗ chuyện này không còn là vấn đề hiển thị: bất kỳ màn hình nào người dùng **dựa vào để hành động**.
Một mức giá trông như vừa giảm vì một replica cũ phục vụ nó sẽ mời gọi một lệnh giao dịch theo một mức
giá không còn tồn tại. Lệnh đó sau đó bị từ chối, hoặc được khớp ở chỗ người dùng không hề muốn. Đó là
vấn đề tài chính và tuân thủ khoác áo một bug giao diện.

### 3. Kết quả đến trước nguyên nhân

Alice đăng bài; Bob bình luận vào bài đó. Một người dùng thứ ba thấy bình luận của Bob mà không có bài
viết nào phía trên. Cả hai row đều đã commit; cả hai đều đã replicate. Chúng chỉ đơn giản đến theo thứ
tự khác nhau, vì chúng đi theo những con đường độc lập — các replica khác nhau với lag khác nhau, hoặc
các Kafka partition khác nhau được tiêu thụ bởi các service chịu tải khác nhau.

Cùng hình dạng đó, nhưng với tiền: một push notification "thanh toán thành công" tới trước khi bút toán
trong sổ cái đọc được, nên người dùng mở app và thấy tiền dường như đã mất mà không có giao dịch nào giải
thích. Xem [ngữ nghĩa phân phối message](message-delivery-semantics.md) để hiểu vì sao thứ tự theo từng
partition không phải là thứ tự toàn cục.

### 4. Failover tua ngược

Replication bất đồng bộ nghĩa là replica được promote có thể thiếu những lần ghi cuối cùng mà primary cũ
đã xác nhận. Khi failover, **mọi** client cùng lúc nhảy lùi — không phải chỉ một session kém may mắn.
Định tuyến sticky không giúp được gì; chính dòng thời gian đã dịch chuyển.

Đây là failure mode khiến "chúng tôi đã ghim người dùng vào một replica" không đủ làm câu chuyện
consistency. Hãy lượng hóa nó bằng RPO và đối chiếu với những gì phía nghiệp vụ nghĩ là đã bền vững —
[replication & sharding](replication-and-sharding.md) bàn về phía độ bền.

### 5. Chuyển giao giữa các region

Việc ghim session gần như luôn chỉ có phạm vi trong một region. Một người dùng di chuyển giữa các region
— đi công tác, dùng VPN, DNS phân giải lại, mạng di động chuyển trạm — sẽ tới một tập replica có vị trí
hoàn toàn khác trong luồng thay đổi, và có thể thấy trạng thái từ trước cả vài lần ghi gần nhất của họ.
Bất kỳ cơ chế sticky nào cũng cần một câu trả lời tường minh cho trường hợp đích sticky không tới được
hoặc nằm ở region khác, và "định tuyến họ đi đâu cũng được" về bản chất đã vi phạm monotonic reads.

### 6. Thêm replica làm mọi thứ tệ hơn

Bản năng khi primary bị tải nặng là thêm read replica. Mỗi replica là thêm một đích mà primary phải gửi
luồng thay đổi tới. Tới một lúc nào đó chính fan-out trở thành nút thắt, lag tăng trên toàn bộ, và
**thay đổi nhằm cải thiện hiệu năng lại nới rộng cửa sổ bất nhất.** Scale việc đọc và giữ consistency
kéo về hai phía ngược nhau; một kế hoạch chỉ nhắc tới vế đầu là chưa đầy đủ.

## Pattern đúng

### Bước 1 — phân loại mọi luồng đọc

Không phải lượt đọc nào cũng cần cùng một đảm bảo, và giả vờ rằng chúng cần chính là điều khiến người ta
với tới strong consistency rồi bỏ cuộc vì chi phí.

| Luồng đọc                                           | Cần              |
|-----------------------------------------------------|--------------------|
| Người dùng đọc dữ liệu họ sở hữu hoặc vừa thay đổi        | read-after-write   |
| Bất kỳ màn hình nào người dùng theo dõi hoặc refresh liên tục   | monotonic reads    |
| Bất cứ chỗ nào một record giải thích một record khác          | causal consistency |
| Số liệu tổng hợp, feed dữ liệu của người khác, analytics | eventual là đủ   |

### Bước 2 — chọn đảm bảo yếu nhất mà vẫn giữ được invariant

Strong consistency ở mọi nơi là cách sửa ngây thơ: mọi lần ghi đều chờ một quorum xuyên region, nên hệ
thống chạy với tốc độ của node chậm nhất và dừng hẳn khi có partition. Dùng two-phase commit toàn cục
để sắp thứ tự cũng là sai lầm ấy, chỉ thêm nhiều bước. Yêu cầu thật không phải là sắp xếp tất cả — mà
là giữ đúng trình tự cho những gì **có liên hệ với nhau**.

### Bước 3 — dùng version token, không dùng stickiness

Ba cơ chế đem lại read-after-write và monotonic reads. Hãy ưu tiên cái thứ ba.

**Đọc từ primary cho các lượt đọc của chủ sở hữu.** Đơn giản và đúng. Nhưng nếu mọi người dùng xem
profile của chính mình đều chạm primary, bạn đã trả lại toàn bộ lợi ích của replica. Chấp nhận được như
một quy tắc hẹp (cài đặt tài khoản, trạng thái KYC), không phải như một chiến lược.

**Sticky session.** Hash `user_id` về một replica cố định ở load balancer. Rẻ, và nó thực sự đem lại
monotonic reads — dòng thời gian của một replica đơn lẻ chỉ tiến về phía trước. Nhưng nó mang tính xác
suất: đảm bảo bốc hơi khi node đó chết, nó tạo ra replica nóng khi những người dùng nặng bị hash về cùng
chỗ, và rebalancing mà vẫn giữ được stickiness thực sự khó.

**Version token (khuyến nghị).** Làm cho vị trí trở nên tường minh thay vì ngầm định qua định tuyến:

1. Một lần ghi thành công trả về vị trí của primary — một LSN, một commit timestamp, một token không
   trong suốt.
2. Ứng dụng lưu nó gắn với session (cookie, session store, response header).
3. Các lượt đọc tiếp theo gửi kèm token.
4. Một proxy hoặc middleware so sánh nó với vị trí đã áp dụng của replica ứng viên, rồi hoặc **chờ** một
   chút để replica bắt kịp, hoặc **định tuyến lại** sang một replica đã bắt kịp, hoặc quay về primary.

Cách này chỉ đọc từ primary khi thực sự cần, sống sót được khi một replica chết, và không tạo điểm nóng.
Đó là thứ các tầng "session consistency" của cloud cài đặt sẵn cho bạn.

### Bước 4 — với thứ tự nhân quả, theo dõi phụ thuộc, không dùng đồng hồ thực

Đồng hồ của các server lệch nhau, nên timestamp không thể xác lập quan hệ "xảy ra trước". Thay vào đó
hãy mang theo phụ thuộc: bình luận ghi lại id và phiên bản của bài viết mà nó trả lời. Ở phía đọc, nếu
kết quả tới mà nguyên nhân chưa nhìn thấy được, hãy **buffer kết quả** cho tới khi nguyên nhân tới thay
vì hiển thị một bản ghi mồ côi. Lamport clock hoặc vector clock khái quát hóa cách này khi phụ thuộc
không phải là một cha duy nhất hiển nhiên.

### Bước 5 — hạ cấp một cách công khai thay vì nói dối

Khi hệ thống phát hiện nó không thể giữ được đảm bảo — token của client đi trước mọi replica tới được —
hãy thể hiện điều đó. Một trạng thái "đang đồng bộ…" là cái giá UX nhỏ. Một banner thành công giả kéo
theo dữ liệu cũ thì tốn niềm tin, và kích động lần gửi lại biến một vấn đề thành hai.

Liên quan: đừng báo thành công cho công việc chưa trở nên nhìn thấy được. Hãy mô hình hóa nó thành một
trạng thái mà client có thể poll — [thao tác bất đồng bộ & chạy lâu](api-async-operations.md).

## Mỗi cơ chế tốn gì

| Cơ chế            | Mua được                                            | Cái giá                                                                       |
|----------------------|-------------------------------------------------|-----------------------------------------------------------------------------|
| Đọc từ primary    | read-after-write                                | mất lợi ích của replica trên luồng đó                                           |
| Sticky replica       | monotonic reads, với chi phí thấp                        | replica nóng; mất đảm bảo khi node hỏng hoặc đổi region               |
| Version token + chờ | read-after-write và monotonic reads, chính xác | tail latency trong lúc chờ; một proxy phải biết vị trí của các replica        |
| Causal metadata      | kết quả không bao giờ đi trước nguyên nhân                     | lưu metadata tăng theo độ sâu phụ thuộc; logic buffer; dọn tỉa |

Không cái nào sống sót qua failover mà không tốn gì. Cái nào cũng cần một câu trả lời tường minh cho
"chúng ta làm gì khi đích đã ghim hoặc đã bắt kịp không còn nữa".

## Checklist review

- [ ] Với mỗi luồng đọc bị động tới: nó cần đảm bảo nào trong ba, và cái gì thực thi nó? "Eventually
  consistent" không phải câu trả lời cho lượt đọc của chủ sở hữu.
- [ ] Một lượt đọc ngay sau lần ghi trong cùng một thao tác của người dùng có đi tới primary, mang theo
  version token, hay chẳng làm gì cả?
- [ ] Có bước kiểm tra gửi trùng hay idempotency nào **đọc từ replica** không? Nó có thể bỏ sót chính
  lần ghi đi trước nó bên trong cửa sổ lag.
- [ ] Định tuyến đọc có chạy round-robin qua một pool replica cho một màn hình người dùng hay refresh
  không? Đó là một vi phạm monotonic reads chỉ chờ lag không đều xảy ra.
- [ ] Client có giữ một version/session token xuyên thiết bị không, hay tính đơn điệu chỉ được giả định
  dựa vào định tuyến?
- [ ] Ở chỗ một record giải thích một record khác (comment→post, notification→ledger, refund→charge),
  phụ thuộc có được ghi lại và kết quả có được buffer cho tới khi nguyên nhân nhìn thấy được không?
- [ ] Luồng notification và luồng dữ liệu có độc lập với nhau không? Một push có thể vượt read model thì
  sẽ vượt.
- [ ] RPO đã tuyên bố là bao nhiêu, và việc failover tua ngược có phá một đảm bảo đã hứa ở chỗ khác trong
  sản phẩm không?
- [ ] Nếu dùng định tuyến sticky: chuyện gì xảy ra khi replica đó không tới được, và cái gì ngăn những
  người dùng nặng bị hash dồn vào một node?
- [ ] Thay đổi có thêm replica không? Nếu có, ảnh hưởng lên fan-out và lag đã được cân nhắc chưa, chứ
  không chỉ phần throughput đọc thu được?
- [ ] Khi không đáp ứng được đảm bảo, UI có nói ra không, hay nó hiện dữ liệu cũ dưới một thông báo thành
  công?

---

*Bản dịch tiếng Việt của bài học cùng tên. Chủ đề được gợi ý từ series Replication & Scaling (P1–P7)
trên [TechCraft](https://www.patreon.com/c/TechCraft). Series đặt khung cho ba đảm bảo và cung cấp các
kịch bản sự cố — trường hợp ảnh profile và địa chỉ, số dư chạy lùi, thứ tự bình luận, failover tua ngược
và nghịch lý fan-out của replica. Cấu trúc review, cách phân loại luồng đọc và checklist được viết từ
thực tiễn đã được kiểm chứng, không chép lại từ series.*
