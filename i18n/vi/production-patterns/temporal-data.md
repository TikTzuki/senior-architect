---
source_sha256: 39a1f8a7277b1349c76d9f9015b47c8e4fe4c913a3993814404bb313a097b39c
---

# Dữ liệu thời gian

**Quy tắc: hệ thống không hành động dựa trên sự thật khách quan — nó hành động dựa trên những gì
nó tin vào lúc đó.** Muốn tái dựng một quyết định trong quá khứ, bạn cần hai trục thời gian: khi nào
sự kiện ấy đúng, và khi nào bạn biết về nó.

Thuộc nhóm data modeling: [lưu giữ & lịch sử](data-retention-and-history.md) ·
**dữ liệu thời gian** (trang này) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[replay event & rebuild](event-replay-and-projection-rebuild.md) ·
[tiến hóa schema event](event-schema-evolution.md) ·
[ngữ nghĩa phân phối message](message-delivery-semantics.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Valid time: mô hình hóa khi nào một sự kiện có hiệu lực](#valid-time-modelling-when-a-fact-applied)
- [Vì sao một trục thời gian là không đủ](#why-one-timeline-is-not-enough)
- [Bitemporal: hai trục](#bitemporal-two-axes)
- [Khi nào bạn thực sự cần nó](#when-you-actually-need-it)
- [Ghi chú triển khai](#implementation-notes)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
CREATE TABLE interest_rates (
  product_id  bigint PRIMARY KEY,
  rate        numeric NOT NULL       -- lãi suất hiện tại. Chỉ lãi suất hiện tại.
);

UPDATE interest_rates SET rate = 8.5 WHERE product_id = :id;   -- lãi suất cũ đã mất
```

Rồi bộ phận pháp chế hỏi: *"Khoản vay này được duyệt hồi tháng Ba thì đã áp lãi suất nào?"* Bảng
chỉ giữ một con số, và đó là con số của hôm nay.

Ngay cả bảng history cũng chỉ cứu được một phần, vì nó ghi *khi nào dòng dữ liệu thay đổi*, chứ
không ghi *khi nào lãi suất có hiệu lực*. Một mức lãi suất hiệu lực từ 1 tháng Một nhưng được nhập
vào hệ thống ngày 15 tháng Hai mang hai mốc ngày khác nhau, và một schema chỉ có một trục thời gian
không thể biểu diễn cả hai.

## Vì sao môi trường dev che giấu lỗi này

Test chỉ assert trên giá trị hiện tại, và giá trị hiện tại thì luôn đúng. Ở local chẳng có gì đặt
câu hỏi gắn với một thời điểm trong quá khứ.

Lỗi này còn **im lặng và không thể khôi phục**: không có gì báo lỗi, và thông tin chưa từng được
lưu, nên làm thêm bao nhiêu sau đó cũng không tạo ra được câu trả lời. Giống các pattern về lưu giữ
dữ liệu, đây là một quyết định schema không thể sửa lại sau khi sự việc đã xảy ra.

## Valid time: mô hình hóa khi nào một sự kiện có hiệu lực

Trục thứ nhất là **valid time** — khoảng thời gian mà một sự kiện đúng trong thế giới thực:

```sql
CREATE TABLE interest_rates (
  product_id   bigint NOT NULL,
  rate         numeric NOT NULL,
  valid_from   date NOT NULL,
  valid_to     date,                     -- NULL = vẫn còn hiệu lực
  PRIMARY KEY (product_id, valid_from)
);
```

Truy vấn "lãi suất áp dụng ngày 15 tháng Ba":

```sql
SELECT rate FROM interest_rates
WHERE product_id = :id
  AND valid_from <= '2026-03-15'
  AND (valid_to IS NULL OR valid_to > '2026-03-15');
```

Cách này đưa thời gian thành một phần chính thức của mô hình thay vì chỉ là metadata của dòng. Nó
cũng cho phép insert một thay đổi có ngày hiệu lực trong tương lai — một mức lãi suất áp dụng từ
tháng sau — điều mà một cột `rate` có thể ghi đè hoàn toàn không biểu diễn được.

Có hai bất biến nên ép buộc thay vì tin tưởng: **không có khoảng thời gian chồng lấn** cho cùng một
key, và **không có khoảng trống** ở chỗ một sự kiện bắt buộc lại không có giá trị. Trong PostgreSQL,
ràng buộc `EXCLUDE` kết hợp kiểu range ép buộc trực tiếp việc không chồng lấn; xem
[transaction-isolation](transaction-isolation.md) để hiểu vì sao kiểm tra ở tầng ứng dụng là không
đủ.

## Vì sao một trục thời gian là không đủ

Kịch bản làm mọi thứ trở nên cụ thể, và đáng để đọc kỹ:

> Một công ty bảo hiểm duyệt một hồ sơ bồi thường vào tháng Ba. Đến tháng Mười, kiểm toán viên
> hỏi: *"Tại sao hồi tháng Ba lại duyệt cho John Doe một khoản chi trả thấp như vậy?"*
>
> Kỹ sư tra lại và giải thích: "Vì anh ấy sinh năm 1990 — nhóm rủi ro thấp, phí rẻ hơn, quyền lợi
> thấp hơn."
>
> Kiểm toán viên đáp: "Nhưng hôm nay hệ thống ghi là 1988."
>
> Cả hai đều đúng. Ngày sinh của anh ấy xưa nay vẫn là 1988 — đó là sự thật. Nhưng vào tháng Ba, hệ
> thống **đang lưu** 1990, và nó định giá hợp đồng theo cái nó đang lưu. Tới tháng Sáu thì có người
> sửa lại bản ghi.

Vậy quyết định tháng Ba có sai không? Với một trục thời gian, bạn không thể biết. Bạn có sự thật của
hôm nay, và đã vĩnh viễn mất *điều hệ thống biết vào lúc nó ra quyết định*.

> Thứ một hệ thống dựa vào không phải là sự thật khách quan, mà là hiểu biết của chính nó vào đúng
> khoảnh khắc ra quyết định.

Sửa dữ liệu khác với dữ liệu thay đổi. Khách hàng chuyển nhà là một sự kiện mới với một khoảng hiệu
lực mới. Sửa một lỗi gõ nhầm ngày sinh là một **chỉnh sửa hồi tố** — sự thật vốn luôn là giá trị đã
sửa, chỉ có hiểu biết của bạn thay đổi. Gộp hai chuyện này làm một chính là bug.

## Bitemporal: hai trục

Giữ cả hai:

- **Valid time** — khi nào sự kiện đúng trong thế giới thực
- **Transaction time** (hay *decision time*) — khi nào hệ thống ghi nhận nó

```sql
CREATE TABLE customer_facts (
  customer_id     bigint NOT NULL,
  date_of_birth   date NOT NULL,
  valid_from      date NOT NULL,          -- hiệu lực trong thế giới thực
  valid_to        date,
  recorded_at     timestamptz NOT NULL,   -- khi chúng ta biết được
  superseded_at   timestamptz,            -- khi niềm tin này bị thay thế
  PRIMARY KEY (customer_id, valid_from, recorded_at)
);
```

Hai câu hỏi tách biệt, cả hai đều trả lời được:

```sql
-- Theo hiểu biết tốt nhất hiện nay, điều gì đang đúng?
WHERE valid_to IS NULL AND superseded_at IS NULL

-- Vào tháng Ba, chúng ta tin gì về trạng thái thế giới trong tháng Ba?
WHERE valid_from <= '2026-03-15' AND (valid_to IS NULL OR valid_to > '2026-03-15')
  AND recorded_at <= '2026-03-31' AND (superseded_at IS NULL OR superseded_at > '2026-03-31')
```

Query thứ hai mới là thứ bảo vệ được quyết định tháng Ba. Nó tái dựng không chỉ trạng thái quá khứ
của thế giới mà cả trạng thái quá khứ của *hiểu biết* trong hệ thống — và đó chính là thứ mà một
cuộc kiểm toán thực sự soi vào.

## Khi nào bạn thực sự cần nó

Mô hình bitemporal thực sự đắt: tốn thêm dung lượng, query khó hơn, và đặt một gánh nặng khái niệm
thật sự lên bất kỳ ai đụng vào schema. Đừng áp dụng nó theo mặc định.

**Bạn cần nó khi một quyết định trong quá khứ phải bảo vệ được sau khi dữ liệu gốc đã được sửa.**
Trên thực tế:

- **Bảo hiểm** — định phí và xử lý bồi thường dựa trên thông tin khách hàng mà sau này bị sửa
- **Ngân hàng và cho vay** — quyết định tín dụng, lãi suất đã áp, hạn mức chịu thanh tra
- **Quản lý quỹ** — NAV được tính lại sau khi một giao dịch hoặc mức giá được sửa
- **Lương và thuế** — điều chỉnh lương hồi tố và quy định thuế áp dụng lùi ngày
- **Y tế** — quyết định lâm sàng dựa trên hồ sơ về sau được bổ sung, chỉnh sửa

**Bạn không cần nó cho** danh mục sản phẩm, tùy chọn người dùng, feature flag, hay bất cứ thứ gì
mà "nó đã đổi và giờ khác rồi" là đủ để giải thích. Với những thứ đó, chỉ valid time hoặc một bảng
history thông thường là đúng.

Câu hỏi để review: *nếu năm sau dữ liệu này bị sửa, có ai cần giải thích một quyết định đã đưa ra
dựa trên giá trị cũ không?* Nếu có, một trục thời gian là không đủ.

## Ghi chú triển khai

- **Không bao giờ `UPDATE` tại chỗ.** Một chỉnh sửa sẽ insert phiên bản mới và đóng niềm tin trước
  đó bằng cách set `superseded_at`. Cùng logic bất biến như [payment-ledger](payment-ledger.md).
- **Đánh index cho đúng query bạn chạy.** Tra cứu tại một thời điểm cần key cùng cả hai khoảng thời
  gian; thiếu nó, mọi query lịch sử đều thoái hóa thành scan — xem
  [indexes-and-query-plans](indexes-and-query-plans.md).
- **Chốt quy ước biên bao gồm/không bao gồm một lần** và ghi lại thành tài liệu. Khoảng nửa mở
  `[from, to)` tránh được sự mơ hồ lệch một ngày vốn sẽ xuất hiện trong mọi báo cáo.
- **Dùng kiểu range và exclusion constraint** khi có thể, thay vì tin code ứng dụng sẽ giữ các
  khoảng thời gian không giao nhau.
- **Đừng để "bây giờ" nằm trong dữ liệu.** Không bao giờ lưu một cờ `is_current` tính sẵn mà phải
  bảo trì; hãy suy ra trạng thái hiện tại từ `valid_to IS NULL AND superseded_at IS NULL`.

## Checklist review

- [ ] Các sự kiện mà giá trị quá khứ có ý nghĩa mang `valid_from` / `valid_to`, không phải một cột bị ghi đè
- [ ] Khoảng hiệu lực chồng lấn bị chặn bằng ràng buộc, không bằng quy ước
- [ ] Khoảng trống trên trục thời gian của các sự kiện bắt buộc được phát hiện
- [ ] Mô hình phân biệt chỉnh sửa với thay đổi thực sự
- [ ] Ở nơi quyết định quá khứ phải bảo vệ được, transaction time được lưu cùng valid time
- [ ] Query tại một thời điểm cố định cả hai trục, không chỉ valid time
- [ ] Dòng lịch sử không bao giờ bị update tại chỗ; chỉnh sửa thì thay thế (supersede)
- [ ] Query thời gian có index trên key cộng khoảng thời gian
- [ ] Quy ước biên khoảng (nửa mở) nhất quán và được ghi lại
- [ ] Trạng thái hiện tại được suy ra, không lưu thành một cờ phải bảo trì

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Data Modeling Patterns phần P5–P6 của
TechCraft ([collection](https://www.patreon.com/techcraft_official)). Kịch bản ngày sinh trong bảo
hiểm và cách đặt vấn đề "hệ thống chạy trên những gì nó biết, không phải trên sự thật khách quan"
là của TechCraft; cấu trúc review và checklist là của repository này.*
