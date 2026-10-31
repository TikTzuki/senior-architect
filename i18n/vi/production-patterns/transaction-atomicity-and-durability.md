---
source_sha256: b762a0afdff591e2a1a1ab7b9f13e4989dae1dc0dd648ba50d9ad6c2b6b55d46
---

# Atomicity & Durability

**Quy tắc: `COMMIT SUCCESSFUL` là một lời hứa về các dòng dữ liệu cục bộ trong database của bạn, và
không gì khác.** Nó không nói gì về lời gọi HTTP bạn thực hiện bên trong transaction, cũng không nói
gì về tính đúng đắn của nghiệp vụ.

Thuộc nhóm transaction: **atomicity & durability** (trang này) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transaction](distributed-transactions.md) ·
[ranh giới nhất quán](consistency-boundaries.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Những gì ACID không hứa](#what-acid-does-not-promise)
- [Transaction dở dang còn tệ hơn không có transaction](#a-half-transaction-is-worse-than-none)
- [Atomicity thực sự kết thúc ở đâu](#where-atomicity-actually-ends)
- [Durability là một lời hứa, không phải một lần ghi](#durability-is-a-promise-not-a-write)
- [Những cách durability hóa ra là nói dối](#ways-durability-turns-out-to-be-a-lie)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
with db.transaction():
    payment = Payment.create(order_id=order.id, amount=order.total, status="succeeded")
    provider.charge(order.total)            # ← external system, outside the transaction's reach
    inventory_service.reserve(order.items)  # ← another one
    order.status = "paid"
```

Dashboard sau đó: CPU 30%, không có exception nào từ database, trả về `200 OK`, dòng log ghi
`Transaction committed`. Còn tiền của khách thì mất mà không có đơn hàng nào tương ứng.

Mọi tín hiệu bạn đang theo dõi đều xanh, vì mọi tín hiệu bạn theo dõi đều nói về database. Database đã
làm đúng những gì nó hứa — commit các dòng của chính nó một cách atomic. Khoản charge đã xảy ra phía
provider không nằm trong lời hứa đó, việc giữ hàng trong kho cũng vậy.

Nếu transaction rollback sau khi `provider.charge()` đã trả về, database sạch sẽ như chưa có gì xảy ra
còn khách hàng thì vẫn đã trả tiền.

## Vì sao môi trường dev che giấu lỗi này

Stub ở local không bao giờ lỗi, nên nhánh rollback không bao giờ chạy vào lúc một lời gọi ra ngoài đã
thành công. Không có network partition nào giữa lần charge và lần commit, không có OOM kill, không có
lần deploy nào rơi đúng giữa transaction.

Lý do sâu hơn khiến nó lọt qua review: **về mặt kỹ thuật, code này đúng.** Không ai đọc mà thấy được
lỗi, vì lỗi nằm ở những gì đảm bảo của transaction *loại trừ* — và phần loại trừ đó vô hình tại chỗ
gọi.

## Những gì ACID không hứa

ACID là một hợp đồng bảo hiểm cho tính toàn vẹn của các thao tác dữ liệu. Nó không phải một checklist
cứ dùng `BEGIN` là đạt, và nó không làm cho logic của bạn đúng.

Cụ thể, ACID không bảo vệ bạn khỏi:

- **Lỗi ngữ nghĩa.** Trừ nhầm tài khoản, tính sai phí, áp giảm giá hai lần. Mỗi thứ đều commit hoàn
  hảo.
- **Bất cứ thứ gì nằm ngoài database.** Lời gọi API, publish vào queue, ghi file, gửi email.
  Transaction không thể rollback chúng.
- **Những invariant nghiệp vụ mà database không biết.** Chữ `C` trong ACID có nghĩa là *các constraint
  bạn đã khai báo vẫn được giữ* — không phải *các quy tắc nghiệp vụ của bạn được thỏa mãn*. Database
  không hề biết số dư không bao giờ được âm, trừ khi bạn nói cho nó. Xem
  [ranh giới nhất quán](consistency-boundaries.md).

> Database xanh không có nghĩa là nghiệp vụ còn sống. Nhất quán ở database và nhất quán ở nghiệp vụ là
> hai chuyện khác nhau.

## Transaction dở dang còn tệ hơn không có transaction

> Một transaction thất bại thường là an toàn. Một transaction hoàn thành nửa vời mới là thảm họa thật.

Thất bại trọn vẹn để thế giới nguyên như cũ, và khách hàng thử lại. Không ai bị thiệt. Thành công một
phần lại tạo ra một **trạng thái trung gian** mà không phần nào của hệ thống được thiết kế để hiểu:

- Số dư đã bị trừ, lịch sử giao dịch không có → tiền đã đi mà không giải thích được
- Thanh toán đã ghi nhận, đơn hàng không có → khách hàng trả tiền cho không gì cả
- Đơn hàng đã tạo, hàng chưa được giữ trong kho → bạn đã bán thứ mình không có

Những trạng thái này tệ hơn một lỗi vì chúng *im lặng*. Không có gì throw. Hệ thống báo thành công, và
sự lệch pha chỉ được phát hiện sau đó bởi khách hàng hoặc kế toán.

Đó là thứ atomicity mang lại: không phải "thao tác thành công" mà là **"hệ thống không bao giờ bị bỏ lại
giữa chừng một suy nghĩ."** Nó là một yêu cầu về niềm tin nghiệp vụ, được diễn đạt thành yêu cầu kỹ
thuật.

## Atomicity thực sự kết thúc ở đâu

Ranh giới là database. Bên trong đó, `WAL` (write-ahead logging) biến atomicity thành hiện thực: thay
đổi được ghi vào một log tuần tự *trước khi* được áp dụng vào file dữ liệu, nên một lần crash giữa
chừng vẫn khôi phục được — khi khởi động lại, engine phát lại các transaction đã commit và bỏ những
transaction chưa hoàn tất.

Cơ chế đó bao phủ các dòng trong database đó. Nó không vươn ra ngoài thêm một li nào.

WAL cũng là lý do atomicity và durability là *cùng một* cơ chế nhìn từ hai phía. Log được ghi tuần tự
— điều quan trọng vì ghi đĩa tuần tự nhanh hơn ghi ngẫu nhiên cả một bậc độ lớn, nên log có thể được
flush đồng bộ mà không giết chết throughput, còn các data page thì được cập nhật lười về sau. Quá trình
khôi phục khi đó dùng log hai lần: **redo** để áp dụng lại các transaction đã commit mà page chưa được
ghi xuống, và **undo** để đảo ngược những transaction chưa commit. **Checkpoint** định kỳ flush các
dirty page và cắt ngắn log, đó là thứ giới hạn thời gian khôi phục — khoảng cách checkpoint dài hơn
nghĩa là ghi ở trạng thái ổn định nhanh hơn và khởi động lại chậm hơn, và sự đánh đổi đó chính là RTO
của bạn.

Vậy nên quy tắc khi review: **một transaction chỉ được chứa thao tác với database.** Đưa mọi tác động ra
bên ngoài khỏi nó:

```python
charge = provider.charge(order.total)      # outside: slow, irreversible, unrollbackable

with db.transaction():                     # inside: local, fast, atomic
    Payment.create(order_id=order.id, charge_id=charge.id, status="succeeded")
    Outbox.insert("payment_succeeded", payload)

# the outbox worker publishes after commit
```

Điều này làm lộ ra câu hỏi thật mà đoạn code ban đầu đang che: nếu charge thành công mà commit thất bại
thì sao? Đó là một bài toán distributed transaction với các lời giải của distributed transaction —
idempotency key, outbox pattern, đối soát — không phải thứ mà một câu `BEGIN` giải được. Xem
[distributed transaction](distributed-transactions.md). Transaction chưa bao giờ làm cho nó atomic; nó
chỉ làm cho mọi thứ chậm đi, và giữ lock trong lúc chờ. Xem [lock-contention](lock-contention.md).

## Durability là một lời hứa, không phải một lần ghi

Durability là cam kết: *tôi đã chấp nhận thay đổi này và nó sẽ sống sót, kể cả khi máy này chết ngay
khoảnh khắc tiếp theo.*

Nó **không** đồng nghĩa với việc đã ghi dữ liệu xuống đĩa. Một lời gọi `write()` trả về thường chỉ mới
nằm trong page cache của hệ điều hành, chưa nằm trên thiết bị lưu trữ vật lý. Durability đòi hỏi
write-ahead log phải được flush và `fsync` trước khi commit được xác nhận — đó là lý do độ trễ commit bị
chi phối bởi việc đồng bộ đĩa, và lý do `synchronous_commit = off` làm cho việc ghi nhanh lên bằng cách
biến lời hứa thành có điều kiện.

Trong môi trường phân tán, lời hứa còn vươn xa hơn: một commit chỉ bền khi đủ số replica đã xác nhận
nó. Chỉ xác nhận trên primary thôi thì một lần failover sẽ làm mất những transaction đã được báo là
commit.

## Những cách durability hóa ra là nói dối

- **`fsync` bị tắt hoặc nới lỏng.** `synchronous_commit = off` trong PostgreSQL, hay
  `innodb_flush_log_at_trx_commit` đặt 0 hoặc 2 trong MySQL, đổi một khoảng thời gian có thể mất
  transaction đã commit lấy throughput. Đó là lựa chọn hợp lý cho một số dữ liệu và là thảm họa với
  tiền — điểm mấu chốt là nó phải là một *quyết định*, không phải một giá trị mặc định ai đó chép lại.
- **Phần cứng nói dối.** SSD dân dụng và một số storage ảo hóa xác nhận `fsync` trong khi dữ liệu vẫn
  nằm trong một cache dễ bay hơi. Mất điện khi đó sẽ làm mất những commit đã được xác nhận.
- **Replication bất đồng bộ cộng với failover.** Primary đã xác nhận, replica chưa nhận được, primary
  chết. Những transaction đó biến mất, trong khi ứng dụng đã được báo là chúng an toàn.
- **Backup chưa từng được restore.** Một bản backup bạn chưa restore thử thì không phải backup — nó là
  một giả định chưa được kiểm chứng. Tương tự với một replica bạn chưa bao giờ failover sang.

## Checklist review

- [ ] Không có lời gọi HTTP, publish queue, ghi file, gửi email hay gọi SDK bên thứ ba nào bên trong transaction
- [ ] Transaction chỉ chứa thao tác với database, và được mở muộn nhất có thể
- [ ] Các tác động ra bên ngoài được sắp thứ tự sao cho một lỗi không thể để tiền bị chuyển mà không có bản ghi
- [ ] Các thao tác xuyên hệ thống dùng idempotency key và outbox, không dùng một transaction bao ngoài
- [ ] Invariant nghiệp vụ được đảm bảo bằng constraint, không mặc định suy ra từ ACID
- [ ] Các trạng thái lỗi một phần được liệt kê đầy đủ, và không trạng thái nào im lặng
- [ ] Các thiết lập durability (`synchronous_commit`, `innodb_flush_log_at_trx_commit`) là có chủ đích
- [ ] Mức xác nhận replication khớp với lượng dữ liệu đã commit mà bạn chấp nhận được việc mất
- [ ] Failover và restore backup đã thực sự được diễn tập, không chỉ được cấu hình

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Transaction & Consistency của
TechCraft, phần P1–P3 và P6 ([collection](https://www.patreon.com/collection/2256242)), và đối chiếu
với Database Internals phần P7 và P10 ([collection](https://www.patreon.com/collection/2115129)). Cách
đặt vấn đề "transaction thành công là lời nói dối", lập luận transaction dở dang còn tệ hơn, sự phân
biệt ACID như một hợp đồng bảo hiểm, và cơ chế WAL/redo/undo/checkpoint là của TechCraft; cấu trúc
review và checklist là của repository này.*
