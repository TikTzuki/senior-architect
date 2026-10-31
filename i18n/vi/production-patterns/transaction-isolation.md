---
source_sha256: bb1d791513416a66b3adb20c2b1e768e6e5b7c4eca7cb4d7ba906e021b239e9d
---

# Transaction Isolation

**Quy tắc: một thao tác đọc-sửa-ghi bị tách thành hai câu lệnh là một race condition, trừ khi bạn
đã chủ động bảo database ngăn nó.** Bọc nó trong một transaction thì không đủ.

Thuộc nhóm transaction:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
**isolation** (trang này) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transaction](distributed-transactions.md) ·
[ranh giới nhất quán](consistency-boundaries.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Vì sao nó lọt qua code review](#why-it-survives-code-review)
- [Mỗi isolation level vẫn cho phép những gì](#what-each-isolation-level-still-permits)
- [Bốn anomaly, cụ thể](#the-four-anomalies-concretely)
- [Cách sửa, theo thứ tự ưu tiên](#fixes-in-order-of-preference)
- [Ba cách sửa không hiệu quả](#three-fixes-that-do-not-work)
- [Retry là bắt buộc](#retry-is-not-optional)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
with db.transaction():
    account = db.query("SELECT balance FROM accounts WHERE id = ?", id)  # reads 100
    if account.balance >= amount:  # checks 100 >= 80
        db.execute("UPDATE accounts SET balance = ? WHERE id = ?",
                   account.balance - amount, id)  # writes 20
```

Hai lệnh rút 80 chạy đồng thời trên số dư 100: cả hai đều đọc ra 100, cả hai đều qua bước kiểm
tra, cả hai đều ghi 20. Tài khoản đã chi ra 160 mà vẫn hiển thị 20.

Transaction là thật và nó đã commit. Transaction mang lại atomicity — hoặc tất cả, hoặc không gì
cả — chứ không mang lại loại trừ lẫn nhau. Ở isolation level mặc định, không có gì ở đây buộc hai
bên gọi phải chạy tuần tự.

Hình dạng cần nhận ra khi review: **đọc một giá trị, ra quyết định trong code ứng dụng, rồi ghi một
giá trị suy ra từ thứ vừa đọc.** Kiểm tra-rồi-insert, kiểm tra-rồi-update, tăng bộ đếm, giữ chỗ nếu
còn, cấp số kế tiếp — tất cả đều là pattern này.

## Vì sao môi trường dev che giấu lỗi này

Request đến lần lượt từng cái một. Không có transaction thứ hai nào để xen vào, nên mọi thao tác
đọc-sửa-ghi đều tuần tự một cách hiển nhiên và test nào cũng pass. Bug này cần hai bên gọi lọt vào
cùng một khe thời gian — rộng cỡ micro giây — điều rất thường gặp với traffic production nhưng gần
như không thể tái hiện bằng tay.

Nó cũng lọt qua code review, vì đọc từ trên xuống dưới thì code đúng. Lỗi nằm ở chỗ một transaction
*khác* có thể làm gì giữa dòng 2 và dòng 4, và điều đó không nhìn thấy được tại chỗ có bug.

## Vì sao nó lọt qua code review

Đáng tách riêng khỏi "môi trường dev che giấu lỗi", vì loại bug này vượt qua mọi cửa kiểm soát mà
một team thường tin tưởng:

- **Nó qua được code review.** Đọc từ trên xuống, code đúng. Lỗi nằm ở việc một transaction *khác*
  có thể làm gì giữa hai dòng của nó — thứ không nhìn thấy tại chỗ có bug.
- **Nó qua được QA và test tự động.** Bộ test kiểm tra kết quả của các thao tác chạy tuần tự. Muốn
  tái hiện một lost update cần hai bên gọi rơi vào cùng một khe rộng vài micro giây, điều mà hầu như
  không test harness nào dựng ra.
- **Nó không sinh ra lỗi nào.** Không exception, không request thất bại, không dòng log. Lần ghi bị
  mất đơn giản là không còn ở đó.

Vì vậy chỗ duy nhất bắt được nó là design review, bằng cách nhận ra *hình dạng* — đọc một giá trị, ra
quyết định trong code ứng dụng, ghi một giá trị suy ra từ thứ đã đọc.

## Mỗi isolation level vẫn cho phép những gì

| Level            | Dirty read | Non-repeatable read | Phantom | Lost update | Write skew |
|------------------|------------|---------------------|---------|-------------|------------|
| READ UNCOMMITTED | có¹        | có                  | có      | có          | có         |
| READ COMMITTED   | không      | có                  | có      | có          | có         |
| REPEATABLE READ  | không      | không               | không²  | không³      | có         |
| SERIALIZABLE     | không      | không               | không   | không       | không      |

¹ PostgreSQL không có READ UNCOMMITTED thật sự; nó hoạt động như READ COMMITTED.
² Chuẩn SQL cho phép phantom ở REPEATABLE READ. PostgreSQL và MySQL InnoDB đều ngăn được, lần lượt
bằng snapshot isolation và next-key locking.
³ Được ngăn *bên trong* database — một lần ghi xung đột sẽ bị abort hoặc bị chặn chờ. Một thao tác
đọc-sửa-ghi ở tầng ứng dụng trải qua hai câu lệnh vẫn có thể mất update ở READ COMMITTED.

**Mặc định mỗi engine một khác, và đây là một cái bẫy thật khi chuyển đổi giữa các engine:**

- PostgreSQL, Oracle, SQL Server: **READ COMMITTED**
- MySQL InnoDB: **REPEATABLE READ**

Code chạy đúng trên MySQL có thể sai trên PostgreSQL mà nhìn vào code không thấy lý do gì. Hãy kiểm
tra connection của bạn thực sự dùng level nào; ORM và pooler đôi khi cũng tự đặt nó.

## Bốn anomaly, cụ thể

**Lost update.** Hai transaction cùng đọc một dòng, cả hai tính giá trị mới từ nó, cả hai cùng ghi.
Lần ghi thứ hai đè lên lần thứ nhất, và lần thứ nhất biến mất mà không có lỗi nào. Chính là ví dụ rút
tiền ở trên.

**Non-repeatable read.** Một transaction đọc một dòng hai lần và nhận được hai giá trị khác nhau, vì
một transaction khác đã commit xen giữa. Ở READ COMMITTED, mỗi *câu lệnh* nhận một snapshot mới, nên
một transaction dài đọc một dòng, làm việc gì đó, rồi đọc lại có thể thấy dòng đó đổi ngay dưới chân
mình.

**Phantom read.** Một transaction chạy một range query hai lần và lần thứ hai trả về những dòng trước
đó chưa tồn tại. `SELECT COUNT(*) FROM bookings WHERE room = 5 AND day = '...'` trả về 0, rồi 1, sẽ
phá vỡ mọi logic "chỉ insert nếu chưa có" dựng trên nó.

Điểm khác biệt quan trọng: các anomaly kia nói về một *dòng*, còn phantom nói về một *tập hợp*. Phần
lớn invariant nghiệp vụ ở mức tập hợp ("không có booking chồng lấn", "bán tối đa N ghế"), nên lock các
dòng bạn đã đọc không bảo vệ được chúng — dòng gây ra vi phạm còn chưa tồn tại. Mỗi engine ngăn phantom
theo một cách: PostgreSQL bằng snapshot isolation (và SSI ở SERIALIZABLE), MySQL InnoDB bằng **gap lock
và next-key lock**, tức là lock cả *khoảng* chứ không chỉ các dòng đang có. Đó cũng là lý do deadlock
của InnoDB đôi khi dính tới những dòng mà câu lệnh chưa hề nhắc tới.

**Write skew.** Anomaly tinh vi nhất, và là lý do SERIALIZABLE tồn tại. Hai transaction đọc một tập
hợp chồng lấn nhau, mỗi bên kiểm tra một invariant hiện đang đúng, mỗi bên ghi vào một dòng *khác
nhau*. Không lần ghi nào xung đột, cả hai đều commit, và gộp lại thì chúng phá vỡ invariant.

> Có hai bác sĩ đang trực. Mỗi người độc lập xin nghỉ. Mỗi transaction đọc được
> `COUNT(on_call) = 2`, xác nhận "vẫn còn ít nhất một người", rồi update dòng của chính mình. Cả hai
> đều commit. Không còn bác sĩ nào trực.

Không dòng nào bị ghi hai lần, nên không có gì phát hiện ra xung đột. **REPEATABLE READ không ngăn được
chuyện này.** Chỉ SERIALIZABLE, hoặc một lock tường minh trên những dòng mà quyết định dựa vào, mới
ngăn được.

## Cách sửa, theo thứ tự ưu tiên

**1. Gộp thành một câu lệnh atomic.** Cách sửa tốt nhất xóa hẳn khe thời gian bằng cách để database
tự đọc và ghi cùng lúc:

```sql
UPDATE accounts
SET balance = balance - :amount
WHERE id = :id
  AND balance >= :amount;
```

Sau đó **kiểm tra số dòng bị ảnh hưởng** — bằng 0 nghĩa là không đủ tiền. Cách này đúng ở mọi
isolation level và không cần lock hay retry. Phần lớn các race đọc-sửa-ghi đều thu gọn được về dạng
này.

**2. Để constraint đảm bảo.** Một index `UNIQUE` khiến race insert trùng lặp không thể xảy ra; bên
thua nhận một lỗi vi phạm constraint để xử lý. `CHECK (balance >= 0)` khiến việc thấu chi không thể
biểu diễn được. Constraint `EXCLUDE` của PostgreSQL kết hợp range type ngăn đặt chỗ trùng trên các
khoảng thời gian chồng lấn. Constraint luôn giữ được bất kể code path nào ghi, kể cả script migration
lẫn lần sửa tay lúc 2 giờ sáng — đó mới là lợi thế thật của nó so với kiểm tra trong ứng dụng.

**3. Lock các dòng bạn đọc.** Khi quyết định thực sự cần logic ứng dụng:

```sql
SELECT balance
FROM accounts
WHERE id = :id FOR UPDATE;
```

Các transaction khác bị chặn chờ cho tới khi bạn commit. Đúng, nhưng tuần tự hóa mọi truy cập vào dòng
đó, và giữ lock suốt phần còn lại của transaction — nên hãy giữ transaction ngắn. `FOR UPDATE` trên một
khoảng **không** ngăn được phantom trong trường hợp tổng quát; dòng mới vẫn có thể xuất hiện. Với write
skew, bạn phải lock những dòng mà *quyết định* dựa vào, và chỗ này rất dễ làm sai.

**4. Optimistic concurrency.** Tốt hơn khi tranh chấp thấp, vì không có gì bị chặn. Việc chọn giữa
cách này và lock, cùng failure mode retry storm mà nó mang theo, được trình bày trong
[locking](optimistic-vs-pessimistic-locking.md):

```sql
UPDATE documents
SET content = :content,
    version = version + 1
WHERE id = :id
  AND version = :expected_version;
```

Không dòng nào bị ảnh hưởng nghĩa là người khác đã thắng; tải lại rồi retry, hoặc báo xung đột lên.
Đây cũng là cách trung thực để xử lý trường hợp "người dùng sửa trên một form đã cũ".

**5. SERIALIZABLE.** Thứ duy nhất ngăn được write skew mà không cần tự tay đặt lock. SSI của
PostgreSQL phát hiện xung đột lúc commit và abort một transaction. Đúng ngay từ cách xây dựng — nhưng
nó *bắt buộc* phải có vòng retry bên dưới, và nó không miễn phí khi tranh chấp cao.

## Ba cách sửa không hiệu quả

Cả ba thường xuyên được đề xuất và không cách nào ngăn được lost update:

**"Cứ bọc nó trong một transaction."** Transaction cho atomicity, không cho loại trừ lẫn nhau. Cả hai
transaction đang race đều commit thành công — đó chính xác là vấn đề.

**"Database sẽ lo."** Ở level mặc định thì không. PostgreSQL, Oracle và SQL Server mặc định READ
COMMITTED, nơi một thao tác đọc-sửa-ghi ở tầng ứng dụng có thể mất update mà không báo lỗi. Database
đang hoạt động đúng như đặc tả.

**"Tôi sẽ dùng lock ở tầng ứng dụng."** Một khối `synchronized`, một đối tượng `Lock`, hay một mutex
cục bộ trong process chỉ tuần tự hóa các bên gọi *trong cùng một process*. Chạy hai instance sau một
load balancer — điều mà mọi bản deploy production đều làm — và đảm bảo đó bốc hơi, trong khi code vẫn
trông như được bảo vệ. Cách này đặc biệt nguy hiểm vì nó chạy ổn trên staging chỉ có một instance.

Nếu bạn cần một lock chứ không phải constraint hay câu lệnh atomic, lock đó phải nằm trong database
hoặc một dịch vụ distributed lock — xem [locking](optimistic-vs-pessimistic-locking.md) và
[distributed-locks](distributed-locks.md).

## Retry là bắt buộc

Ở REPEATABLE READ và SERIALIZABLE, database sẽ abort transaction để giữ tính đúng đắn. Đây là hoạt
động bình thường, không phải sự cố. Code không retry sẽ biến một cơ chế đảm bảo tính đúng thành lỗi 500
mà người dùng nhìn thấy.

Retry khi gặp:

- `40001` serialization_failure
- `40P01` deadlock_detected

với số lần thử có giới hạn và backoff có jitter. **Phải chạy lại toàn bộ transaction**, kể cả các lần
đọc — chỉ retry câu lệnh bị lỗi là áp dụng lại một quyết định được đưa ra từ một snapshot đã mất hiệu
lực, đúng cái bug mà lần abort vừa ngăn.

Chỉ retry những mã này. Lỗi vi phạm constraint hay lỗi cú pháp sẽ thất bại y hệt mãi mãi.

## Checklist review

- [ ] Không có thao tác đọc-sửa-ghi nào tách qua nhiều câu lệnh mà không có lock, kiểm tra version, hoặc SERIALIZABLE
- [ ] Update có điều kiện dùng điều kiện `WHERE` và kiểm tra số dòng bị ảnh hưởng
- [ ] Các invariant phải luôn đúng được database constraint đảm bảo, không chỉ nằm trong code
- [ ] Tính duy nhất dựa vào index `UNIQUE`, không dựa vào một câu `SELECT` kiểm tra tồn tại trước đó
- [ ] Isolation level được biết rõ và khẳng định tường minh, không vô tình kế thừa
- [ ] Serialization failure và deadlock được retry, với toàn bộ transaction chạy lại
- [ ] Retry có giới hạn và có jitter; lỗi không retry được thì không retry
- [ ] Invariant trên nhiều dòng ("ít nhất một", "không chồng lấn") được giữ bằng SERIALIZABLE, constraint
  `EXCLUDE`, hoặc lock tường minh — không phải bằng một phép kiểm tra `COUNT(*)`
- [ ] Transaction giữ lock trong thời gian ngắn nhất có thể
- [ ] Không dựa vào lock cục bộ trong process (`synchronized`, mutex) cho tính đúng giữa các request
- [ ] Invariant ở mức tập hợp được bảo vệ bằng range/predicate locking, SERIALIZABLE, hoặc constraint

---

*Bản dịch tiếng Việt của bài học cùng tên. Ban đầu được viết từ thực tiễn đã được kiểm chứng, sau đó
đối chiếu với series Transaction & Consistency của TechCraft, phần P5 và P7–P11
([collection](https://www.patreon.com/collection/2256242)). Cách đặt vấn đề "lọt qua code review", ba
cách sửa không hiệu quả, và sự phân biệt dòng với tập hợp đối với phantom đến từ series đó; các bảng
anomaly, thứ tự cách sửa và checklist là của repository này.*
