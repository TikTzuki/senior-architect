---
source_sha256: 6fe0f4de1b7b8a2353311b520c977a66c3aba5896670d9e0464b3e3fb5537eed
---

# Replay event & Rebuild projection

**Quy tắc: replay là tính lại trạng thái; nó không bao giờ được thực thi lại quá khứ.** Rebuild một
projection là một cuộc migration dữ liệu online chạy giữa traffic thật — không side effect, tất
định, dựng bên cạnh bản đang chạy, kiểm chứng trước khi cut over, và được ước lượng theo RTO.

Thuộc nhóm data modeling: [lưu giữ & lịch sử](data-retention-and-history.md) ·
[dữ liệu thời gian](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
**replay event & rebuild projection** (trang này) ·
[tiến hóa schema event](event-schema-evolution.md) ·
[ngữ nghĩa phân phối message](message-delivery-semantics.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Replay không phải là thực thi lại](#replay-is-not-re-execution)
- [Apply phải tất định](#apply-must-be-deterministic)
- [Checkpoint không bao giờ được nhảy cóc](#a-checkpoint-must-never-skip)
- [Không bao giờ rebuild tại chỗ](#never-rebuild-in-place)
- [Ước lượng rebuild theo RTO](#size-the-rebuild-against-the-rto)
- [Sau khi restore, đừng tin thứ bạn nạp vào](#after-a-restore-distrust-what-you-load)
- [Sai sự việc hay sai projection?](#wrong-fact-or-wrong-projection)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
class RenewalProjector:
    def on_subscription_renewed(self, e):
        db.upsert("subscriptions", e.sub_id, status="ACTIVE", renewed_at=datetime.now())
        email.send(e.customer, "Your subscription was renewed")     # side effect
        if fx.rate("EUR", "USD") > 1.1: ...                          # tra cứu trực tiếp

# runbook khi projection có bug: sửa code, rồi
#   TRUNCATE subscriptions; reset checkpoint to 0; restart projector
```

Khi chạy thường ngày, mỗi event đi qua handler này đúng một lần và kết quả trông ổn. Lần đầu tiên
có người làm theo runbook, email gia hạn của hai năm được gửi đi trong một buổi chiều, `renewed_at`
thành ngày chạy rebuild, nhánh FX lấy tỷ giá hôm nay cho event của năm ngoái, và màn hình mà đội
vận hành dựa vào trống trơn cho tới khi replay xong.

## Vì sao môi trường dev che giấu lỗi này

Môi trường test có vài trăm event, được ghi ngay trước khi đọc: replay xong tức thì, đồng hồ gần như
không nhích, tỷ giá bên ngoài không đổi, và client gửi email chỉ là stub. Không ai rebuild gì cho
tới khi một bug production buộc phải làm — vì vậy đường rebuild là đoạn code ít được chạy nhất
trong hệ thống, được chạy lần đầu dưới áp lực sự cố, trên hàng tỷ event và giữa lúc vẫn có ghi mới.

## Replay không phải là thực thi lại

Một event ghi nhận rằng một việc đã xảy ra. Đọc lại nó không phải là yêu cầu làm lại việc đó.

**Tách chuyển trạng thái khỏi hành động ra bên ngoài.** Projector chỉ suy ra trạng thái. Mọi thứ
chạm tới thế giới bên ngoài — email, SMS, webhook, lời gọi thanh toán, yêu cầu giao hàng — được dẫn
dắt bởi một bản ghi ý định tường minh (một dòng outbox với business key ổn định) và được thực thi bởi
một worker riêng không bao giờ tham gia replay. Replay trạng thái; không bao giờ replay outbox.

**Một cờ không phải là một hàng rào.** `if replay_mode: return` chỉ bảo vệ những đường mà ai đó nhớ
chặn, và handler tiếp theo viết thiếu nó sẽ gây ra hiệu ứng thật. Hãy làm cho an toàn khi replay
mang tính cấu trúc:

- Bản build của replay worker không chứa client email, thanh toán hay webhook.
- Nó chạy không có credential outbound của production, nên một lời gọi lạc sẽ fail ở bước xác thực.
- Đường egress của nó tới endpoint bên thứ ba bị chặn ở tầng mạng.
- Một metric đếm số lời gọi ra ngoài trong lúc replay sẽ dừng job ngay ở giá trị khác không đầu
  tiên.

**Quyết định từ bên ngoài là sự việc.** Nếu một phép kiểm tra gian lận, điểm rủi ro hay báo giá FX
đã ảnh hưởng tới những gì xảy ra, hãy lưu kết quả của nó vào một event
(`RiskApproved{decision_id, policy_version}`). Một lần replay gọi lại service sẽ nhận câu trả lời
của hôm nay, và có thể biến một lần duyệt trong quá khứ thành từ chối. Hỏi xem model *hôm nay* sẽ
quyết định thế nào là mô phỏng, không phải replay.

## Apply phải tất định

`state = fold(apply, initial, events)` chỉ đúng qua nhiều năm nếu `apply` không phụ thuộc vào gì
ngoài trạng thái và event:

- **Không đọc đồng hồ.** Dùng timestamp mà event mang theo. `now()` làm kết quả phụ thuộc vào thời
  điểm chạy rebuild.
- **Không sinh ID.** ID ngẫu nhiên hoặc sinh từ sequence sẽ khác nhau mỗi lần chạy; hãy suy ra
  chúng từ event.
- **Không tra cứu.** Không gọi mạng, không đọc từ service khác hay bảng tham chiếu có thể thay đổi.

**Chính sách hôm nay không được viết lại lịch sử.** Phí đổi từ 1% lên 1.2% là một quy tắc mới kể
từ ngày hiệu lực, không phải một lần sửa lỗi. Projector áp quy tắc hiện tại cho mọi event sẽ tính
lại phí của năm ngoái ở lần rebuild tới. Hãy mang version quy tắc hoặc ngày hiệu lực trong event và
áp quy tắc đang có hiệu lực cho từng event. Chỉ một bug triển khai thực sự — code chưa bao giờ khớp
với quy tắc đang áp dụng — mới được sửa bằng cách replay với logic đã sửa. Xem
[dữ liệu thời gian](temporal-data.md#valid-time-modelling-when-a-fact-applied) cho cùng cách phân
tách này.

## Checkpoint không bao giờ được nhảy cóc

Một projector đang chờ vị trí 4103 mà nhận được 4104 là đã phát hiện ra một khoảng hở, không phải
một hiện tượng lạ. Đẩy checkpoint lên để nó tiếp tục chạy sẽ làm mất 4103 vĩnh viễn — nếu đó là một
lệnh rút tiền, mọi số dư sau nó đều sai, và chỉ có rebuild mới sửa được. **Dừng partition lại, hoặc
tạm gác nó và đi lấy vị trí còn thiếu**; không bao giờ nhảy qua khoảng hở. Ghi thay đổi projection
và checkpoint trong cùng một transaction, cùng quy tắc như với
[inbox](message-delivery-semantics.md#inbox-do-not-apply-it-twice).

Một **poison event** — event mà code hiện tại không đọc hay apply được, thường là một
[version event cũ](event-schema-evolution.md#failure-modes) mà reader không còn hiểu — cần một chính
sách do từng view quyết định, không phải do deadline quyết định. Một view phân tích có thể cách ly
nó và công bố một con số biết rõ là chưa đầy đủ; một view số dư hay sổ cái thì không thể bỏ qua nó,
vì mọi giá trị sau đều phụ thuộc vào nó, và job phải dừng lại chờ con người.

Dashboard hạ tầng không thấy bất kỳ điều gì trong số này: một partition bị kẹt không dùng CPU. Hãy
alert theo **tuổi của event chưa xử lý lâu nhất trên mỗi partition**, theo số poison event, và theo
độ lệch giữa projection với các tổng kiểm soát.

## Không bao giờ rebuild tại chỗ

Replay thẳng vào bảng đang chạy sẽ race với projector đang chạy. Một đợt backfill ghi đè một
document hai năm tuổi lên một document vừa cập nhật một giây trước, không kiểm tra version, sẽ đưa
sản phẩm về giá cũ và tồn kho cũ trong khi checkpoint báo "hoàn tất". Còn truncate trước thì khiến
người đọc view đó không có gì trong nhiều giờ. Thay vào đó:

1. **Dựng một shadow copy** — một bảng, index hay namespace mới — trong khi bản cũ vẫn phục vụ.
2. **Chốt một high-water mark** ngay từ đầu: vị trí head, **theo từng partition** nếu không có thứ
   tự toàn cục. Backfill tới mốc đó.
3. **Bắt kịp** mọi thứ được ghi từ lúc đó, và chỉ đi tiếp khi độ trễ đã dưới một ngưỡng đã nêu rõ.
4. **Kiểm chứng** trước khi có người đọc: số dòng và các ràng buộc; tổng kiểm soát nghiệp vụ so với
   nguồn có thẩm quyền; một bản diff so với bản cũ, mỗi khác biệt được phân loại là dự kiến (chính
   cái bug bạn đã sửa) hoặc bất ngờ (cần điều tra); và timeline được kiểm tay cho các entity biên.
5. **Cut over** bằng cách chuyển một alias hoặc cờ, và giữ bản cũ trong một khoảng thời gian để
   rollback.

Điều tiết tốc độ backfill theo các tín hiệu production mà nó có thể làm tổn hại — p99 của luồng
command, tải của event store, replica lag — chứ không theo một tốc độ cố định. Một lần khôi phục
gây ra sự cố thứ hai thì chưa khôi phục được gì. Và chọn phạm vi nhỏ nhất mà bạn chứng minh được là
đúng: nếu bug tồn tại ba tuần trong một partition, hãy rebuild partition đó và mấy tuần đó, không
phải năm năm của mọi thứ.

## Ước lượng rebuild theo RTO

"Lúc nào cũng replay được" là đúng nhưng vô dụng nếu không làm phép tính:

```
rebuild time ≈ events ÷ measured projector throughput + catch-up + verification
3 000 000 000 ÷ 40 000/s ≈ 21 h  — against an RTO of 2 h
```

Nếu con số vượt quá RTO, replay toàn bộ không phải là kế hoạch khôi phục. Restore một snapshot đã
được kiểm chứng rồi chỉ replay phần chênh lệch, chia partition và chạy song song, mới là kế hoạch.
Hãy đo throughput trong một buổi diễn tập trên dữ liệu cỡ production; một lần rebuild chưa thử là
một giả định, giống như một bản backup chưa từng được restore.

## Sau khi restore, đừng tin thứ bạn nạp vào

Một lần restore từ backup hoặc một lần append bị mất có thể khiến store bị *tụt lại phía sau* những
thứ được dẫn xuất từ nó:

- **Snapshot đi trước stream.** Nếu version của snapshot cao hơn head của stream, nó đã được dựng từ
  những event không còn tồn tại. Nạp nó vào là phục vụ một trạng thái không có gì biện minh được.
  **Fail closed**: từ chối nạp và điều tra.
- **Khoảng hở bên trong một stream.** Fold qua các version 1–90 và 96–140 vẫn ra một số dư trông hợp
  lý. Hãy kiểm tra version theo từng aggregate là liên tục khi nạp và sau mỗi lần restore.
- **Snapshot cộng phần đuôi phải bằng replay toàn bộ.** Một serializer snapshot làm rơi hoặc làm
  tròn một trường sẽ khiến hai bên không khớp. Hãy test sự tương đương này, và cho một snapshot
  không đọc được tự động quay về replay toàn bộ.

## Sai sự việc hay sai projection?

Phản xạ khi gặp sự cố dữ liệu là "drop projection rồi replay". Cách đó chỉ giúp được nếu projection
là thứ bị sai. Nếu một command sai đã được chấp nhận — một khoản nạp được ghi gấp mười lần số mà
gateway xác nhận — thì chính event *là* lỗi, và replay sẽ tái tạo y nguyên lỗi đó, sau nhiều giờ
làm việc.

Vì vậy hãy phân loại trước: với một số entity mẫu, so sánh projection, kết quả fold lịch sử event,
và nguồn có thẩm quyền bên ngoài (sao kê ngân hàng, gateway, sổ cái).

- **Lịch sử đúng, projection sai:** sửa projector và rebuild phạm vi bị ảnh hưởng vào một shadow
  copy, như trên.
- **Lịch sử sai:** append một event sửa sai tham chiếu tới event gốc, ghi lại lý do và ai đã duyệt,
  như trong [chỉnh sửa sổ cái](payment-ledger.md#corrections-reversing-entries) — rồi sửa phần
  validation đã để nó lọt vào.

Trước khi làm bất kỳ cách nào, hãy đóng băng các đường có thể lan rộng thiệt hại và giữ lại bằng
chứng: khoảng event, các checkpoint, và version code liên quan.

## Checklist review

- [ ] Projector chỉ suy ra trạng thái; hiệu ứng ra bên ngoài chạy từ bản ghi ý định trong một worker riêng
- [ ] Replay worker không thể chạm tới thế giới bên ngoài: không có thư viện client, không có credential outbound, egress bị chặn
- [ ] Lời gọi ra ngoài trong lúc replay được đếm, và mọi giá trị khác không đều dừng job
- [ ] Quyết định bên ngoài (gian lận, rủi ro, FX) được lưu thành event, không bao giờ truy vấn lại khi replay
- [ ] `apply` không đọc đồng hồ, không sinh ID và không gọi gì; timestamp lấy từ event
- [ ] Quy tắc thay đổi theo thời gian được áp theo hiệu lực tại thời điểm của từng event, không theo hôm nay
- [ ] Checkpoint không bao giờ nhảy qua khoảng hở; checkpoint và thay đổi projection commit cùng nhau
- [ ] Mỗi view có chính sách cho poison event; view kiểu sổ cái dừng lại chứ không bỏ qua
- [ ] Có alert theo tuổi của event chưa xử lý lâu nhất trên mỗi partition
- [ ] Rebuild ghi vào shadow copy với high-water mark theo partition, bước bắt kịp và cut over bằng alias
- [ ] Cut over đòi hỏi tổng kiểm soát và một bản diff đã phân loại so với bản cũ
- [ ] Rebuild được điều tiết theo độ trễ production và replica lag
- [ ] Thời gian rebuild toàn bộ đã được đo và so với RTO; có snapshot cộng phần chênh lệch nếu nó dài hơn
- [ ] Nạp snapshot fail closed khi nó đi trước stream; version của stream được kiểm tra khoảng hở sau restore
- [ ] Sự cố dữ liệu được phân loại sai sự việc hay sai projection trước mọi lần replay

---

*Bản dịch tiếng Việt của bài học cùng tên. Khởi nguồn từ series Event Sourcing & CQRS của TechCraft,
phần P3, P4, P7, P8, P12, P21, P22, P24 và P28
([collection](https://www.patreon.com/techcraft_official)). Các bài viết cung cấp: email gia hạn và
lần retry thanh toán bị replay kích hoạt, apply thuần và quyết định bên ngoài như sự việc, các hàng
rào hạ tầng thay cho cờ replay, sự phân biệt thay đổi chính sách với sửa bug, cách xử lý khoảng hở ở
checkpoint, đợt backfill tại chỗ đưa giá cũ trở lại, các bước watermark, shadow và đối soát, điều
tiết tốc độ, phép tính RTO, snapshot fail closed và test tương đương, khoảng hở sau restore, cùng sự
cố sai sự việc và cách phân loại. Cấu trúc, các con số minh họa và checklist review là của
repository này.*
