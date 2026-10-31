---
source_sha256: 0fa37abfdc25541651a1be599ad82ce2842e136303521a2023780960f8adce9b
---

# Optimistic vs Pessimistic Locking

**Quy tắc: lựa chọn này là một canh bạc về tần suất các lần ghi thực sự va chạm, và đoán sai thì trả
giá đắt theo cả hai chiều.** Optimistic khi tranh chấp cao biến thành retry storm; pessimistic khi tải
cao biến thành một hàng đợi.

Thuộc nhóm transaction:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · **locking** (trang này) ·
[distributed transaction](distributed-transactions.md) ·
[ranh giới nhất quán](consistency-boundaries.md)

Với những lock phải trải qua nhiều process thay vì nhiều dòng, xem
[distributed-locks](distributed-locks.md).

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Hai canh bạc](#the-two-bets)
- [Optimistic locking](#optimistic-locking)
- [Retry storm](#the-retry-storm)
- [Pessimistic locking](#pessimistic-locking)
- [Lock là một khoản nợ](#a-lock-is-a-debt)
- [Lựa chọn](#choosing)
- [Checklist review](#review-checklist)

## Anti-pattern

Hai kiểu hỏng, hình dạng ngược nhau.

**Optimistic locking trên một hot row:**

```python
for attempt in range(100):                          # unbounded-ish, no backoff
    item = db.query("SELECT stock, version FROM items WHERE id = ?", item_id)
    n = db.execute("UPDATE items SET stock = ?, version = version + 1 "
                   "WHERE id = ? AND version = ?", item.stock - 1, item_id, item.version)
    if n == 1:
        break
```

Một đợt flash sale dồn 10.000 request vào một dòng. Gần như mọi lần thử đều thua ở bước kiểm tra
version và retry ngay lập tức, nên database nhận một lượng tải gấp nhiều lần tải thật — và toàn bộ
đều vô ích. Tính đúng đắn thì ổn; hệ thống thì sập.

**Pessimistic locking trên một hot row:**

```python
with db.transaction():
    item = db.query("SELECT * FROM items WHERE id = ? FOR UPDATE", item_id)
    ...
    payment_gateway.charge(...)      # holding the lock across a network call
```

Giờ thì mọi request xếp hàng sau bên giữ lock chậm nhất, connection cạn dần vì chờ, và cả những
endpoint không hề đụng tới dòng này cũng bắt đầu lỗi. Xem [lock-contention](lock-contention.md).

## Vì sao môi trường dev che giấu lỗi này

Cả hai chiến lược đều đúng khi chỉ có một người dùng, và tính đúng là thứ test kiểm tra. Tranh chấp là
một thuộc tính của *tải*: ở mức một request mỗi giây thì không phân biệt được hai chiến lược, còn ở mức
vài nghìn request trên cùng một dòng thì chúng hỏng theo hai cách hoàn toàn khác nhau.

Không có gì trong unit test đo tỷ lệ xung đột, mà đó lại là con số duy nhất quyết định chiến lược nào
đúng.

## Hai canh bạc

Sự khác biệt không nằm ở cơ chế, mà là một niềm tin về thế giới:

|                     | Optimistic                                               | Pessimistic                                                     |
|---------------------|----------------------------------------------------------|-----------------------------------------------------------------|
| Giả định            | Xung đột hiếm khi xảy ra                                 | Xung đột là điều được tính trước                                |
| Cách tiếp cận       | Tin trước, kiểm chứng lúc ghi                            | Không tin ai, lock trước                                        |
| Cái giá khi sai     | Retry storm, công sức bỏ phí                             | Xếp hàng, lock contention, deadlock                             |
| Chặn bên đọc        | Không                                                    | Tùy lock mode                                                   |
| Phù hợp nhất cho    | Nhiều dòng, ít va chạm (hồ sơ người dùng, tài liệu)      | Ít hot row, va chạm nhiều (tồn kho flash sale, đặt chỗ ngồi)    |

## Optimistic locking

Thêm một cột `version`, và để lần ghi có điều kiện theo version bạn đã đọc:

```sql
UPDATE documents SET content = :content, version = version + 1
WHERE id = :id AND version = :expected_version;
```

Không dòng nào bị ảnh hưởng nghĩa là người khác đã commit trước. **Kiểm tra số dòng bị ảnh hưởng là toàn
bộ cơ chế** — code bỏ qua nó thì chỉ có optimistic locking trên danh nghĩa, và âm thầm làm mất update.
Xem [transaction-isolation](transaction-isolation.md).

Thua thì làm gì là một quyết định sản phẩm, không phải quyết định kỹ thuật:

- **Tải lại và retry** với một thao tác tăng giảm máy móc (một bộ đếm, trừ tồn kho)
- **Báo xung đột lên** khi có ý định của con người ở đó — người dùng đang sửa một form đã cũ nên được
  thông báo, chứ không bị một lần retry âm thầm ghi đè

Lợi thế thật của nó: không gì bị chặn. Bên đọc không bao giờ bị trễ, và một client chết giữa lúc sửa
cũng không giữ thứ gì.

## Retry storm

Failure mode khiến optimistic locking trở nên nguy hiểm, và là thứ phần lớn các bản cài đặt bỏ sót.

Khi tranh chấp cao, bên thua retry, và các lần retry đó va chạm với nhau cũng như với các request mới
đến. Tải *tăng lên* đúng lúc hệ thống đã đang chật vật — một vòng phản hồi dương.

Những điều kiện để retry an toàn:

- **Giới hạn số lần thử.** Hết lượt thì cho request thất bại. Một vòng lặp không giới hạn biến tranh
  chấp thành sự cố ngừng dịch vụ.
- **Exponential backoff có jitter.** Không có jitter, mọi bên thua retry cùng một nhịp và lại va nhau.
  Jitter ở đây không phải một tinh chỉnh; nó chính là thứ phá vỡ sự đồng bộ.
- **Chạy lại toàn bộ thao tác**, kể cả bước đọc. Chỉ retry bước ghi là áp dụng lại một quyết định đưa
  ra từ một snapshot đã cũ.
- **Theo dõi tỷ lệ xung đột như một metric.** Tỷ lệ retry tăng lên là tín hiệu sớm cho thấy dòng này đã
  vượt quá sức của optimistic locking.

Khi tỷ lệ xung đột cao, câu trả lời thường không phải "retry giỏi hơn" — mà là thôi không đọc giá trị
nữa và dùng một update có điều kiện atomic:

```sql
UPDATE items SET stock = stock - 1 WHERE id = :id AND stock > 0;
```

Không version, không đọc, không retry. Phần lớn tranh chấp trên hot row tan biến ở đây.

## Pessimistic locking

Lấy lock trước và để những bên khác chờ:

```sql
SELECT * FROM items WHERE id = :id FOR UPDATE;
```

Đúng, dễ suy luận, và là lựa chọn đúng khi va chạm có khả năng xảy ra cao và cái giá của nó lớn — bán
quá đơn vị hàng cuối cùng, đặt trùng một chỗ ngồi.

Cái giá chính là những thứ trong [lock-contention](lock-contention.md): truy cập vào dòng bị tuần tự
hóa, lock bị giữ đến lúc commit, hàng đợi lan sang các endpoint không liên quan, và deadlock khi hai
luồng lấy lock theo thứ tự khác nhau.

Những điều không thể thương lượng khi dùng nó:

- **Không bao giờ giữ lock qua một lời gọi mạng.** Đây là cách phổ biến nhất khiến pessimistic locking
  đánh sập một hệ thống.
- **Đặt `lock_timeout`.** Một lỗi có giới hạn tốt hơn một lần treo vô hạn.
- **Lấy lock theo thứ tự xác định** (sắp theo key) để ngăn deadlock.
- **Cân nhắc `NOWAIT` hoặc `SKIP LOCKED`.** Với các bảng worker/queue, `SKIP LOCKED` là lời giải chuẩn,
  để các worker lấy những dòng khác nhau thay vì xếp hàng trên cùng một dòng.

## Lock là một khoản nợ

> Lock là thời gian đi vay. Bạn trả lại nó bằng latency.

Mỗi lock đổi throughput lấy sự chắc chắn. Đó thường là cuộc trao đổi đúng — nhưng nó vẫn là một cuộc
trao đổi, và nó cần được thấy rõ khi review. Một lock được lấy "cho chắc" mà không biết tỷ lệ xung đột
là một khoản nợ chưa định giá, và nó đến hạn đúng ở mức traffic mà bạn xây hệ thống để phục vụ.

## Lựa chọn

Dùng **optimistic** khi các lần ghi vào cùng một dòng hiếm so với tổng số lần ghi: hồ sơ người dùng,
tài liệu, cài đặt, bản ghi đơn hàng theo từng khách. Trường hợp phổ biến không mất chi phí gì.

Dùng **pessimistic** khi cùng một dòng thực sự bị tranh chấp và lost update là không chấp nhận được:
tồn kho đơn vị cuối cùng, phân bổ chỗ ngồi, số dư ví khi có đợt tải dồn dập.

**Không dùng cả hai** khi thao tác có thể biểu diễn thành một câu lệnh có điều kiện atomic. Cách đó nhanh
hơn cả hai, không cần retry, và đúng ở mọi isolation level — hãy xét nó trước tiên.

## Checklist review

- [ ] Chiến lược locking khớp với tỷ lệ xung đột dự kiến, và tỷ lệ đó đã được biết
- [ ] Update atomic trong một câu lệnh đã được cân nhắc trước khi chọn bất kỳ chiến lược locking nào
- [ ] Lần ghi optimistic kiểm tra số dòng bị ảnh hưởng; trường hợp bằng 0 được xử lý tường minh
- [ ] Retry có giới hạn, với exponential backoff có jitter
- [ ] Retry chạy lại toàn bộ thao tác kể cả các bước đọc
- [ ] Tỷ lệ xung đột/retry được đưa ra thành metric
- [ ] Thua xung đột được báo cho người dùng khi có ý định của con người ở đó
- [ ] Không lock nào bị giữ qua một lời gọi mạng
- [ ] Đã đặt `lock_timeout`; lock nhiều dòng được lấy theo thứ tự xác định
- [ ] Bảng worker/queue dùng `SKIP LOCKED` thay vì chặn chờ

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Transaction & Consistency của
TechCraft, phần P12–P13 ([collection](https://www.patreon.com/collection/2256242)). Cách đặt vấn đề
tin tưởng đối lại đa nghi, failure mode retry storm, và "lock là một khoản nợ kỹ thuật" là của
TechCraft; cấu trúc review và checklist là của repository này.*
