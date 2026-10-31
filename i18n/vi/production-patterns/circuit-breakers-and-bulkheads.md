---
source_sha256: b0734564530092a3e1d4bca7b306695a1d655682310c6f61d020a51c79fe91c6
---

# Circuit Breaker & Bulkhead

**Quy tắc: một dependency đang hấp hối cần được cô lập, không phải bị dội thêm request.** Breaker ngăn
bạn gọi thứ không thể trả lời; bulkhead ngăn một sự cố nuốt mất tài nguyên mà mọi phần khác cần dùng.

Thuộc nhóm hệ phân tán: [mạng & độ trễ](network-and-latency.md) ·
[timeout & retry](timeouts-and-retries.md) ·
**circuit breaker & bulkhead** (trang này) ·
[đồng thuận & bầu leader](consensus-and-leader-election.md) ·
[thiết kế cho sự cố](designing-for-failure.md) ·
[failover đa region](multi-region-failover.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Ngừng gọi thứ đang hấp hối](#stop-calling-what-is-dying)
- [Ba trạng thái](#the-three-states)
- [Tinh chỉnh](#tuning-it)
- [Làm gì khi circuit đang mở](#what-to-do-when-the-circuit-is-open)
- [Bulkhead](#bulkheads)
- [Đặt vách ngăn ở đâu](#where-to-put-the-walls)
- [Breaker và bulkhead không thay thế cho nhau](#breakers-and-bulkheads-are-not-interchangeable)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# Một pool dùng chung, không có breaker. Endpoint nào cũng phụ thuộc vào sức khỏe của mọi dependency.
executor = ThreadPoolExecutor(max_workers=100)

def product_page(pid):
    product  = executor.submit(catalog.get, pid).result()
    reviews  = executor.submit(review_service.get, pid).result()   # service này đang hấp hối
    recs     = executor.submit(recommender.get, pid).result()      # có thì tốt, không có cũng được
    return render(product, reviews, recs)
```

Review service xuống cấp, mỗi response mất 8 giây. Giờ mỗi trang sản phẩm giữ ba worker trong tám giây,
pool dùng chung đầy lên, và **checkout bắt đầu lỗi** — một endpoint hoàn toàn không gọi tới review
service.

Có hai lỗi độc lập: không có gì ngừng việc gọi một dependency rõ ràng đang hỏng, và không có gì ngăn sự
chậm chạp của một dependency nuốt mất công suất mà phần còn lại của hệ thống cần.

> "Mạng hơi chậm thôi, lát hệ thống tự hồi lại."
> "Service đang lỗi thì cứ gọi tiếp — biết đâu lần sau lại được."

Cả hai đều là kiểu suy nghĩ dẫn tới đoạn code trên.

## Vì sao môi trường dev che giấu lỗi này

Dependency là stub luôn trả lời tức thì, nên breaker không bao giờ ngắt và pool dùng chung không bao giờ
bão hòa. Không có lỗi một phần — một dependency hoặc được nối vào, hoặc vắng mặt, chứ không bao giờ
*ốm yếu*.

Lỗi này còn đòi hỏi **tải đồng thời cộng với một tài nguyên dùng chung**, nên nó không thể xuất hiện khi
mỗi lần chỉ có một request. Một bộ test pass hết không có gì để phân biệt thiết kế được cô lập với thiết
kế bị buộc chặt vào nhau.

## Ngừng gọi thứ đang hấp hối

> Một service sắp gục cần được tách riêng ra cho nó thở, chứ không phải hứng thêm hàng nghìn request
> mới.

Khi một dependency đã hỏng, tiếp tục gọi nó gây hại cho cả hai phía:

- **Cho bạn** — mỗi lời gọi tốn một khoảng tài nguyên bị giữ bằng đúng một lần timeout, để chờ một
  response bạn sẽ không nhận được.
- **Cho nó** — một service đang cố hồi phục sẽ không thể hồi phục khi lưu lượng vẫn ồ ạt đổ tới. Retry
  càng làm tệ hơn; xem [timeouts-and-retries](timeouts-and-retries.md).

Thay vào đó, **circuit breaker** cho lỗi nhanh: sau đủ số lần lỗi, nó ngừng thử gọi và trả về ngay lập
tức, cho dependency khoảng trống để hồi phục và giải phóng công suất của bạn.

Giá trị ở đây không nằm ở sự lịch sự. **Lỗi trong 1 ms thay vì 8 s chính là thứ giữ cho pool của bạn
còn sống.**

## Ba trạng thái

```
        failure rate exceeds threshold
CLOSED ─────────────────────────────────▶ OPEN
  ▲                                        │ after cool-down
  │ probe succeeds                         ▼
  └────────────────── HALF-OPEN ◀──────────┘
                          │ probe fails
                          └──────────▶ OPEN
```

- **Closed** — bình thường. Lời gọi đi qua; lỗi được đếm.
- **Open** — lời gọi lỗi ngay mà không cần thử. Không giữ tài nguyên nào.
- **Half-open** — sau khoảng cool-down, cho phép một số ít lời gọi thử. Thành công thì đóng circuit;
  thất bại thì mở lại.

Half-open là phần quan trọng, và cũng là phần hay bị cài đặt sai: nó chỉ được cho phép **vài** request,
không phải tất cả. Mở toang cửa xả lũ vào một service vừa mới sống lại chính là cách đánh gục nó thêm
lần nữa.

## Tinh chỉnh

- **Ngắt theo *tỷ lệ* lỗi, không theo số lượng tuyệt đối**, trên một cửa sổ trượt có ngưỡng lưu lượng
  tối thiểu. Thiếu ngưỡng lưu lượng, chỉ hai lần lỗi trong lúc vắng khách cũng đủ mở circuit.
- **Tính sự chậm là lỗi.** Một dependency trả lời với độ trễ gấp 10 lần bình thường là đang hỏng, kể cả
  khi trả về `200`. Timeout là thứ biến "chậm" thành thứ đếm được — đó là lý do breaker cần có timeout
  thì mới hoạt động được.
- **Đừng đếm lỗi phía client.** Một `400` hay `404` là response đúng cho một request sai; đếm chúng thì
  một client gửi request hỏng có thể mở circuit cho tất cả mọi người. Hãy đếm `5xx`, timeout và lỗi kết
  nối.
- **Theo từng dependency, và thường là theo từng endpoint.** Một endpoint chậm của một service không nên
  chặn luôn các lời gọi tới những endpoint khỏe mạnh của nó.
- **Cool-down đủ dài để có ý nghĩa** (vài giây đến vài chục giây) — quá ngắn thì về bản chất bạn đang
  retry mà không có backoff.

Đặt ngưỡng dựa trên hành vi bình thường của dependency. Một service vốn có tỷ lệ lỗi tự nhiên 2% cần
ngưỡng cao hơn một service bình thường gần như không bao giờ lỗi.

## Làm gì khi circuit đang mở

Lỗi nhanh mới chỉ là một nửa thiết kế. Nửa còn lại là **người dùng nhận được gì**, và điều này nên được
quyết định riêng cho từng dependency:

| Dependency                          | Hành vi khi circuit mở                                    |
|-------------------------------------|-----------------------------------------------------------|
| Gợi ý sản phẩm, "sản phẩm liên quan" | Bỏ phần đó đi. Trang vẫn hoạt động.                        |
| Đánh giá                            | Hiển thị bản cache, hoặc ẩn đi kèm một thông báo          |
| Hiển thị tồn kho                    | Trả về giá trị biết gần nhất kèm ghi chú dữ liệu có thể cũ |
| **Ủy quyền thanh toán**             | **Cho request lỗi.** Không bao giờ giả thành công với tiền. |
| Auth                                | Fail closed — không bao giờ suy giảm thành cấp quyền truy cập |

Đây là câu hỏi cần đặt ra khi review: *dependency này là thiết yếu hay chỉ bổ trợ?* Một dependency bổ
trợ mà có thể làm lỗi cả request là đã bị đặc tả sai — và phần lớn các trang sản phẩm có vài cái như
vậy. Trả về một trang bị suy giảm tốt hơn trả về lỗi, trừ khi tính đúng đắn hoặc bảo mật bị đe dọa.

## Bulkhead

Tên gọi lấy từ các khoang của con tàu: thân tàu thủng thì nước chỉ tràn vào một khoang, không nhấn chìm
cả con tàu.

Lỗi mà bulkhead ngăn chặn là **tranh cướp tài nguyên** — một dependency chậm nuốt hết một pool dùng
chung. Cách sửa là thôi dùng chung:

```python
# Công suất riêng, có giới hạn, cho từng dependency.
catalog_pool  = ThreadPoolExecutor(max_workers=40)
review_pool   = ThreadPoolExecutor(max_workers=10)   # có cạn thì cạn một mình
recommend_pool= ThreadPoolExecutor(max_workers=5)    # ít quan trọng nhất, ít công suất nhất
```

Giờ review service muốn chậm bao nhiêu cũng được; nó dùng hết 10 worker rồi dừng. Checkout vẫn giữ
nguyên công suất của riêng mình.

> Hỏng hóc là trạng thái bình thường. Điều cần hỏi không phải "làm sao để không bao giờ hỏng?" mà là
> "khi một bộ phận gục, làm sao để thiệt hại dừng lại ở bộ phận đó?"

## Đặt vách ngăn ở đâu

Bulkhead áp dụng cho mọi tài nguyên dùng chung có thể cạn kiệt — không chỉ thread pool:

- **Thread/worker pool** cho từng dependency. Xem
  [server-concurrency-model](server-concurrency-model.md).
- **Connection pool** — một pool riêng cho background job và báo cáo, để một query phân tích không thể
  bỏ đói lưu lượng request. Xem
  [connection-pools-and-latency](connection-pools-and-latency.md).
- **Công suất theo từng tenant** để một khách hàng không thể chiếm hết mọi thứ — đây là nơi bulkhead gặp
  [rate limiting](rate-limiting.md).
- **Queue và consumer** — mỗi loại workload một queue riêng, để tồn đọng của một loại message không chặn
  các loại khác.
- **Instance** — instance riêng cho các đường đi quan trọng (checkout tách khỏi duyệt sản phẩm), dạng
  mạnh nhất và cũng đắt nhất.
- **Gửi webhook theo từng consumer** — xem
  [webhook-provider-design](webhook-provider-design.md).

Cái giá là có thật: chia nhỏ công suất nghĩa là hiệu suất sử dụng thấp hơn, vì mỗi khoang phải được định
cỡ cho đỉnh tải của riêng nó. Đó là sự đánh đổi — một chút hiệu quả để đổi lấy khả năng khoanh vùng — và
nó nên được chọn một cách tường minh thay vì bị phát hiện ra giữa một sự cố.

## Breaker và bulkhead không thay thế cho nhau

Chúng giải quyết hai vấn đề khác nhau và thường bạn cần cả hai:

|                     | Ngăn chặn                                                | Nếu thiếu                                               |
|---------------------|----------------------------------------------------------|---------------------------------------------------------|
| **Circuit breaker** | Lãng phí tài nguyên vào một dependency không thể trả lời | Bạn giữ tài nguyên qua từng lần timeout                 |
| **Bulkhead**        | Một dependency nuốt mất công suất dùng chung             | Một dependency chậm làm suy giảm các endpoint không liên quan |

Breaker mà không có bulkhead thì lưu lượng trước lúc ngắt vẫn có thể vắt kiệt một pool dùng chung.
Bulkhead mà không có breaker thì khoang đó vẫn tiếp tục đốt công suất vào những lời gọi chắc chắn thất
bại. Kết hợp lại: khoang có giới hạn *và* không còn phải trả giá cho sự cố.

## Checklist review

- [ ] Mọi dependency từ xa đều có circuit breaker, hoặc có lý do được ghi lại vì sao không cần
- [ ] Breaker ngắt theo **tỷ lệ** lỗi trên một cửa sổ, có ngưỡng lưu lượng tối thiểu
- [ ] Response chậm được tính là lỗi (đã đặt timeout — breaker cần chúng)
- [ ] Lỗi phía client (`4xx`) không làm breaker ngắt
- [ ] Breaker được khoanh theo từng dependency, và theo từng endpoint khi hành vi khác nhau
- [ ] Half-open chỉ cho một lượng thăm dò giới hạn, không phải toàn bộ lưu lượng
- [ ] Mỗi dependency được phân loại thiết yếu hay bổ trợ
- [ ] Dependency bổ trợ suy giảm nhẹ nhàng; request không bị lỗi
- [ ] Auth và thanh toán fail closed — không bao giờ giả thành công
- [ ] Thread/worker pool được chia theo từng dependency, không dùng chung toàn cục
- [ ] Background job và báo cáo dùng connection pool tách khỏi lưu lượng request
- [ ] Queue được tách theo workload
- [ ] Việc breaker đổi trạng thái được log và có cảnh báo
- [ ] Cái giá về hiệu suất sử dụng khi chia nhỏ được chấp nhận một cách có chủ đích

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Distributed Systems của TechCraft, các
phần P6–P7 ([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề "service đang
hấp hối cần được cô lập, không phải thêm request", hai ngộ nhận về việc tự hồi phục, và cách nhìn bulkhead
như chia nhỏ để khoanh vùng là của TechCraft; chi tiết tinh chỉnh state machine, bảng suy giảm, và
checklist là của repository này.*
