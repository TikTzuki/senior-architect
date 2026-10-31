---
source_sha256: 259b933829c0afbf6d10c04ed4972a8d74a55f125cc1fae4394846518cf823fd
---

# Mô hình concurrency của server

**Quy tắc: một server chậm trong khi CPU thấp không phải đang thiếu sức tính toán — nó đang chờ.**
Cách runtime của bạn sử dụng khoảng chờ đó quyết định việc thêm thread sẽ giúp ích hay làm mọi thứ tệ
hơn.

Thuộc nhóm backend runtime: **mô hình concurrency** (trang này) · [bộ nhớ & GC](memory-and-gc.md) ·
[caching](caching.md) · [distributed lock](distributed-locks.md) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
[deploy & cấu hình](deployment-and-config.md) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Nghịch lý CPU nhàn rỗi](#the-paradox-of-idle-cpu)
- [Một thread thực sự tốn những gì](#what-a-thread-actually-costs)
- [Thread pool là lớp bảo vệ, không phải tối ưu hoá](#thread-pools-are-a-protection-not-an-optimization)
- [Event loop](#event-loops)
- [Chặn event loop](#blocking-the-event-loop)
- [Giữ nhiều connection](#holding-many-connections)
- [Lựa chọn và định cỡ](#choosing-and-sizing)
- [Checklist review](#review-checklist)

## Anti-pattern

```java
// "Latency cao, nên ta cần thêm worker."
server.setMaxThreads(2000);
```

```javascript
// Node: một lời gọi nặng CPU nằm trên đường xử lý request.
app.get('/report', (req, res) => {
  const rows = db.querySync(...);              // lời gọi blocking trong event loop
  res.json(rows.map(heavyTransform));          // và còn thêm việc tốn CPU
});
```

Cả hai đều xuất phát từ cùng một cách hiểu sai: latency cao, vậy server hẳn đang thiếu năng lực xử
lý. Không phải vậy. Ở trường hợp đầu, server đang chờ I/O và 2.000 thread chỉ cộng thêm context
switch vào khoảng chờ đó. Ở trường hợp thứ hai, một request duy nhất làm đứng *mọi* request khác trên
process đó.

## Vì sao môi trường dev che giấu lỗi này

Mỗi lần một request nghĩa là không có tranh chấp, không có xếp hàng, và không có thread nào bị bỏ
đói. Event loop không bao giờ bị chặn vì chẳng có gì khác đang chờ nó, và 2.000 thread hành xử y hệt
20 thread khi chỉ có một cái được dùng.

Tín hiệu đánh lừa tất cả mọi người là dashboard: **CPU thấp, nên server "không bận".** Cách đọc đó
sai, và đó là lý do các team scale nhầm tài nguyên suốt nhiều tuần.

## Nghịch lý CPU nhàn rỗi

Lần theo một request điển hình:

```
parse + route          0.4 ms   CPU
database query        12   ms   WAITING
call another service   40   ms   WAITING
serialize response      0.6 ms   CPU
                      ─────────
total                 53   ms   of which ~1 ms is CPU
```

98% thời gian của request là chờ một thứ khác. Vậy ràng buộc không nằm ở sức tính toán — mà là
**process của bạn giữ được bao nhiêu khoảng chờ cùng lúc**, và giữ một khoảng chờ thì tốn bao nhiêu.

Điều đó đặt lại toàn bộ bài toán. Thêm CPU chẳng được gì. Thêm thread chỉ có ích cho đến khi chi phí
giữ một khoảng chờ vượt quá lợi ích của việc giữ thêm một cái nữa.

## Một thread thực sự tốn những gì

Thread là một thực thể do OS lập lịch, không phải một object rẻ tiền:

- **Bộ nhớ stack** — mặc định dành sẵn 512 KB đến 1 MB (với JVM thường là 512 KB–1 MB). 1.000
  thread là khoảng một gigabyte stack trước khi ứng dụng của bạn cấp phát bất cứ thứ gì.
- **Context switch** — scheduler lưu và khôi phục trạng thái thanh ghi, đồng thời làm bẩn CPU cache.
  Khi số thread sẵn sàng chạy nhiều hơn số core, máy dành ngày càng nhiều thời gian để chuyển đổi
  thay vì thực thi.
- **Sổ sách của scheduler và kernel** — mỗi thread đều tham gia vào các quyết định lập lịch.

Vì vậy **bùng nổ thread** có một hình dạng rõ ràng: throughput tăng, đi ngang, rồi *giảm*, trong khi
latency leo thang trên mọi endpoint. Hệ thống bận rộn hoàn toàn với việc điều phối. Qua điểm gãy đó,
thêm thread chỉ có hại — đây cũng chính là đường cong của
[connection pool](connection-pools-and-latency.md), vì cùng một lý do.

## Thread pool là lớp bảo vệ, không phải tối ưu hoá

Tái sử dụng thread giúp tránh chi phí tạo mới, nhưng đó là lợi ích nhỏ hơn. Lợi ích thật là **giới
hạn mức concurrency**.

Một server không có giới hạn sẽ nhận việc cho tới khi chết. Một pool có giới hạn cùng một queue có
giới hạn khiến hệ thống **bỏ bớt hoặc trì hoãn tải thay vì sụp đổ** — một queue đứng trước một server
khoẻ mạnh tốt hơn việc cho vào không giới hạn một server đang quá tải.

Khi nào pool trở thành vấn đề:

- **Queue không giới hạn** — pool thôi từ chối và bắt đầu tích tụ. Request nằm chờ đến khi client đã
  timeout từ lâu, nên bạn tiêu năng lực xử lý để tính ra những câu trả lời không ai đọc. Hãy giới hạn
  queue và từ chối khi đầy.
- **Một pool cho mọi thứ** — một lời gọi downstream chậm làm đầy pool dùng chung và kéo sập cả những
  endpoint không liên quan. Tách pool riêng cho từng dependency (bulkhead) sẽ khoanh vùng nó — xem
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Blocking trong pool trong khi đang giữ một tài nguyên khác** — một thread trong pool chờ lời gọi
  HTTP trong khi giữ một database connection sẽ trói hai kiểu cạn tài nguyên vào nhau. Xem
  [lock-contention](lock-contention.md).

## Event loop

Cách thay thế: một thread (hoặc vài thread) cộng với I/O không blocking. Thay vì đỗ một thread vào
một khoảng chờ, runtime đăng ký một callback rồi chuyển sang việc khác; OS báo khi nào sẵn sàng qua
`epoll` / `kqueue`.

Giữ 10.000 connection đang chờ chỉ tốn 10.000 object trạng thái nhỏ, không phải 10.000 stack. Đó là
điều làm cho các runtime dựa trên event loop hiệu quả với workload nặng I/O và nhiều connection —
Node, nginx, Netty, Python async.

Cái giá phải trả: **bạn tuyệt đối không được chặn loop.**

## Chặn event loop

Đây là kiểu lỗi đặc trưng, và nó gây hại nặng hơn nhiều so với một thread bị chặn:

```javascript
// Một request; mọi request đồng thời đều phải trả giá.
const hash = crypto.pbkdf2Sync(password, salt, 600000, 64, 'sha512');  // ~800 ms CPU
```

Với mô hình thread-per-request, một request chậm chiếm một thread. Với event loop, **một callback
chậm làm đứng mọi connection trên process đó**, vì tất cả dùng chung một loop.

Những thứ chặn loop: lời gọi file hoặc database đồng bộ, `JSON.parse` trên payload lớn, crypto, xử lý
ảnh, regex backtracking, và bất kỳ vòng lặp chặt nào trên một mảng lớn. Cách sửa là đưa việc tốn CPU
ra khỏi loop — một worker pool, một process riêng, hoặc một queue (xem
[message-delivery-semantics](message-delivery-semantics.md)).

**Đo trực tiếp event loop lag.** Đó là metric hữu ích nhất với các runtime này, và lag tăng lên giải
thích được những đợt latency mà đồ thị CPU và bộ nhớ không giải thích nổi.

## Giữ nhiều connection

Hai giới hạn khác nhau thường bị gộp làm một:

- **Số connection đang được giữ đồng thời** — phần lớn nhàn rỗi, đang chờ. Bị giới hạn bởi bộ nhớ
  cho mỗi connection và file descriptor. Event loop rất mạnh ở điểm này; thiết kế
  thread-per-connection thì không.
- **Số request đang xử lý đồng thời** — đang thực sự tiêu tốn CPU hoặc một tài nguyên downstream. Bị
  giới hạn bởi pool và năng lực của downstream.

Một server hoàn toàn có thể giữ 50.000 WebSocket connection nhàn rỗi trong khi xử lý đồng thời 200
request. Định cỡ cho 50.000 *worker* chỉ vì có 50.000 *connection* mới là sai lầm.

Các giới hạn thực tế cần kiểm tra: `ulimit -n` (file descriptor) — thường là trần thật sự và khi chạm
vào thì báo một lỗi khó hiểu; cạn ephemeral port ở chiều gọi ra; và buffer cho mỗi connection, với
64 KB × 50.000 là 3 GB.

## Lựa chọn và định cỡ

| Workload                                           | Mô hình                                            |
|----------------------------------------------------|----------------------------------------------------|
| Nặng I/O, nhiều connection đồng thời               | Event loop, hoặc virtual thread                    |
| Mỗi request tốn CPU                                | Thread pool cỡ xấp xỉ số core                      |
| Hỗn hợp                                            | Event loop cho I/O + worker pool riêng cho CPU     |
| Connection sống lâu (WebSocket, SSE, streaming)    | Event loop                                         |

**Virtual thread** (Java 21+, goroutine của Go) xoá bỏ thế lưỡng nan này: code kiểu thread-per-request
nhưng scale gần như event loop, vì một virtual thread bị chặn sẽ được đỗ lại với chi phí rẻ thay vì
giữ một OS thread. Lưu ý đáng nhớ khi review: một virtual thread bị pin bởi khối `synchronized` hoặc
một native call vẫn giữ carrier thread của nó, điều này đưa giới hạn ban đầu trở lại đúng ở chỗ bạn
không còn đề phòng.

Quy tắc định cỡ theo kinh nghiệm: nặng CPU → khoảng bằng số core; thread pool nặng I/O → cao hơn,
nhưng hãy suy ra từ thời gian chờ đo được và năng lực của downstream thay vì cứ đoán tăng dần, vì
tài nguyên downstream thường mới là ràng buộc thật.

## Checklist review

- [ ] "Chậm nhưng CPU thấp" được chẩn đoán là đang chờ, không phải cần thêm CPU hay thread
- [ ] Kích thước thread pool được suy ra từ số core và năng lực downstream, không phải từ lượng request
- [ ] Queue của pool có giới hạn, và việc từ chối được xử lý thay vì xếp hàng vô thời hạn
- [ ] Các pool riêng cô lập dependency chậm khỏi lưu lượng request
- [ ] Không có lời gọi blocking, việc nặng CPU hay parse dữ liệu lớn nào chạy trên event loop
- [ ] Event loop lag được giám sát với các runtime dùng event loop
- [ ] Năng lực giữ connection và mức concurrency của request được định cỡ như hai giới hạn riêng
- [ ] Đã tính đến `ulimit -n` và bộ nhớ buffer cho mỗi connection
- [ ] Không có thread nào trong pool chờ lời gọi mạng trong khi giữ một tài nguyên pool khác
- [ ] Ở nơi dùng virtual thread, đã kiểm tra việc bị pin bởi `synchronized` hoặc native call

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P2–P4 và P8 ([collection](https://www.patreon.com/techcraft_official)). Nghịch lý CPU nhàn rỗi, cách
nhìn thread pool như một lớp bảo vệ, và phân tích bùng nổ thread là của TechCraft; cấu trúc review và
checklist là của repository này. Phần P1 (vòng đời request) không khôi phục được từ trang đã lưu.*
