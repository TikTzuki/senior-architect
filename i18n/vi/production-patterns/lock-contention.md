---
source_sha256: d7eb463f783d2db37659e42ebad02d0ef4f4158827bda270fcad3138468fa0b3
---

# Lock, deadlock & transaction dài

**Quy tắc: một transaction giữ các lock của nó cho đến khi commit, nên cách đáng tin cậy duy nhất để
giảm tranh chấp là giữ cho transaction thật ngắn.** Đừng bao giờ chờ mạng bên trong một transaction.

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Nó hỏng thế nào trên production](#how-it-fails-in-production)
- [Deadlock: lúc nào cũng cùng một nguyên nhân](#deadlock-always-the-same-cause)
- [Giữ transaction ngắn](#keeping-transactions-short)
- [Idle in transaction, và vì sao nó tệ hơn vẻ ngoài](#idle-in-transaction-and-why-it-is-worse-than-it-looks)
- [Chẩn đoán](#diagnosing)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
with db.transaction():
    order = db.query("SELECT * FROM orders WHERE id = ? FOR UPDATE", order_id)
    charge = payment_gateway.charge(order.total)  # network call, 200ms - 30s
    db.execute("UPDATE orders SET status='paid', charge_id=? WHERE id=?",
               charge.id, order_id)
    email.send_receipt(order.customer_email)  # another network call
```

Lock trên row được lấy ở dòng 2 và nhả ra lúc commit. Mọi thứ ở giữa — kể cả hai lời gọi tới những hệ
thống bạn không kiểm soát — đều là thời gian lock bị giữ. Khi payment gateway chậm, transaction này
giữ một lock của database lâu đúng bằng thời gian gateway trả lời.

Hình dạng liên quan: **bất kỳ transaction nào có thời lượng phụ thuộc vào thứ gì đó khác ngoài
database.** Lời gọi HTTP, publish lên queue, upload S3, `sleep`, chờ người dùng, hoặc một vòng lặp qua
một tập lớn mà mỗi phần tử tốn một round trip.

## Vì sao môi trường dev che giấu lỗi này

Mỗi lúc chỉ có một transaction nghĩa là không ai phải chờ sau lock của bạn, nên thời gian giữ lock vô
hình — nó hiện ra dưới dạng độ trễ mà bạn vốn đã đổ cho payment gateway. Một gateway được stub ở local
trả về trong một mili giây, ép cửa sổ nguy hiểm xuống gần như bằng không.

Tranh chấp tăng nhanh hơn tuyến tính theo mức đồng thời. Ở một request mỗi giây thì không đo được; ở vài
trăm request thì thành sự cố. Không có cảnh báo nhẹ nhàng nào ở giữa.

## Nó hỏng thế nào trên production

- **Xếp hàng sau một bên giữ lock chậm duy nhất.** Trong PostgreSQL, việc chờ lock theo thứ tự FIFO.
  Một transaction giữ lock 30 giây không chỉ làm chậm một request khác — nó làm đứng mọi request muốn
  chạm row đó, và chúng dồn lại. Metric nhúc nhích là p99 latency trên một endpoint trông chẳng liên
  quan.
- **Cạn connection pool, ngay bước sau đó.** Mỗi request bị chặn giữ connection lấy từ pool trong lúc
  chờ. Pool cạn dần, và những request chưa từng chạm tới row đang bị tranh chấp bắt đầu không lấy được
  connection nào. Triệu chứng được báo lên là "database sập"; nguyên nhân là một bên giữ lock chậm.
- **Deadlock khi có tải.** Hai thứ tự lấy lock chưa từng gặp nhau khi test sẽ gặp nhau khi chạy đồng
  thời.
- **Một query bị chặn trông như một query chậm.** Thời gian chờ lock là thời gian thực, nên nó rơi vào
  slow query log như một vấn đề hiệu năng. Các team thêm index và viết lại SQL hàng ngày trời mà không
  chạm tới nguyên nhân.
- **DDL khuếch đại nó một cách thảm khốc.** Một lệnh `ALTER TABLE` đang chờ lock sẽ xếp hàng sau bên
  đang giữ — và mọi lượt đọc đến sau lại xếp hàng sau `ALTER`, vì các yêu cầu lock không vượt mặt nhau.
  Một transaction dài cộng một migration là đủ làm sập mọi lượt đọc trên bảng đó.

## Deadlock không phải là bug

Đáng nói thẳng ra, vì các team hay coi deadlock là một khiếm khuyết phải loại bỏ: **deadlock là cái giá
của việc chạy đồng thời, và việc database tự giải quyết nó là hành vi đúng.** Engine phát hiện vòng chờ
và kill một transaction chính là để transaction kia đi tiếp được — không có việc đó, cả hai sẽ chờ mãi
mãi.

Vậy nên mục tiêu không phải là không có deadlock nào. Mục tiêu là (a) giảm tần suất của chúng bằng cách
sắp thứ tự lấy lock, và (b) **luôn retry transaction bị chọn làm nạn nhân**, vì database đã rollback nó
sạch sẽ rồi. Code đẩy `40P01` lên người dùng như một lỗi là đang coi một sự kiện có thể vượt qua thành
một thất bại.

## Deadlock: lúc nào cũng cùng một nguyên nhân

Hai transaction lấy cùng các lock theo thứ tự ngược nhau:

```
T1: lock account A ──▶ wants B
T2: lock account B ──▶ wants A     both wait forever; the database kills one
```

Mọi deadlock đều là thế này, dù được khoác lên vẻ ngoài nào. Cách sửa vì vậy cũng đơn giản tương ứng:

**Lấy lock theo một thứ tự nhất quán, được định nghĩa toàn cục.** Với một lần chuyển tiền giữa hai tài
khoản, hãy lock ID nhỏ hơn trước bất kể bên nào là người gửi:

```python
first, second = sorted([from_id, to_id])
db.execute("SELECT ... FROM accounts WHERE id IN (?,?) ORDER BY id FOR UPDATE", first, second)
```

Những nguồn vô tình tạo ra thứ tự không nhất quán đáng kiểm tra khi review:

- Một lệnh `UPDATE ... WHERE id IN (...)` nhiều row — thứ tự row đi theo query plan, không theo danh
  sách của bạn. Thêm `ORDER BY` vào một `SELECT ... FOR UPDATE` chạy trước để cố định nó.
- Foreign key lấy lock trên row cha mà bạn không hề nhắc tới.
- Một trigger chạm vào bảng thứ hai mà bạn không biết.
- Hai luồng code cập nhật cùng một cặp bảng theo thứ tự ngược nhau.

Deadlock không thể loại bỏ hoàn toàn, nên **dù thế nào cũng retry khi gặp `40P01`** — database đã
rollback một transaction rồi, và chạy lại nó thường sẽ thành công.

## Giữ transaction ngắn

Quy tắc giá trị nhất: **làm phần việc chậm ở bên ngoài, phần việc database ở bên trong.**

```python
charge = payment_gateway.charge(order_total)  # outside: slow, no locks held

with db.transaction():  # inside: fast, deterministic
    db.execute("UPDATE orders SET status='paid', charge_id=? WHERE id=? AND status='pending'",
               charge.id, order_id)

queue.publish("send_receipt", order_id)  # outside: after commit
```

Việc này đặt ra câu hỏi mà code ban đầu đã né: nếu charge thành công mà update thất bại thì sao? Đó là
một bài toán hệ phân tán thật sự, và các câu trả lời trung thực là một idempotency key trên lần charge
cộng một job đối soát, hoặc outbox pattern — không phải giữ lock database xuyên qua lời gọi gateway.
Lock chưa bao giờ làm cho nó atomic; nó chỉ làm cho nó chậm.

Ngoài ra:

- **Publish lên queue sau khi commit, không phải bên trong.** Một message được publish bên trong một
  transaction mà sau đó bị rollback là đã được gửi cho một sự kiện không hề xảy ra. Consumer cũng thường
  đủ nhanh để nhận nó trước cả khi transaction commit.
- **Đặt `lock_timeout` và `statement_timeout`.** Một thất bại có giới hạn tốt hơn một lần treo không
  giới hạn. Dùng `SELECT ... FOR UPDATE NOWAIT` hoặc `SKIP LOCKED` khi workload cho phép — `SKIP LOCKED`
  là cách chuẩn để xây một bảng hàng đợi mà các worker không chặn lẫn nhau.
- **Chia nhỏ các update hàng loạt.** Một transaction cập nhật một triệu row giữ một triệu lock và chặn
  mọi thứ. Chia thành từng đợt, commit giữa các đợt.
- **Không giữ transaction xuyên qua một tương tác của người dùng.** Không bao giờ.

## Idle in transaction, và vì sao nó tệ hơn vẻ ngoài

Một connection đã bắt đầu một transaction rồi không làm gì là trạng thái tệ nhất trong hệ thống. Nó giữ
mọi lock đã lấy *và* ghim mốc snapshot của MVCC.

Vế thứ hai mới là vế khiến người ta bất ngờ. PostgreSQL không thể vacuum những phiên bản row mà bất kỳ
transaction nào đang mở vẫn còn nhìn thấy. Một connection idle in transaction hàng giờ sẽ ngăn việc dọn
dead tuple trên **toàn bộ database**, không chỉ những bảng nó đã chạm vào. Bảng phình ra, thống kê của
planner trôi lệch, sequential scan chậm dần, và đĩa đầy lên — mà không có lock contention nào hiện ra ở
đâu để giải thích.

Các nguyên nhân thường gặp: một ORM mở transaction ở query đầu tiên và giữ nó suốt request; một nhánh
xử lý exception bỏ qua rollback; một connection được trả về pool mà không commit hay rollback; một
session tương tác ai đó bỏ quên đang mở.

Hãy đặt `idle_in_transaction_session_timeout` và để database kill những connection này. Đó là dây an
toàn, không phải cách sửa, nhưng nó biến một sự xuống cấp chậm rãi trên toàn database thành một thất bại
ồn ào duy nhất.

## Chẩn đoán

```sql
-- PostgreSQL: who is blocking whom, right now
SELECT pid,
       state,
       wait_event_type,
       wait_event,
       now() - xact_start AS txn_age,
       left(query, 80)
FROM pg_stat_activity
WHERE state <> 'idle'
ORDER BY xact_age DESC NULLS LAST;

-- The blocking chain
SELECT pid, pg_blocking_pids(pid), left(query, 60)
FROM pg_stat_activity
WHERE cardinality(pg_blocking_pids(pid)) > 0;
```

Join `pg_locks` với `pg_stat_activity` cho bạn bức tranh đầy đủ. Trên MySQL thì dùng
`performance_schema.data_lock_waits` và `SHOW ENGINE INNODB STATUS` — lệnh sau in ra deadlock gần nhất
cùng tập lock của cả hai transaction, thường là đủ để nhận ra vấn đề thứ tự.

**Đặt alert theo tuổi của transaction**, không chỉ theo thời lượng query. Transaction chạy lâu nhất là
chỉ báo sớm cho cả tranh chấp lẫn bloat, và chỉ tốn một query rẻ.

## Checklist review

- [ ] Không có lời gọi HTTP, publish lên queue, upload file, sleep hay tương tác người dùng nào bên trong transaction
- [ ] Lock trên nhiều row được lấy theo thứ tự xác định (key đã sắp xếp, `ORDER BY` tường minh)
- [ ] Deadlock (`40P01`) được retry bằng cách chạy lại toàn bộ transaction
- [ ] Đã đặt `lock_timeout` và `statement_timeout`; cả `idle_in_transaction_session_timeout` nữa
- [ ] Update hàng loạt được chia đợt, commit giữa các đợt
- [ ] Publish lên queue diễn ra sau commit, hoặc qua outbox
- [ ] Transaction được mở càng muộn và đóng càng sớm càng tốt
- [ ] Mọi nhánh — kể cả exception — đều commit hoặc rollback trước khi trả connection
- [ ] Bảng worker/queue dùng `FOR UPDATE SKIP LOCKED` thay vì chặn nhau
- [ ] Tuổi của transaction chạy lâu được giám sát và có alert

---

*Bản dịch tiếng Việt của bài học cùng tên. Ban đầu được viết từ thực tiễn đã được kiểm chứng, sau đó
đối chiếu với series Database Internals của TechCraft, các phần P6, P9 và P17
([collection](https://www.patreon.com/collection/2115129)). Cách nhìn "deadlock là cái giá của việc chạy
đồng thời, không phải bug" đến từ series đó; MVCC bloat và vacuum được mở rộng trong
[storage-engines](storage-engines.md). Phần chẩn đoán và checklist là của repository này.*
