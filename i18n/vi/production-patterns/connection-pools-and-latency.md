---
source_sha256: ef3f35f7a6f5661e4bdd3e723a7c5c126599ec79060dcb0f0d02f98c15bf3976
---

# Connection pool & độ trễ

**Quy tắc: database không scale theo số connection, và một query 10 ms không làm nên một API 10 ms.**
Độ trễ là của cả đường ống, và các vòng round trip chiếm phần lớn trong đó.

Thuộc nhóm storage: [storage engine](storage-engines.md) ·
[index & query plan](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) · [bố trí replica & quorum](replica-placement-and-quorums.md) ·
[session consistency](session-consistency.md) · **connection pool & độ trễ** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Connection không miễn phí](#connections-are-not-free)
- [Pool càng lớn càng tệ](#bigger-pools-make-it-worse)
- [Định cỡ pool](#sizing-a-pool)
- [Cạn pool là một chuỗi sụp đổ dây chuyền](#pool-exhaustion-is-a-cascade)
- [290 ms còn lại đã đi đâu](#where-the-other-290-ms-went)
- [Đưa phép tính tới chỗ dữ liệu](#move-the-computation-to-the-data)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# "Traffic doubled, so double the pool."
POOL_SIZE = 500          # across 20 app instances = 10,000 connections
```

Và ngõ cụt khi chẩn đoán luôn đi kèm với nó:

```
Slow query log:  SELECT ... FROM orders WHERE id = ?   →  9.8 ms   ✅
API p99:                                                  310 ms   ❌
```

Query nào cũng nhanh. Endpoint thì chậm. Nhìn chằm chằm vào slow query log sẽ không bao giờ giải thích
được, vì thời gian không nằm trong các query.

## Vì sao môi trường dev che giấu lỗi này

Một người dùng nghĩa là một connection và không có hàng đợi. Pool không bao giờ bị bão hòa, nên kích
thước của nó không quan trọng và con số nào cũng chạy được.

Độ trễ bị che theo cách khác: trên localhost một round trip mất khoảng 0.05 ms, nên 60 query tuần tự tốn
3 ms và trông như miễn phí. Cũng 60 round trip đó, mỗi cái 1 ms qua mạng, là 60 ms, và 5 ms mỗi cái sang
một AZ khác là 300 ms. **Code không đổi — khoảng cách thì đổi.**

## Connection không miễn phí

Góc nhìn từ phía thread của cùng ràng buộc này nằm ở
[server-concurrency-model](server-concurrency-model.md): một thread trong pool bị chặn chờ I/O và một
connection trong pool bị giữ mở là cùng một kiểu cạn kiệt tài nguyên.

Một connection tới database không phải là một handle rẻ tiền. Trong PostgreSQL nó là một **process**
của hệ điều hành; trong MySQL là một thread. Mỗi cái mang theo vài megabyte bộ nhớ riêng, và cái nào
cũng tham gia vào việc sổ sách nội bộ — bảng lock, khả năng nhìn thấy của snapshot, và lập lịch.

> Database không scale theo số lượng connection.

Quá một con số vừa phải, throughput *giảm* khi connection tăng: cùng số CPU giờ phải chuyển ngữ cảnh
giữa nhiều worker hơn hẳn, tranh nhau các latch nội bộ, và làm cache rối loạn. Database dành thời gian
để điều phối thay vì để thực thi.

Đó là lý do **pool** tồn tại — mượn rồi trả một tập nhỏ connection sống lâu thay vì tạo rồi hủy chúng.
Thiết lập connection (TCP, TLS, xác thực, trạng thái session) tốn vài mili giây, đáng kể khi bản thân
query chỉ tốn một.

## Pool càng lớn càng tệ

Kết quả ngược trực giác quan trọng nhất khi review: **với database bị giới hạn bởi CPU, pool nhỏ hơn
thường nhanh hơn.**

Với 8 core, 8 query đồng thời đã dùng hết máy. 500 query đồng thời không chạy nhanh hơn 60 lần — chúng
làm cùng tổng khối lượng việc nhưng với lượng chuyển ngữ cảnh, tranh chấp lock và áp lực bộ nhớ gấp 60
lần, và từng query riêng lẻ đều chậm đi. Throughput chững lại rồi giảm, trong khi độ trễ tăng trên toàn
bộ.

Xếp hàng trong pool không phải là kẻ thù. **Một hàng đợi ngắn trước một database khỏe mạnh tốt hơn một
hàng đợi dài bên trong một database đang ốm**, vì một database bão hòa làm chậm mọi query, kể cả những
query đang chạy dở.

## Định cỡ pool

Bắt đầu từ năng lực của database, không phải từ mức đồng thời của ứng dụng:

```
pool_size  ≈  (core_count × 2) + effective_spindle_count
```

Với một server 8 core điển hình chạy SSD, con số rơi vào khoảng 16–20 — thấp hơn nhiều so với mức phần
lớn các team cấu hình. Rồi nhớ rằng pool là **theo từng instance**: 20 instance ứng dụng × 20 connection
= 400 connection tới database, phải nằm dưới `max_connections` và còn dư cho các session quản trị và
replication.

- **Dùng một connection proxy** (PgBouncer, ProxySQL, RDS Proxy) khi nhiều instance hoặc serverless
  function phải dùng chung một pool nhỏ phía server. Pooling ở chế độ transaction ghép được nhiều client
  hơn hẳn lên ít connection — với lưu ý rằng trạng thái session, prepared statement và advisory lock sẽ
  hành xử khác đi.
- **Tách pool theo workload.** Cho báo cáo và background job một pool nhỏ riêng để một query phân tích
  chậm không bỏ đói traffic request — xem
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Đặt timeout khi lấy connection.** Thất bại nhanh khi pool cạn tốt hơn để mọi request treo.

## Cạn pool là một chuỗi sụp đổ dây chuyền

Sự cố không bao giờ chỉ gói gọn trong thứ đang chậm:

```
one slow query (or a lock wait, or a transaction holding a connection across an HTTP call)
      ↓ connections held longer than usual
pool exhausted
      ↓ every request waits to acquire, including trivial ones
health check can't get a connection  →  instance marked unhealthy  →  removed
      ↓ its traffic shifts to remaining instances
their pools exhaust  →  full outage from one slow query
```

Hai hệ quả khi review. Thứ nhất, **không bao giờ giữ một connection của pool xuyên qua một lời gọi
mạng** — đây là cùng quy tắc với [lock-contention](lock-contention.md), và cạn pool là cách nó thường lộ
ra. Thứ hai, **health check không nên phụ thuộc vào cùng pool với traffic request**, nếu không một pool
bão hòa sẽ kéo sập cả những instance khỏe mạnh.

## 290 ms còn lại đã đi đâu

Độ trễ của một request là tổng của mọi bước, và query thường là phần nhỏ nhất:

| Thành phần                            | Chi phí điển hình                                       |
|----------------------------------------|----------------------------------------------------|
| Thực thi query                        | con số trong slow log của bạn                        |
| Round trip mạng, app ↔ database     | 0.5–2 ms cùng AZ; 5–20 ms khác AZ hoặc khác region |
| **× số query tuần tự**     | vấn đề thật sự                                 |
| Lấy connection                 | ~0 khi pool đã ấm, không có giới hạn khi pool cạn |
| Serialize / deserialize kết quả | tăng theo số row và số cột                    |
| ORM hydrate thành object             | thường vượt thời gian query với tập kết quả lớn      |
| TLS, các chặng proxy, service mesh          | mỗi chặng, mỗi lời gọi                                  |

> Thứ người dùng chờ không chỉ là query: đó là thời gian cộng dồn qua từng chặng của cả hệ thống.

Hệ số nhân là **các round trip tuần tự**. 60 query mỗi cái 10 ms là 600 ms chờ đợi dù từng query đều
"nhanh" — và đây chính là hình dạng N+1 trong [indexes-and-query-plans](indexes-and-query-plans.md), nơi
từng query riêng lẻ đều có index đàng hoàng và chính *số lượng* mới là lỗi.

Qua nhiều service còn tệ hơn: một N+1 nằm trong một lời gọi service mà bản thân lời gọi đó lại nằm trong
một vòng lặp sẽ nhân số round trip theo tích, không phải theo tổng. Độ trễ là loại chi phí duy nhất cộng
dồn theo khoảng cách, và khoảng cách là một quyết định deploy được đưa ra sau khi code đã viết xong.

Thêm hai thứ nữa nằm ngay trước mắt mà không ai thấy. **Serialization**: `SELECT *` trả về row rộng hoặc
cột JSON lớn tốn thời gian thật để encode, truyền đi và hydrate dữ liệu mà bên gọi vứt bỏ — thêm một lý
do độ rộng của page quan trọng, xem [storage-engines](storage-engines.md). Và **lấy dữ liệu về để đếm
hay lọc trong code ứng dụng**: kéo 100,000 row về để tính một phép tổng là chuyển cả dataset qua mạng để
làm việc mà database đã có thể làm trong một lượt.

## Đưa phép tính tới chỗ dữ liệu

Cách sửa chung là giảm số round trip và để database làm việc trên tập dữ liệu:

- **Batch.** `WHERE id = ANY(:ids)` thay vì mỗi id một query. Một round trip.
- **Tổng hợp trong SQL.** `COUNT`, `SUM`, `GROUP BY` ở phía server thay vì trong code ứng dụng.
- **Dùng join hoặc CTE** để diễn đạt trong một câu lệnh điều mà một vòng lặp diễn đạt bằng N câu.
- **Phân trang theo keyset**, không dùng `OFFSET` lớn — offset sâu phải đọc rồi bỏ đi mọi thứ bị bỏ qua.
- **Chỉ select các cột bạn cần**, để chi phí serialization và hydration tương xứng với giá trị của dữ
  liệu.
- **Insert và update hàng loạt** thay vì từng row một.
- **Assert số lượng query trong test** cho các endpoint nóng. Đó là cách đáng tin cậy duy nhất để bắt
  các hồi quy về round trip, vì không cái nào trông sai khi code review.

## Checklist review

- [ ] Kích thước pool được tính từ số core của database, không phải từ mức đồng thời của request
- [ ] Tổng số connection trên mọi instance nằm dưới `max_connections` và còn dư
- [ ] Dùng connection proxy ở nơi có nhiều instance hoặc serverless function kết nối
- [ ] Background job và báo cáo dùng pool tách biệt với traffic request
- [ ] Việc lấy connection có timeout; khi cạn pool thì thất bại nhanh thay vì treo
- [ ] Health check không dùng pool của request
- [ ] Không có connection nào của pool bị giữ xuyên qua một lời gọi HTTP, một lần publish lên queue, hay một lệnh sleep
- [ ] Các endpoint nóng assert số lượng query trong test
- [ ] Vòng lặp không phát query cho từng phần tử; dùng batching hoặc join
- [ ] Phép tổng hợp diễn ra trong SQL, không phải bằng cách kéo row về ứng dụng
- [ ] Bảng lớn phân trang theo keyset
- [ ] `SELECT *` rộng trên đường nóng có lý do chính đáng khi cân với chi phí serialization và hydration
- [ ] Round trip khác AZ hoặc khác region được đếm, không mặc định là không đáng kể

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Database Internals của TechCraft, các phần
P18–P20 ([collection](https://www.patreon.com/collection/2115129)) và Backend Internals phần P5. Cách nhìn
"database không scale theo số connection", kết quả pool nhỏ hơn thì nhanh hơn, và ý "độ trễ là tổng của
mọi bước, không phải thời gian query" là của TechCraft; cấu trúc review và checklist là của repository
này.*
