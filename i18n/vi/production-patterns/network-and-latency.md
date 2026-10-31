---
source_sha256: 0b618f72e5e33a8096451f037714e13cd896e4d607bc1261d2ce254dcb984cfc
---

# Mạng & Độ trễ

**Quy tắc: một service khỏe cộng một service khỏe không cho ra một hệ thống khỏe.** Phần mạng nằm giữa
chúng có thể hỏng, và một service không cần phải chết mới kéo sập bạn — nó chỉ cần chậm.

Thuộc nhóm hệ phân tán: **mạng & độ trễ** (trang này) ·
[timeout & retry](timeouts-and-retries.md) ·
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md) ·
[đồng thuận & bầu leader](consensus-and-leader-election.md) ·
[thiết kế cho sự cố](designing-for-failure.md) ·
[failover đa region](multi-region-failover.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Những ngộ nhận bạn đang dựa vào](#the-fallacies-you-are-relying-on)
- [Chậm còn tệ hơn chết](#slow-is-worse-than-dead)
- [Độ trễ cộng dồn, rồi nhân lên](#latency-adds-up-then-multiplies)
- [Cái giá của sự trừu tượng](#the-abstraction-cost)
- [Tail latency mới là con số quan trọng](#tail-latency-is-the-number-that-matters)
- [Phải làm gì](#what-to-do-about-it)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
def checkout(cart_id):
    cart    = cart_service.get(cart_id)          # trông như một lời gọi hàm
    price   = pricing_service.quote(cart)        # thực ra là một round trip qua mạng
    order   = order_service.create(cart, price)  # có thể lỗi, hoặc treo
    payment = payment_service.charge(order)      # không lời gọi nào có timeout
    return payment
```

Bốn service, mỗi service có availability 99.9% và đều "nhanh". Availability gộp của cả chuỗi chỉ còn
99.6%, còn độ trễ là *tổng* của bốn round trip cộng với bất cứ điều gì service chậm nhất đang làm hôm
nay.

Không dòng nào trông nguy hiểm cả. Cú pháp gọi method che đi bốn cơ hội để mạng cư xử tệ.

## Vì sao môi trường dev che giấu lỗi này

Mọi thứ chạy trên một máy. Round trip chừng ~0.05 ms, không mất gói, không reset kết nối, không lỗi
DNS, không phân vùng mạng. `localhost` không phải là một bản thu nhỏ của production — nó là **một hệ
thống khác hẳn**, nơi mạng không thể hỏng.

> Unit test pass 100%, CI xanh, và business logic không đổi một dòng so với bản monolith.

Đó chính là lý do loại lỗi này gây bất ngờ đến vậy khi tách một monolith: logic giữ nguyên, nhưng bề
mặt lỗi thì hoàn toàn mới.

## Những ngộ nhận bạn đang dựa vào

Code biến một lời gọi từ xa thành thứ trông như gọi cục bộ đều ngầm giả định từng điều dưới đây:

1. **Mạng đáng tin cậy** — không phải vậy; gói tin bị mất, kết nối bị reset, phân vùng mạng xảy ra.
2. **Độ trễ bằng không** — mọi lời gọi đều có một mức sàn do vật lý và số hop quyết định.
3. **Băng thông là vô hạn** — payload lớn thì chậm và tốn kém.
4. **Mạng an toàn** — mọi thứ không mã hóa hoặc không xác thực giữa các service đều bị phơi ra.
5. **Topology không thay đổi** — instance liên tục bị di chuyển, scale và lên lịch lại.
6. **Chỉ có một người quản trị** — đội sở hữu service bạn đang gọi có lịch deploy và sự cố của riêng họ.
7. **Chi phí truyền tải bằng không** — serialization, TLS handshake và các hop qua proxy đều tốn thời
   gian thật.
8. **Mạng đồng nhất** — cùng AZ, khác AZ và khác region chênh nhau nhiều bậc độ lớn.

Điểm thực tế cần kiểm tra khi review là **#5 cộng #6**: service bạn phụ thuộc sẽ bị deploy lại ngay
giữa giờ cao điểm của bạn, bởi những người không hề biết bạn đang gọi nó.

## Chậm còn tệ hơn chết

> Thứ giết hệ thống của bạn thường không phải một service đã sập hẳn, mà là một service vẫn sống và
> trả lời cực kỳ chậm.

Một dependency đã chết thì lỗi nhanh: kết nối bị từ chối, bạn nhận lỗi, bạn xử lý. Một dependency
chậm thì **giữ chặt tài nguyên của bạn**:

```
dependency latency 50ms → 5s
   ↓
your threads/connections held 100× longer
   ↓
pool exhausted            ← server-concurrency-model, connection-pools-and-latency
   ↓
requests that never touch that dependency start failing
   ↓
health checks can't get a connection → instances evicted → cascade
```

Đây là sự bất đối xứng cốt lõi của hệ phân tán: **suy giảm lan xa hơn sự cố.** Một lần crash tự giới
hạn trong chính nó; còn sự chậm chạp thì lan qua mọi tài nguyên dùng chung.

Đó là lý do "không có timeout" không bao giờ là một mặc định an toàn, và vì sao cách chữa là
[timeout và retry](timeouts-and-retries.md) cùng với
[circuit breaker và bulkhead](circuit-breakers-and-bulkheads.md).

## Độ trễ cộng dồn, rồi nhân lên

**Gọi tuần tự thì cộng.** Bốn service, mỗi cái 100 ms, là 400 ms, và client thấy tổng đó. Phản xạ "chỉ
chậm thêm 400 ms, người dùng không để ý đâu" bỏ qua rằng con số này tính *trên mỗi request*, và số
request đồng thời bằng độ trễ × throughput — nên thêm 400 ms ở 1,000 rps nghĩa là thêm 400 request
đang chờ, mỗi request đều giữ tài nguyên.

**Gọi lồng nhau thì nhân.** Nếu A gọi B trong một vòng lặp 20 lần, và B lại gọi C, bạn đã tạo ra
20 × 2 round trip cho một thao tác của người dùng. Đây là dạng N+1 trong
[indexes-and-query-plans](indexes-and-query-plans.md) nhưng ở quy mô service, và còn tệ hơn vì mỗi hop
đều cộng thêm serialization, TLS và thời gian xếp hàng.

**Fan-out bị chặn bởi thành phần chậm nhất.** Gọi song song năm service sẽ mất thời gian bằng service
chậm nhất, không phải trung bình — nên p99 của bạn do p99 của thành phần tệ nhất quyết định, và nó càng
xấu đi với mỗi service bạn thêm vào fan-out.

## Cái giá của sự trừu tượng

Một RPC framework, một service mesh, hay một client kiểu ORM khiến lời gọi từ xa trông như gọi cục bộ.
Điều đó có giá trị, nhưng cũng có giá: **code không còn cho thấy ranh giới lỗi nằm ở đâu.**

`pricing_service.quote(cart)` trông giống hệt `calculate_price(cart)`. Thế nhưng một trong hai có thể
treo 30 giây, thành công một phần, hoặc trả về dữ liệu cũ từ một lần retry. Cú pháp không có gì để phân
biệt chúng.

Vì vậy, thói quen cần có khi review: **coi mỗi dấu `.` vượt qua ranh giới process là một điểm có thể
lỗi**, và hỏi chuyện gì xảy ra khi nó chậm, khi nó lỗi, và khi nó thành công nhưng response bị mất.
Mỗi câu trả lời phải nằm trong code, không phải trong một giả định.

## Tail latency mới là con số quan trọng

Giá trị trung bình che giấu mọi thứ. Với p50 = 50 ms và p99 = 3 s, cứ một trăm request thì có một
request chậm gấp 60 lần — và nếu một trang thực hiện mười lời gọi như vậy, thì khoảng một trong mười lần
tải trang sẽ dính ít nhất một lời gọi chậm.

Hai hệ quả:

- **Fan-out khuếch đại phần đuôi.** Với 10 lời gọi song song, mỗi lời gọi có 1% khả năng chậm, thì ~10%
  request sẽ chậm. Càng tách nhỏ, phần đuôi càng chiếm ưu thế.
- **Retry được rút ra từ chính phần đuôi.** Một timeout đặt ở p99 nghĩa là bạn retry đúng 1% chậm nhất —
  chính lúc dependency ít có khả năng gánh thêm tải nhất.

Hãy đo và cảnh báo trên p99 và p99.9, theo từng dependency. Một dashboard độ trễ trung bình sẽ trông
ổn suốt cả sự cố.

## Phải làm gì

- **Gắn timeout cho mọi lời gọi từ xa**, suy ra từ một ngân sách thời gian. Xem
  [timeout và retry](timeouts-and-retries.md).
- **Giảm số round trip.** Gom batch, tổng hợp ở phía server, hoặc denormalize read model thay vì
  fan-out cho từng item.
- **Song song hóa các lời gọi độc lập** thay vì xâu chuỗi chúng, để độ trễ là giá trị lớn nhất thay vì
  tổng.
- **Biến các lời gọi không thiết yếu thành non-blocking.** Một gợi ý sản phẩm hay một analytics event
  không bao giờ được nằm trên đường đi quan trọng.
- **Cache những gì chịu được dữ liệu cũ**, và biết rõ những gì không chịu được. Xem
  [caching](caching.md) và [consistency-boundaries](consistency-boundaries.md).
- **Đặt các service hay nói chuyện với nhau ở gần nhau** — cùng AZ, hoặc gộp lại. Round trip khác
  region là một quyết định thiết kế, không phải tai nạn.
- **Truyền deadline xuống**, để service phía dưới biết còn bao nhiêu thời gian thay vì cặm cụi xử lý một
  request đã timeout từ lâu.

## Checklist review

- [ ] Mọi lời gọi xuyên process đều có timeout tường minh
- [ ] Availability gộp của chuỗi dependency đã được tính toán, không phải giả định
- [ ] Các lời gọi tuần tự có thể chạy song song đã được song song hóa
- [ ] Không có lời gọi từ xa cho từng item bên trong vòng lặp
- [ ] Các dependency không thiết yếu nằm ngoài đường đi quan trọng
- [ ] Độ trễ p99/p99.9 được đo theo từng dependency, không chỉ giá trị trung bình
- [ ] Độ rộng fan-out có giới hạn và hiệu ứng khuếch đại phần đuôi của nó đã được hiểu rõ
- [ ] Deadline được truyền xuống các lời gọi phía dưới
- [ ] Các hop khác AZ và khác region là có chủ đích
- [ ] Kích thước payload có giới hạn; response lớn được phân trang hoặc stream
- [ ] Trong code, lời gọi từ xa phân biệt được rõ ràng với lời gọi cục bộ

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Distributed Systems của TechCraft, các
phần P1–P3 ([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề "A khỏe + B khỏe
≠ hệ thống khỏe", ý "một hệ thống không cần chết mới hỏng, nó chỉ cần chậm", và lập luận về cái giá của
sự trừu tượng là của TechCraft; phần liệt kê các ngộ nhận, phân tích tail latency, và checklist là của
repository này.*
