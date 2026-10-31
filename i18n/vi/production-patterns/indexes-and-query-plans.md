---
source_sha256: 62549cfcb6c652e2782533832dfddbb3a15d34e5caa75a1391d093cba2dcb8e9
---

# Index & query plan

**Quy tắc: một index tồn tại không có nghĩa là một index được dùng.** Query chạy nhanh ở môi trường dev
là vì bảng nhỏ, không phải vì query plan tốt.

Thuộc nhóm storage: [storage engine](storage-engines.md) ·
**index & query plan** (trang này) · [replication & sharding](replication-and-sharding.md) · [bố trí replica & quorum](replica-placement-and-quorums.md) ·
[session consistency](session-consistency.md) · [connection pool & độ trễ](connection-pools-and-latency.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Vì sao một index có sẵn lại không được dùng](#why-an-existing-index-goes-unused)
- [Composite index và quy tắc cột dẫn đầu](#composite-indexes-and-the-leading-column-rule)
- [Query N+1](#n1-queries)
- [Đọc EXPLAIN](#reading-explain)
- [Cái giá của một index](#the-cost-of-an-index)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
-- The index exists.
CREATE INDEX idx_users_email ON users (email);

-- None of these can use it.
SELECT * FROM users WHERE lower(email) = 'a@b.com';       -- function on the column
SELECT * FROM users WHERE email LIKE '%@b.com';           -- leading wildcard
SELECT * FROM users WHERE email::text = 'a@b.com';        -- type coercion
```

Câu nào cũng rơi về sequential scan. Index có đó, query trả kết quả đúng, và không có chỗ nào báo vấn
đề — query chỉ đơn giản là lần nào cũng đọc cả bảng.

## Vì sao môi trường dev che giấu lỗi này

Một sequential scan qua 200 row mất chưa tới một mili giây. Cũng lần scan đó qua 20 triệu row mất vài
giây và làm bão hòa I/O đĩa. **Query plan giống hệt; chỉ có số row là thay đổi.**

Tệ hơn, planner chọn khác nhau ở các kích thước bảng khác nhau. Dưới vài nghìn row, sequential scan
*thực sự* nhanh hơn tra index, nên PostgreSQL bỏ qua index của bạn ở môi trường dev là hoàn toàn đúng.
Bạn không thể phân biệt "planner đang hành xử hợp lý với một bảng nhỏ" với "index này không bao giờ dùng
được" chỉ bằng cách quan sát hành vi ở local. Chỉ có `EXPLAIN` trên dữ liệu giống production mới phân
biệt được hai trường hợp.

Đó là lý do một query có thể qua review, qua CI, qua staging, rồi làm sập production vào đúng ngày một
bảng vượt qua một ngưỡng mà không ai để mắt tới.

## Index thực chất là gì

Không phải một cấu trúc để tìm row — mà là **một công cụ để giảm số page phải đọc từ đĩa.** Engine đọc
các page có kích thước cố định, không bao giờ đọc từng row riêng lẻ, nên thứ duy nhất một index thay đổi
là số page. Xem [storage-engines](storage-engines.md).

Cách nhìn đó giải thích các hành vi bên dưới. Sequential scan trên bảng nhỏ là *đúng*, vì đọc liền một
mạch vài page thắng đọc ngẫu nhiên từng page đơn lẻ. Covering index nhanh vì nó trả lời từ các page của
index và không bao giờ chạm tới bảng. Và "index không được dùng" thường là do planner ước tính rằng dùng
index sẽ tốn nhiều page hơn scan.

## Vì sao một index có sẵn lại không được dùng

**Một hàm hoặc biểu thức bọc quanh cột.** `WHERE lower(email) = ?` không thể dùng index trên `email`.
Hoặc là index chính biểu thức đó — `CREATE INDEX ON users (lower(email))` — hoặc lưu giá trị đã chuẩn
hóa vào một cột riêng. Điều tương tự áp dụng cho `date(created_at)`, `CAST(...)` và phép nối chuỗi.

**Ép kiểu ngầm định.** So sánh một cột `varchar` với một tham số số nguyên, hoặc một cột `bigint` với
một literal numeric, có thể buộc phải ép kiểu ở phía cột và vô hiệu hóa index. Lỗi này gần như vô hình
khi review vì câu SQL trông sạch sẽ; nó hiện ra trong `EXPLAIN` dưới dạng một filter thay vì một điều
kiện index. Hay gặp với các ID được client JSON gửi lên dưới dạng chuỗi.

**Wildcard ở đầu trong LIKE.** `LIKE 'prefix%'` dùng được B-tree; `LIKE '%suffix'` thì không, vì B-tree
được sắp theo tiền tố. Với tìm kiếm chuỗi con hoặc tìm gần đúng, hãy dùng trigram index (`pg_trgm`) hoặc
một full-text index thực thụ.

**`OR` trên các cột khác nhau.** `WHERE a = 1 OR b = 2` thường không dùng được index trên cột nào cả.
Viết lại thành `UNION` của hai query có index thường nhanh hơn rất nhiều.

**Độ chọn lọc thấp.** Một index trên cột boolean, hoặc trên cột status mà 95% số row cùng một giá trị,
sẽ bị bỏ qua với giá trị phổ biến — hoàn toàn đúng, vì đọc phần lớn bảng qua các lần tra index ngẫu nhiên
chậm hơn scan cả bảng. **Partial index** sửa đúng chỗ này:
`CREATE INDEX ON jobs (created_at) WHERE status = 'pending'` nhỏ, nóng, và luôn được query quan trọng
sử dụng.

**Thống kê cũ.** Planner chọn dựa trên ước tính. Sau một lần nạp hàng loạt hoặc một lần xóa lớn, ước
tính có thể sai lệch rất xa, sinh ra một plan tốt cho một hình dạng bảng không còn tồn tại. Chạy
`ANALYZE` sau các thay đổi hàng loạt.

**Ngữ nghĩa của `NULL`.** `WHERE col != 'x'` không khớp những row có `col IS NULL`, và `NOT IN` với một
`NULL` ở bất kỳ đâu trong danh sách sẽ không trả về row nào cả. Cả hai là bug về tính đúng đắn trước khi
là bug về hiệu năng.

## Planner làm việc dựa trên thống kê, không phải dữ liệu

Chuỗi quyết định plan của bạn: **thống kê → chi phí ước tính → plan được chọn.** Optimizer không bao giờ
nhìn vào dữ liệu của bạn. Nó nhìn vào một bản tóm tắt thống kê — số row, số giá trị phân biệt, các giá
trị phổ biến nhất, histogram — và chọn plan rẻ nhất *theo bản tóm tắt đó*.

Hai hệ quả đáng mang theo khi review:

- **Thống kê cũ sinh ra những plan sai một cách đầy tự tin.** Sau một lần nạp hàng loạt, một lần xóa
  lớn, hoặc một thay đổi phân bố đột ngột, bản tóm tắt mô tả một bảng không còn tồn tại. Plan tối ưu cho
  một thứ hư cấu. `ANALYZE` là cách sửa, và ngưỡng analyze của autovacuum đáng được kiểm tra trên các
  bảng thay đổi nhiều.
- **Dữ liệu lệch đánh bại một plan duy nhất.** Với `status` có 95% là `'completed'`, một plan không thể
  đúng cho cả `status = 'completed'` (scan) lẫn `status = 'pending'` (index). Extended statistics, một
  partial index trên giá trị hiếm, hoặc tăng độ phân giải histogram (`ALTER TABLE ... SET STATISTICS`)
  là các đòn bẩy. Đây cũng là lý do một query có thể nhanh suốt nhiều tháng rồi chậm bất thường với một
  giá trị tham số cụ thể.

`EXPLAIN` cho thấy ước tính; `EXPLAIN ANALYZE` cho thấy cả hai, và khoảng cách giữa chúng là đường nhanh
nhất dẫn tới vấn đề thật.

## Composite index và quy tắc cột dẫn đầu

Một index trên `(a, b, c)` được sắp theo `a`, rồi theo `b` trong cùng giá trị `a`, rồi theo `c`. Thứ tự
đó quyết định nó phục vụ được gì:

| Điều kiện trong query             | Dùng `(a, b, c)`?                                |
|-----------------------------|--------------------------------------------------|
| `a = ?`                     | có                                              |
| `a = ? AND b = ?`           | có                                              |
| `a = ? AND b = ? AND c = ?` | có, trọn vẹn                                       |
| `b = ?`                     | **không** — `b` không phải cột dẫn đầu           |
| `a = ? AND c = ?`           | một phần — seek theo `a`, lọc `c`            |
| `a > ? AND b = ?`           | chỉ seek theo `a`; một điều kiện khoảng chặn việc seek tiếp |

Từ đó suy ra hai quy tắc, và chúng bao phần lớn các lỗi về composite index:

1. **Cột so sánh bằng đặt trước, cột so sánh khoảng đặt sau cùng.** Một khi đã dùng điều kiện khoảng,
   các cột phía sau nó trong index chỉ còn lọc được, không seek được. `(status, created_at)` phục vụ
   `status = 'x' AND created_at > y`; `(created_at, status)` phục vụ nó tệ hơn nhiều.
2. **Một index riêng trên cột dẫn đầu là thừa.** Nếu đã có `(a, b)`, một index chỉ trên `(a)` chỉ thêm
   chi phí ghi mà chẳng được gì. Review tìm index *thừa* có giá trị ngang với review tìm index còn
   thiếu.

**Covering index.** Nếu index chứa mọi cột mà query cần, database trả lời chỉ từ index và không bao giờ
chạm tới bảng — một index-only scan. `INCLUDE (...)` trong PostgreSQL thêm các cột payload mà không ảnh
hưởng tới thứ tự. Đây là khác biệt giữa nhanh và rất nhanh trên các query nóng.

## Query N+1

```python
orders = db.query("SELECT * FROM orders WHERE user_id = ?", user_id)   # 1 query
for order in orders:
    order.items = db.query("SELECT * FROM items WHERE order_id = ?", order.id)   # N queries
```

Từng query riêng lẻ đều nhanh và có index đúng. Vấn đề nằm ở số lượng: 50 đơn hàng là 51 round trip.
Với độ trễ mạng 1 ms mỗi lần là 51 ms; qua một ranh giới mạng ở mức 5 ms thì là một phần tư giây, cho
một trang lẽ ra chỉ mất 10 ms.

Đây là lỗi hiệu năng database phổ biến nhất trong code ứng dụng, và ORM mặc định sinh ra nó qua lazy
loading — vòng lặp trông như truy cập thuộc tính bình thường, nên chẳng có gì trong code đọc lên giống một
query cả.

Sửa bằng cách tải trong một round trip: một chỉ thị eager-load / `JOIN FETCH`, hoặc lấy các bản ghi con
cho mọi ID cha cùng một lúc (`WHERE order_id = ANY(:ids)`) rồi gom nhóm trong bộ nhớ. Lưu ý rằng eager
loading một quan hệ to-many qua `JOIN` sẽ nhân số row — phần lớn ORM xử lý việc này bằng một query thứ
hai thay vì một join, cách đó đúng và vẫn là O(1) round trip.

Để bắt được nó: assert số lượng query trong test cho các endpoint nóng. Đó là cách đáng tin cậy duy
nhất, vì về bản chất N+1 vô hình khi code review.

Lý do nó đau hơn vẻ ngoài: chi phí nằm ở **round trip**, không ở độ phức tạp của query. Mỗi query đều có
index đàng hoàng và thực sự nhanh — lỗi là *số lượng*, nhân với độ trễ mạng. Hệ số nhân đó là một thuộc
tính của cách deploy, nên cùng một đoạn code chạy ổn trên localhost và thành thảm họa khi đi qua các
availability zone. Xem [connection-pools-and-latency](connection-pools-and-latency.md).

## Đọc EXPLAIN

Dùng `EXPLAIN (ANALYZE, BUFFERS)` — `EXPLAIN` trơn chỉ cho thấy ước tính, mà ước tính lại chính là thứ
sai khi plan tệ.

Hai thứ cần nhìn trước tiên:

1. **Số row ước tính so với thực tế.** `rows=10` với `actual rows=45000` nghĩa là planner đang làm việc
   từ một tiền đề sai, và mọi lựa chọn phía trên node đó đều đáng ngờ. Thường là do thống kê cũ, một
   tương quan mà planner không thấy được, hoặc một điều kiện nó không ước tính được.
2. **`loops=N` trên một node bên trong.** Thời gian được báo của node là *cho mỗi vòng lặp*. Một node
   0.5 ms với `loops=10000` là năm giây. Đây là chỗ công việc có hình dạng N+1 nấp bên trong một query
   duy nhất.

Sau đó: `Seq Scan` trên một bảng lớn với điều kiện lọc có độ chọn lọc cao nghĩa là thiếu index hoặc
index không dùng được. `Filter: ...` với `Rows Removed by Filter` lớn nghĩa là row đang bị đọc lên rồi
vứt đi — điều kiện không nằm trong index condition. `Nested Loop` trên một tập bên ngoài lớn thường là
một plan tệ sinh ra từ ước tính tệ.

`BUFFERS` cho thấy số page đã đọc; so sánh `shared hit` với `shared read` cho bạn biết một query chậm là
do I/O hay do plan.

## Cái giá của một index

Index không miễn phí, và những lần review chỉ biết thêm index sẽ làm hệ thống tệ đi:

- Mọi `INSERT`, `UPDATE` và `DELETE` đều phải duy trì mọi index trên bảng. Một bảng có mười hai index sẽ
  ghi chậm, và thường chẳng ai có nhiệm vụ để ý chuyện đó.
- Index chiếm dung lượng lưu trữ và cache. Một index không dùng tới sẽ đẩy ra khỏi cache những page mà
  một index đang dùng cần.
- `UPDATE` chạm vào cột có index sẽ tốn hơn; tối ưu HOT của PostgreSQL chỉ áp dụng khi không có cột
  được index nào thay đổi.

Tìm index không dùng tới trong `pg_stat_user_indexes` (`idx_scan = 0` qua một khoảng thời gian đại
diện), và drop chúng. Xem `pg_stat_statements` để biết thứ gì thực sự chạy trước khi thêm một index.

## Checklist review

- [ ] Mọi cột trong `WHERE`, `JOIN` và `ORDER BY` trên bảng lớn đều có index, hoặc cố ý không có
- [ ] Không có hàm, phép ép kiểu hay biểu thức nào bọc một cột có index trong điều kiện
- [ ] Kiểu của tham số khớp kiểu của cột — không có ép kiểu ngầm ở phía cột
- [ ] Composite index bắt đầu bằng các cột so sánh bằng; điều kiện khoảng đặt sau cùng
- [ ] Không có index thừa là tiền tố của một index khác
- [ ] Query trên một tập con chọn lọc dùng partial index thay vì một index đầy đủ có độ chọn lọc thấp
- [ ] Không có N+1: vòng lặp qua một tập kết quả không phát query; đường nóng assert số lượng query trong test
- [ ] Đã chạy `EXPLAIN (ANALYZE, BUFFERS)` trên dữ liệu giống production, không phải trên bảng dev
- [ ] Số row ước tính và thực tế trong plan gần nhau
- [ ] Query trả về nhiều row được phân trang, dùng keyset pagination thay vì `OFFSET` lớn
- [ ] `ANALYZE` chạy sau các lần nạp hàng loạt hoặc xóa lớn
- [ ] Index mới được biện minh so với chi phí ghi của nó; index không dùng tới được drop

---

*Bản dịch tiếng Việt của bài học cùng tên. Ban đầu được viết từ thực tiễn đã được kiểm chứng, sau đó
đối chiếu với series Database Internals của TechCraft, các phần P2–P5 và P19
([collection](https://www.patreon.com/collection/2115129)). Cách nhìn index là công cụ giảm số page,
chuỗi thống kê → chi phí → plan, và phân tích về dữ liệu lệch đến từ series đó; các bảng, thứ tự cách
sửa và checklist là của repository này.*
