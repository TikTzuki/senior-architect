---
source_sha256: fbdb82c959c2b47b9e1e471944996b28589a2eb415d8cbea6432dece8f576251
---

# Tiến hóa schema event

**Quy tắc: mọi version event từng được ghi đều là một định dạng bạn phải đọc được, với đúng ý nghĩa
ban đầu của nó, chừng nào store còn tồn tại.** Thay đổi một event không phải là một lần deploy — đó
là bổ sung vĩnh viễn vào tập hình dạng mà code của bạn phải hiểu, và nó chỉ được kiểm chứng khi có
thứ gì đó replay lịch sử.

Thuộc nhóm data modeling: [lưu giữ & lịch sử](data-retention-and-history.md) ·
[dữ liệu thời gian](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[replay event & rebuild](event-replay-and-projection-rebuild.md) ·
**tiến hóa schema event** (trang này) ·
[ngữ nghĩa phân phối message](message-delivery-semantics.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Các failure mode](#failure-modes)
- [Thay đổi hình dạng và thay đổi ý nghĩa](#shape-changes-and-meaning-changes)
- [Upcaster chuyển dịch; nó không bịa ra](#upcasters-translate-they-do-not-invent)
- [Reader trước, writer sau](#readers-first-writers-second)
- [Chứng minh lịch sử cũ vẫn đọc được](#prove-old-history-still-reads)
- [Event store không phải là public API](#the-event-store-is-not-a-public-api)
- [Checklist review](#review-checklist)

## Anti-pattern

Một lần dọn dẹp được merge vào thứ Ba:

```diff
 class FundPurchased(Event):
-    amount: Decimal
+    investment_amount: Decimal   # tên rõ nghĩa hơn, product yêu cầu
+    currency: str = "VND"        # sắp mở thị trường mới
```

Event mới được ghi với hình dạng mới; projector đang chạy đọc được chúng; mọi test đều pass, vì mọi
test đều dựng event từ class của hôm nay. Vài tuần sau có người rebuild một projection. Ba năm lịch
sử đầu tiên có `amount` và không có `currency`, nên lần rebuild hoặc fail ở mọi event cũ, hoặc — tệ
hơn, với một deserializer dễ dãi — đọc chúng thành số không, bằng VND.

## Vì sao môi trường dev che giấu lỗi này

Code đang chạy chủ yếu đọc những event vừa được ghi bởi chính version code đó. Fixture của test được
dựng từ các class hiện tại, nên chúng chỉ có thể mang hình dạng hiện tại. Event cũ chỉ được đọc bởi
replay, rebuild và consumer mới bắt đầu từ đầu — những thao tác hiếm khi chạy, và thường là trong
lúc có sự cố. Vì vậy một sai lầm về schema được release một cách im lặng và chỉ bị phát hiện vài
tháng sau, vào lúc tệ nhất, trên mọi event được ghi trước nó.

## Các failure mode

**1. Đổi tên hoặc xóa trường làm vỡ lịch sử.** Một trường bị đổi tên hay bỏ đi mà không tăng version
khiến mọi event trước đó trở nên không đọc được với code mới — bị phát hiện ở lần rebuild tiếp theo,
không phải lúc deploy.

**2. Giá trị mặc định bịa ra sự việc.** Một upcaster điền `"VND"` cho `currency` bị thiếu, hoặc điền
quỹ phổ biến nhất cho `fund_code` bị thiếu. Báo cáo lịch sử giờ khẳng định chắc nịch một điều mà hệ
thống chưa từng ghi nhận. Nếu không suy ra được giá trị từ những gì event cũ thực sự mang theo, nó
là **unknown** và phải nói rõ như vậy — một cách tường minh, khác với null.

**3. Cùng một trường đổi nghĩa.** `amount` trước đây là tổng trước thuế, giờ là sau thuế, cùng tên
và cùng version. Không có gì parse lỗi; mọi lần replay, báo cáo và đối soát trên event cũ giờ đều
sai đúng bằng khoản thuế. Đây là thay đổi nguy hiểm nhất vì không phép kiểm tra nào thấy được nó —
cùng loại hỏng hóc như đổi đơn vị trong một
[API contract](api-contracts.md#three-breakages-that-pass-every-schema-check).

**4. Một giá trị enum bị xóa.** Code bỏ một trạng thái không còn được sinh ra nữa. Event từ hai năm
trước vẫn chứa nó. Consumer gặp một event như vậy không deserialize được, và partition của nó dừng
lại — với vài trăm tài khoản trên partition đó — trong khi mọi metric hạ tầng vẫn xanh. Thêm giá
trị là việc của consumer; xóa giá trị là việc của producer, mãi mãi.

**5. Một lần rollback không thể rollback.** Writer mới đã append event ở V2 thì một bug buộc phải
rollback. Code quay về; event thì không. Reader cũ giờ gặp event V2 và fail. Trong hệ thống event
sourcing, tính an toàn khi rollback được quyết định trước khi writer được release.

**6. Upcaster không tất định.** Một upcaster tra bảng tham chiếu hay gọi service để điền một trường
sẽ cho câu trả lời khác nhau ở các lần replay khác nhau, phá vỡ tính tất định mà mọi lần rebuild
dựa vào — xem [replay](event-replay-and-projection-rebuild.md#apply-must-be-deterministic).

**7. Sự việc bị vứt bỏ lúc ghi.** Một event chỉ ghi những gì tính năng hôm nay cần sẽ đánh rơi
những gì hệ thống biết vào lúc đó — nguồn, kênh, quy tắc đã áp dụng. Về sau không gì khôi phục được;
lịch sử chỉ chứa những gì đã được ghi. Hãy ghi lại ngữ cảnh mà command thực sự có.

## Thay đổi hình dạng và thay đổi ý nghĩa

Phân loại mọi thay đổi trước khi viết code cho nó:

| Thay đổi | Event cũ | Cách xử lý |
|---|---|---|
| Thêm một trường tùy chọn | không có trường đó | upcast thành UNKNOWN tường minh, trừ khi suy ra được từ dữ liệu event cũ mang theo |
| Đổi tên / tái cấu trúc | hình dạng cũ | upcaster ánh xạ cũ → mới một cách tất định; không bao giờ sửa event đã lưu |
| Xóa một trường hoặc giá trị enum | vẫn còn chứa nó | tiếp tục đọc nó; ngừng *ghi* nó |
| Đổi ý nghĩa của một trường | không phân biệt được | **không tái sử dụng trường đó** — trường mới hoặc loại event mới |
| Bản thân sự việc đã thay đổi | mô tả sự việc cũ | loại event mới; event cũ giữ nguyên ý nghĩa |

Tolerant reader (bỏ qua trường lạ, coi trường mới là tùy chọn) xử lý các thay đổi bổ sung với chi
phí thấp, nhưng để ý nghĩa trôi đi mà không ai hay. Chép toàn bộ lịch sử sang một store mới là
phương án nặng, chỉ chính đáng khi đổi nền tảng hoặc vì yêu cầu compliance — không bao giờ để làm
gọn một cái tên trường.

## Upcaster chuyển dịch; nó không bịa ra

Upcaster chuyển một event đã lưu sang hình dạng hiện tại tại thời điểm đọc, nối chuỗi từng version
một, trước khi code domain nhìn thấy nó. Các byte đã lưu không bao giờ thay đổi. Quy tắc của nó:

- **Tất định và tự đủ** — đầu vào chỉ là event cũ.
- **Không bịa giá trị** — UNKNOWN, không phải một giá trị mặc định nghe có vẻ hợp lý.
- **Mỗi bước version một upcaster**, giữ lại chừng nào còn event ở version đó.
- **Được test với payload cũ thật**, không phải payload dựng tay.

## Reader trước, writer sau

1. **Kiểm kê mọi consumer** — projector, job replay, export, subscriber của các team khác. Một
   consumer không có owner là một rủi ro bạn chưa định lượng.
2. **Deploy reader** hiểu cả hai version, trong khi writer vẫn phát version cũ.
3. **Replay một lịch sử pha trộn nhiều version** trên staging.
4. **Canary writer** cho một phần nhỏ traffic; theo dõi số version không xác định, lỗi deserialize
   và độ trễ projection.
5. **Mở rộng** writer khi các chỉ số đó giữ ở mức không.
6. **Thu hẹp** — chỉ bỏ phần xử lý version cũ khi không còn event nào của version đó trong thời hạn
   lưu giữ, mà với event store thường nghĩa là không bao giờ.

Đây chính là [expand and contract](schema-migrations.md#expand-and-contract), với một khác biệt:
migration của bảng có thể viết lại các dòng cũ; event store thì không, nên bước "contract" hiếm khi
tới.

## Chứng minh lịch sử cũ vẫn đọc được

Biến tính tương thích thành một cổng CI, không phải một comment trong review:

- **Fixture của mọi version trong quá khứ**, lấy từ payload thật, phải deserialize và upcast được.
- **Một golden stream** — lịch sử được tuyển chọn chứa mọi version, các trường hợp khó, các lần
  chỉnh sửa và trường bị thiếu — được replay tới các trạng thái cuối đã biết.
- **Mọi projection quan trọng được rebuild** từ lịch sử pha trộn nhiều version mà không lỗi.
- **Một phép kiểm tra rằng dữ liệu thiếu ra kết quả UNKNOWN**, không phải giá trị mặc định.

## Event store không phải là public API

Domain event tồn tại để dựng lại trạng thái của một service; hình dạng của nó đi theo nội bộ của
service đó và phải được tự do thay đổi. Publish thẳng chúng lên broker biến mọi lần refactor nội bộ
thành breaking change cho những team bạn chưa từng gặp: tách một event định giá làm hai, và payment
không còn tìm thấy số tiền cuối cùng, analytics đếm trùng, notification crash vì thiếu trường, và
backlog của một consumer dùng chung làm đình trệ một team chẳng liên quan.

Thay vào đó hãy publish **integration event**: một contract riêng, cố ý giữ nhỏ, có version, được
một bộ chuyển dịch sinh ra từ domain event và gửi qua
[outbox](message-delivery-semantics.md#outbox-do-not-lose-the-event). ID nội bộ, chi tiết tính toán
và dữ liệu cá nhân ở lại bên trong. Đối xử với schema của nó bằng kỷ luật của một
[API contract](api-contracts.md).

**Publish sự việc bạn sở hữu, không phải yêu cầu bạn đã gửi.** Một service mới chỉ *yêu cầu* giữ
hàng không được phát `InventoryReserved` — phía sau sẽ báo khách hàng rằng hàng đã được giữ. Hãy phát
`InventoryReservationRequested`; service tồn kho sẽ phát `InventoryReserved` khi điều đó thành sự
thật. Một contract cũng nên nêu rõ những gì nó **không** khẳng định ("đơn hàng đã xác nhận" không có
nghĩa là hàng đã được giữ), để consumer không tự suy diễn.

## Checklist review

- [ ] Thay đổi event được phân loại: bổ sung, đổi tên, xóa, đổi nghĩa, sự việc mới
- [ ] Không event đã lưu nào bị sửa; version cũ được chuyển dịch lúc đọc
- [ ] Một ý nghĩa thay đổi thì có trường hoặc loại event mới, không bao giờ dùng lại trường cũ
- [ ] Upcaster tất định, không gọi gì, và trả ra UNKNOWN thay vì giá trị mặc định bịa ra
- [ ] Giá trị enum và trường vẫn còn trong lịch sử không bao giờ bị xóa khỏi reader
- [ ] Reader hiểu version mới được release trước khi bất kỳ writer nào phát nó; writer được canary
- [ ] Kế hoạch rollback tính đến các event version mới đã được ghi
- [ ] CI deserialize fixture thật của mọi version cũ và replay một golden stream
- [ ] Số version không xác định và số lỗi deserialize được theo dõi theo từng consumer
- [ ] Event ghi lại ngữ cảnh hệ thống có vào lúc đó, không chỉ những gì tính năng hôm nay cần
- [ ] Domain event không được publish trực tiếp; integration event đi qua bộ chuyển dịch và outbox
- [ ] Event được publish nêu sự việc mà producer sở hữu, và nói rõ những gì chúng không khẳng định

---

*Bản dịch tiếng Việt của bài học cùng tên. Khởi nguồn từ series Event Sourcing & CQRS của TechCraft,
phần P9, P14, P23 và P28 ([collection](https://www.patreon.com/techcraft_official)). Các bài viết
cung cấp: lịch sử version của event mua chứng chỉ quỹ, UNKNOWN thay vì giá trị mặc định bịa ra, thay
đổi ý nghĩa từ trước thuế sang sau thuế, lần đổi tên bị phát hiện khi rebuild, ma trận tương thích
và cổng CI với golden stream, rollout reader trước với canary writer, lần rollback không thể rollback
được event, enum bị xóa làm đình trệ một partition, thông tin nguồn gốc bị vứt bỏ, và sự cố domain
event với integration event cùng sự phân biệt ý định với sự việc. Cấu trúc, bảng phân loại thay đổi
và checklist review là của repository này.*
