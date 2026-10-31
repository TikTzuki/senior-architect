---
source_sha256: 1d4d342897adeeae78f87c43c19b08bb91397efb37470f6153191de4d47d3577
---

# Schema migration khi đang có tải

**Quy tắc: mức nguy hiểm của một migration nằm ở thời gian nó giữ lock, không phải thời gian nó chạy.**
Một yêu cầu lock đang phải chờ cũng chặn luôn mọi query xếp hàng phía sau nó.

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Hàng đợi lock: phần khiến người ta bất ngờ](#the-lock-queue-the-part-that-surprises-people)
- [DDL nào an toàn](#which-ddl-is-safe)
- [Expand và contract](#expand-and-contract)
- [Backfill](#backfills)
- [Dây an toàn](#seatbelts)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
-- Migration 004
ALTER TABLE orders ADD COLUMN region varchar(32) NOT NULL DEFAULT 'us-east';
CREATE INDEX idx_orders_region ON orders (region);
UPDATE orders SET region = derive_region(shipping_country);
```

Trên database dev có 500 row, đoạn này chạy xong tức thì và migration được duyệt. Trên 80 triệu row ở
production, nó lấy một lock `ACCESS EXCLUSIVE`, ghi lại toàn bộ bảng, build một index trong khi chặn
việc ghi, và chạy một lệnh `UPDATE` trên toàn bảng trong một transaction — suốt thời gian đó mọi lượt
đọc và ghi vào `orders` đều bị chặn.

Migration này *đúng*. Nó tạo ra chính xác schema mong muốn. Nó cũng là một sự cố ngừng dịch vụ.

## Vì sao môi trường dev che giấu lỗi này

Ba yếu tố khiến DDL nguy hiểm đều vắng mặt ở local: kích thước bảng, traffic đồng thời, và hàng đợi
lock. Một migration chạy trên bảng rỗng không có connection nào khác không thể phô ra bất kỳ yếu tố nào
trong đó, nên lần chạy ở local chỉ cho bạn biết câu SQL parse được và kết quả đúng.

Staging thường cũng không bắt được, trừ khi staging giữ dữ liệu cỡ production *và* có tải chạy vào nó
trong lúc migration. Phần lớn đều không như vậy.

## Hàng đợi lock: phần khiến người ta bất ngờ

Đây là cơ chế biến một migration chậm thành một sự cố sập toàn bộ, và nó không hề hiển nhiên.

PostgreSQL cấp các yêu cầu lock theo thứ tự. Một yêu cầu `ACCESS EXCLUSIVE` đang chờ không nhường đường
cho những lượt đọc vốn tương thích với nhau — **chúng xếp hàng sau nó**:

```
T1  long-running SELECT, holds ACCESS SHARE ..................... running
T2  ALTER TABLE, wants ACCESS EXCLUSIVE .......................... waiting on T1
T3  SELECT (would be compatible with T1) ......................... waiting on T2
T4  SELECT ......................................................... waiting on T2
...  every subsequent query on the table ......................... waiting
```

Vậy nên một lệnh `ALTER TABLE` lẽ ra chỉ mất 2 mili giây lại chặn cả bảng trong suốt thời gian mà **một
query chậm chẳng liên quan** cần để chạy xong. Chỉ một query analytics bị bỏ quên, hoặc một connection
idle in transaction, là đủ.

Hệ quả khi review: riêng việc DDL chạy nhanh là chưa đủ. Bạn còn phải giới hạn thời gian nó có thể phải
*chờ*, và đó là việc của `lock_timeout`.

## DDL nào an toàn

Hành vi phụ thuộc vào phiên bản — hãy kiểm chứng với release note của engine bạn dùng. Với PostgreSQL
hiện đại (11+):

**An toàn: chỉ thay đổi metadata, không ghi lại bảng**

- `ADD COLUMN` với default `NULL`, hoặc với một `DEFAULT` hằng số (từ bản 11 được lưu trong catalog)
- `DROP COLUMN` (đánh dấu cột là đã chết; dung lượng được thu hồi sau)
- `ADD CONSTRAINT ... NOT VALID`, rồi `VALIDATE CONSTRAINT` riêng
- `RENAME` một cột hoặc một bảng — tức thì, nhưng xem cảnh báo về tương thích bên dưới
- `CREATE INDEX CONCURRENTLY`, `DROP INDEX CONCURRENTLY`
- `ALTER TABLE SET STATISTICS`, phần lớn các thay đổi storage parameter

**Nguy hiểm: ghi lại bảng hoặc chặn việc ghi**

- `ADD COLUMN ... NOT NULL` không có default trên phiên bản cũ — ghi lại toàn bộ
- `ALTER COLUMN TYPE` — ghi lại, trừ vài trường hợp nới rộng (ví dụ `varchar(n)` sang `varchar(m)`
  với `m > n`, hoặc sang `text`)
- `SET NOT NULL` — scan cả bảng để kiểm tra, trong lúc giữ `ACCESS EXCLUSIVE`. Từ PG 12, một
  `CHECK (col IS NOT NULL)` đã có sẵn và đã được validate cho phép bỏ qua lần scan; hãy thêm check đó
  với `NOT VALID`, validate nó, rồi mới `SET NOT NULL`.
- `CREATE INDEX` không có `CONCURRENTLY` — chặn việc ghi trong suốt quá trình build
- `ADD FOREIGN KEY` không có `NOT VALID` — scan và lock cả hai bảng
- Thêm `DEFAULT` trên phiên bản cũ (trước 11) — ghi lại toàn bộ

`CREATE INDEX CONCURRENTLY` tốn hai lần scan bảng và không thể chạy bên trong transaction — nên nó phải
là một migration riêng, và nhiều migration framework mặc định bọc mỗi file trong một transaction nên cần
tắt tường minh. Nó cũng có thể thất bại và để lại một index `INVALID`, thứ phải được drop rồi build lại;
hãy kiểm tra `pg_index.indisvalid` sau đó.

**`RENAME` đáng có cảnh báo riêng.** Bản thân DDL tức thì và an toàn. Việc *deploy* thì không: code ứng
dụng cũ tham chiếu tới tên cũ sẽ vỡ ngay khi lệnh đó commit. Đổi tên phải đi qua expand/contract như mọi
thay đổi không tương thích khác.

MySQL khác biệt đáng kể — nhiều lệnh `ALTER` hỗ trợ `ALGORITHM=INPLACE, LOCK=NONE`, và các công cụ như
`gh-ost` và `pt-online-schema-change` tồn tại chính vì những lệnh không hỗ trợ thì đau đớn đến thế. Hãy
kiểm tra từng câu lệnh thay vì giả định.

## Expand và contract

Mọi thay đổi không tương thích ngược đều phải được tách ra để code ứng dụng cũ và mới có thể chạy cùng
lúc — điều luôn xảy ra trong một lần rolling deploy.

Tách `users.name` thành `first_name` / `last_name`:

1. **Expand.** Thêm các cột mới, cho phép null. Deploy. Giờ schema hỗ trợ cả hai hình dạng.
2. **Dual-write.** Ứng dụng ghi cả cũ lẫn mới. Deploy. Chưa có gì đọc các cột mới.
3. **Backfill.** Điền các cột mới cho những row đã có, theo từng đợt (bên dưới).
4. **Chuyển việc đọc.** Chuyển lượt đọc sang các cột mới. Deploy. Kiểm chứng.
5. **Contract.** Ngừng ghi cột cũ. Deploy. Rồi drop nó, ở một bản release sau.

Chậm và chẳng hào nhoáng gì, nhưng đó là cách duy nhất sống sót qua rolling deploy và cho phép rollback
ở mọi bước. Lỗi thường gặp là dồn bước 4 và 5 vào một lần deploy: lượt đọc chuyển sang một cột mà phiên
bản trước không điền, và rollback sẽ làm mất dữ liệu được ghi trong khoảng giữa.

**Đặt việc drop ở một bản release riêng, sau bản release cuối cùng còn ghi vào cột.** Một khi cột đã
mất, đường rollback cũng mất theo.

## Backfill

Đừng bao giờ `UPDATE` cả một bảng lớn trong một câu lệnh. Nó giữ lock trên mọi row nó chạm tới, sinh ra
một lượng WAL khổng lồ, chặn vacuum trong suốt thời gian chạy, và — nếu thất bại ở 90% — rollback toàn
bộ.

Chia đợt, commit giữa các đợt:

```sql
-- Repeat until zero rows affected. Keyset, not OFFSET.
UPDATE orders SET region = derive_region(shipping_country)
WHERE id IN (
  SELECT id FROM orders
  WHERE region IS NULL
  ORDER BY id
  LIMIT 5000
);
```

- Chạy nó **bên ngoài** schema migration, như một job riêng. Thay đổi schema nên nhanh; thay đổi dữ
  liệu là công việc chạy lâu với đặc tính thất bại khác hẳn.
- Làm cho nó có thể tiếp tục lại và idempotent — điều kiện `WHERE region IS NULL` làm được cả hai ở
  đây.
- Index điều kiện mà backfill dùng để scan, nếu không mỗi đợt sẽ thoái hóa thành một lần scan toàn bảng.
  Một partial index trên `WHERE region IS NULL` là lý tưởng: nó co lại dần khi backfill tiến triển.
- Tạm dừng giữa các đợt khi đang có tải, và theo dõi replication lag — một lần backfill nhanh có thể đẩy
  read replica tụt lại hàng phút, bản thân điều đó đã là một sự cố mà người dùng nhìn thấy.

## Dây an toàn

Đặt những thứ này cho mọi session migration:

```sql
SET lock_timeout = '3s';        -- fail fast rather than queueing behind a slow query
SET statement_timeout = '30s';  -- bound the statement itself
```

Với `lock_timeout`, một migration không lấy được lock kịp thời sẽ **thất bại thay vì chặn cả bảng**.
Hãy retry nó trong một vòng lặp; một lần chạy migration thất bại chẳng là gì, một bảng bị khóa là một
sự cố. Chỉ riêng thiết lập này đã ngăn được cách phổ biến nhất khiến migration gây sập dịch vụ.

Cũng đáng có: một quy tắc rằng migration không deploy cùng lúc với một query analytics chạy lâu, và một
bước kiểm tra các transaction dài đang mở trước khi bắt đầu.

## Checklist review

- [ ] Mọi câu lệnh DDL đã được phân loại theo hành vi ghi lại bảng/lock của engine ở đúng phiên bản đang dùng
- [ ] Đã đặt `lock_timeout` và `statement_timeout` cho session migration
- [ ] `CREATE INDEX` dùng `CONCURRENTLY`, trong một migration riêng không có transaction
- [ ] `NOT NULL` và foreign key được thêm qua `NOT VALID` rồi `VALIDATE`, không phải trong một bước chặn duy nhất
- [ ] Backfill tách khỏi thay đổi schema, chia đợt, tiếp tục lại được, và có index trên điều kiện của nó
- [ ] Việc chia đợt backfill có theo dõi replication lag
- [ ] Thay đổi không tương thích đi theo expand/contract; cả code cũ lẫn mới đều chạy được với schema
- [ ] Drop và đổi tên cột được phát hành ở bản release sau bản thay đổi code đã ngừng dùng chúng
- [ ] Có thể rollback ở mọi bước trung gian
- [ ] Migration đã được đo thời gian trên dữ liệu cỡ production, không phải database dev

---

*Bản dịch tiếng Việt của bài học cùng tên. Được viết từ thực tiễn đã được kiểm chứng. Series Database
Internals của TechCraft ([collection](https://www.patreon.com/collection/2115129)) gợi ra mảng chủ đề
này nhưng không đề cập tới schema migration — trong 20 phần của nó, không phần nào bàn về DDL khi đang
có tải, nên không có gì ở đây được lấy từ series đó.*
