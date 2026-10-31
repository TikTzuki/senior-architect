---
source_sha256: 23e580f921924feafc0279e1a902794ed703bc3e6d07ae33b6eb630697c23e5f
---

# Lưu giữ dữ liệu & Lịch sử

**Quy tắc: còn dữ liệu chưa chắc đã còn lịch sử.** `is_deleted = true` để dòng đó ở lại, nhưng
xóa mất mọi dấu vết về chuyện đã xảy ra với nó.

Thuộc nhóm data modeling: **lưu giữ & lịch sử** (trang này) ·
[dữ liệu thời gian](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[replay event & rebuild](event-replay-and-projection-rebuild.md) ·
[tiến hóa schema event](event-schema-evolution.md) ·
[ngữ nghĩa phân phối message](message-delivery-semantics.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Vì sao hệ thống lớn gần như không bao giờ xóa](#why-large-systems-almost-never-delete)
- [Soft delete là bước đầu, không phải lời giải](#soft-delete-is-a-first-step-not-a-solution)
- [Audit log ghi lại hành động](#audit-log-records-actions)
- [Bảng history ghi lại trạng thái](#history-tables-record-state)
- [Bạn cần cái nào](#which-one-you-need)
- [Xóa dữ liệu khi lịch sử là append-only](#erasure-when-history-is-append-only)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
-- Thứ Hai: script dọn dẹp "làm gọn" dữ liệu.
DELETE FROM credit_limits WHERE customer_id = :id AND active = false;

-- Thứ Ba: bộ phận compliance hỏi vì sao hạn mức của khách này đi từ 10M → 50M → biến mất.
-- Không có câu trả lời. Không có dòng nào. Không có bằng chứng rằng từng có một dòng.
```

Phiên bản trông "nhẹ tay" hơn cũng mang đúng khuyết tật đó:

```sql
UPDATE credit_limits SET is_deleted = true WHERE id = :id;
```

Dòng dữ liệu vẫn còn, nhưng mọi câu hỏi mà người ta thực sự sẽ hỏi vẫn không có lời đáp: *ai* làm,
*khi nào*, *vì sao*, và *giá trị trước đó là gì*. Bạn giữ được đối tượng nhưng đánh mất sự kiện.

## Vì sao môi trường dev che giấu lỗi này

Ở môi trường dev chẳng có ai hỏi về quá khứ. Bạn kiểm tra trạng thái hiện tại đúng, test pass,
tính năng được release. Hóa đơn đến vài tháng sau, từ một người không phải kỹ sư — kiểm toán viên,
luật sư, nhân viên hỗ trợ, cơ quan quản lý — hỏi dữ liệu trông như thế nào tại một thời điểm cụ thể
trong quá khứ.

Đến lúc đó thì thông tin chưa bao giờ được ghi lại. Đây là đặc điểm định danh của loại bug này:
**không thể sửa ngược về quá khứ.** Bạn không thể backfill thứ lịch sử mà mình chưa từng ghi.

## Vì sao hệ thống lớn gần như không bao giờ xóa

`DELETE` được coi là thao tác phá hủy, chỉ dùng khi hết cách, vì một dòng không chỉ là một dòng:

- **Pháp lý và quy định** — thời hạn lưu trữ thường bắt buộc nhiều năm, và xóa trong thời hạn đó tự
  nó đã là vi phạm.
- **Giải quyết tranh chấp** — "chứng minh khách hàng đã đồng ý hạn mức này" đòi hỏi trạng thái tại
  thời điểm ký.
- **Debug** — một bug từ ba tuần trước không thể tái hiện nếu dữ liệu mà nó tác động đã mất.
- **Tính đúng đắn tài chính** — dòng bị xóa làm hỏng đối soát, vì tổng không còn khớp và không có gì
  giải thích phần chênh lệch.

Ngoại lệ vẫn phải hoạt động được là một yêu cầu xóa thật sự (GDPR và các quy định tương tự). Đó là
việc xóa hoặc ẩn danh hóa có chủ đích, có phạm vi, có ghi log đối với một số trường dữ liệu cá nhân
cụ thể — không phải một script dọn dẹp, và nó phải để lại bản ghi rằng việc xóa đã diễn ra.

## Soft delete là bước đầu, không phải lời giải

Soft delete ra đời vì hard delete từng gây ra thảm họa, và nó giải quyết đúng một vấn đề: dòng dữ
liệu có thể khôi phục.

Những gì nó không làm được, mà các team vẫn mặc định là nó làm được:

- **Không ghi lại người thực hiện và thời điểm.** `is_deleted = true` không nói gì về ai hay khi
  nào. Tối thiểu hãy thêm `deleted_at` và `deleted_by` — một boolean đơn lẻ gần như vô dụng.
- **Không ghi lại giá trị cũ.** Một lệnh `UPDATE` đổi hạn mức từ 10M lên 50M không để lại dấu vết
  nào; soft delete chỉ bao phủ việc xóa.
- **Nó lan vào mọi query, mãi mãi.** Mọi `SELECT` đều cần `WHERE is_deleted = false`, và chỉ một
  chỗ ai đó quên là thành bug đúng đắn, hiển thị dữ liệu đã xóa cho người dùng. Partial index và
  view có giúp được; chỉ dựa vào kỷ luật thì không.
- **Ràng buộc unique bị vỡ.** Ràng buộc `UNIQUE(email)` giờ va chạm với các dòng đã soft delete.
  Cách sửa thường dùng là một partial unique index (`WHERE deleted_at IS NULL`).

## Audit log ghi lại hành động

Audit log trả lời *chuyện gì đã xảy ra, ai làm, và khi nào*:

| Trường           | Mục đích                                         |
|------------------|--------------------------------------------------|
| entity type + id | đối tượng nào bị tác động                        |
| action           | created / updated / deleted / approved           |
| actor            | user, service hoặc job — không bao giờ được null |
| timestamp        | khi nào                                          |
| changed fields   | cũ → mới cho những gì đã thay đổi                |
| reason / context | request id, ticket, mã tham chiếu phê duyệt      |

Các thuộc tính quan trọng: **append-only** (thu hồi quyền `UPDATE`/`DELETE` ở mức database role,
như trong [payment-ledger](payment-ledger.md)), được ghi trong cùng transaction với thay đổi mà nó
mô tả để không thể bị thiếu, và luôn có actor — "system" là actor hợp lệ, `NULL` thì không.

## Bảng history ghi lại trạng thái

Đây là điểm khác biệt mà phần lớn các team bỏ lỡ, và cũng chính là lý do cả hai pattern cùng tồn
tại:

> Biết **ai đã thay đổi** dữ liệu là một câu hỏi khác với biết **dữ liệu khi đó là gì** tại một
> thời điểm.

Audit log cho bạn một chuỗi hành động. Để trả lời *"toàn bộ bản ghi này trông thế nào lúc
10:30?"*, bạn phải chạy lại mọi hành động từ lúc tạo trở đi và cầu mong không sót cái nào. Cách đó
tốn kém, dễ gãy, và thường cho kết quả sai.

Một **bảng history** lưu toàn bộ dòng sau mỗi lần thay đổi:

```sql
CREATE TABLE contracts_history (
  history_id    bigserial PRIMARY KEY,
  contract_id   bigint NOT NULL,
  -- ... mọi cột nghiệp vụ, đúng như giá trị lúc đó ...
  valid_from    timestamptz NOT NULL,
  valid_to      timestamptz,          -- NULL = phiên bản hiện tại
  changed_by    text NOT NULL
);
```

Giờ thì "bản ghi tại thời điểm 10:30" là một query có index, không phải một lần replay. Cái giá là
dung lượng lưu trữ và khuếch đại ghi; cái được là câu hỏi đó có thể trả lời được.

## Bạn cần cái nào

| Câu hỏi bạn phải trả lời                                             | Pattern                                                                                                                |
|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| "Ai đã khóa tài khoản này, và khi nào?"                              | Audit log                                                                                                              |
| "Hợp đồng ghi gì vào ngày được ký?"                                  | Bảng history                                                                                                           |
| "Vì sao chúng ta duyệt hạn mức này?"                                 | Audit log (trường reason/context)                                                                                      |
| "Giá trị của từng trường lúc 10:30 thứ Ba tuần trước là gì?"         | Bảng history                                                                                                           |
| "Tái dựng cách số dư đi đến con số này"                              | Event sourcing / ledger — xem [event sourcing & CQRS](event-sourcing-and-cqrs.md), [payment-ledger](payment-ledger.md) |
| "Hồi đó chúng ta *tin* điều gì là đúng, so với điều đúng bây giờ?"   | Bitemporal — xem [dữ liệu thời gian](temporal-data.md)                                                                 |

Chúng bổ sung cho nhau, không thay thế nhau. Phần lớn hệ thống chịu quản lý cần audit log để quy
trách nhiệm và bảng history để tái dựng, và chỉ có một trong hai là kiểu thất bại phổ biến.

Hãy chọn dựa trên những câu hỏi mà nghiệp vụ sẽ thực sự đặt ra — và đặt câu hỏi đó *trước khi*
release schema, vì đây là quyết định thiết kế duy nhất mà bạn không thể quay lại sửa sau.

## Xóa dữ liệu khi lịch sử là append-only

Mâu thuẫn càng gay gắt với một [event store](event-sourcing-and-cqrs.md) hoặc một audit log
append-only: `UPDATE` và `DELETE` bị thu hồi có chủ đích, nên dữ liệu cá nhân đã ghi vào payload của
event không thể gỡ bỏ nếu không viết lại lịch sử. Thiết kế này qua được mọi vòng review cho tới yêu
cầu xóa đầu tiên — và lúc đó cùng tên và địa chỉ ấy đã nằm trong snapshot, projection, topic của
broker, data lake và mọi bản backup.

Hãy quyết định trước khi event đầu tiên được ghi:

- **Không đưa dữ liệu cá nhân vào event.** Event mang một subject ID ổn định; tên, thông tin liên hệ
  và giấy tờ nằm trong một kho được bảo vệ, có vòng đời riêng, nơi việc xóa chỉ là một lệnh delete
  bình thường. Đây là phương án đơn giản nhất và bao phủ phần lớn trường hợp.
- **Crypto-shredding** khi dữ liệu cá nhân buộc phải đi cùng event. Mã hóa các trường đó bằng một
  key riêng cho mỗi subject; xóa dữ liệu nghĩa là xóa key, và mọi bản sao — kể cả backup — đồng
  loạt trở nên không đọc được.
- **Lưu giữ theo partition** cho dữ liệu đã hết thời hạn pháp lý: drop nguyên các partition theo
  thời gian dựa trên một chính sách thành văn, thay vì xóa từng event.

Mỗi phương án đều có hệ quả cần được thiết kế từ trước, chứ không phải đợi phát hiện ra:

- **Replay phải sống sót qua các subject đã bị xóa.** Sau khi key bị xóa, event cũ vẫn tồn tại với
  các trường không đọc được. Projector và upcaster phải hiển thị chúng ở dạng đã xóa — cùng một kỷ
  luật như UNKNOWN trong [tiến hóa schema event](event-schema-evolution.md) — chứ không crash hay bỏ
  qua event.
- **Các bản sao dẫn xuất cũng cần được dọn.** Snapshot và read model được dựng khi dữ liệu còn đọc
  được vẫn giữ nó ở dạng rõ; việc xóa phải kích hoạt rebuild hoặc dọn có mục tiêu cho chúng.
- **Quản lý key trở thành một phần của lịch sử.** Key của các subject chưa bị xóa phải sống lâu hơn
  thời hạn lưu giữ, và kho key cần có backup riêng: mất nó là mất mọi trường mã hóa từng được ghi.
- **Event đã publish tuyệt đối không được mang dữ liệu đó.** Không thể bắt consumer phía sau quên
  đi — hãy giữ dữ liệu cá nhân ngoài
  [integration event](event-schema-evolution.md#the-event-store-is-not-a-public-api).

Ghi lại rằng việc xóa đã diễn ra — subject ID, yêu cầu, thời điểm, người thực hiện — mà không kèm
chính dữ liệu đó.

## Checklist review

- [ ] Không có `DELETE` trên dữ liệu có ý nghĩa nghiệp vụ ngoài một chính sách lưu giữ có chủ đích và có ghi log
- [ ] Soft delete mang `deleted_at` và `deleted_by`, không chỉ một boolean
- [ ] Các dòng đã soft delete được loại trừ nhất quán — qua view hoặc partial index, không dựa vào trí nhớ ở từng query
- [ ] Ràng buộc unique có tính đến các dòng đã soft delete (partial unique index)
- [ ] Có audit log cho những thay đổi trạng thái gắn với trách nhiệm
- [ ] Dòng audit là append-only, với `UPDATE`/`DELETE` bị thu hồi ở mức database
- [ ] Dòng audit được ghi trong cùng transaction với thay đổi mà nó mô tả
- [ ] Actor không bao giờ null; thay đổi tự động ghi tên hệ thống hoặc job
- [ ] Nếu cần tái dựng trạng thái tại một thời điểm, phải có bảng history — không replay từ audit
- [ ] Thời hạn lưu giữ được nêu rõ cho từng loại dữ liệu, và việc xóa kiểu GDPR làm được và có ghi log
- [ ] Kho append-only (event, audit) giữ dữ liệu cá nhân ngoài payload, hoặc mã hóa nó theo từng subject
- [ ] Replay, projection và snapshot xử lý subject đã bị xóa mà không crash, và được dọn sau khi xóa
- [ ] Key theo subject sống lâu hơn thời hạn lưu giữ với subject chưa bị xóa, và kho key được backup riêng

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Data Modeling Patterns phần P1–P4 của
TechCraft ([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề "giữ dữ liệu
không phải là giữ lịch sử" và sự phân biệt hành động với trạng thái giữa audit log và bảng history
là của TechCraft; cấu trúc review và checklist là của repository này. Phần xóa dữ liệu trong hệ
append-only dựa trên Event Sourcing & CQRS phần P13, P20 và P26 của TechCraft (tokenization,
crypto-shredding, lưu giữ theo partition, vòng đời key); danh sách hệ quả là của repository này.*
