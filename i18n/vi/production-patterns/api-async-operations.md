---
source_sha256: 364a45a3ad3462936787eaa9dfec29a7709671c186e3063189d2f9c9a5bba8f1
---

# Thao tác bất đồng bộ & chạy lâu

**Quy tắc: khi câu trả lời chưa tồn tại, đừng giả vờ rằng request có thể trả nó về.** Hãy mô hình
hóa thao tác thành một resource mà client có thể poll, và timeout sẽ không còn là vấn đề của bạn.

Thuộc nhóm thiết kế API: [hợp đồng & versioning](api-contracts.md) ·
[mô hình hóa resource](api-resource-modeling.md) · [endpoint danh sách](api-list-endpoints.md) ·
**thao tác bất đồng bộ** (trang này) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Giả định bị phá vỡ](#the-assumption-that-breaks)
- [Thao tác như một resource](#operation-as-a-resource)
- [Polling mà không gây hại](#polling-that-does-not-hurt)
- [Phục hồi mới là yêu cầu thực sự](#recovery-is-the-actual-requirement)
- [Thông báo thay vì polling](#notifying-instead-of-polling)
- [Ranh giới nằm ở đâu](#where-the-boundary-is)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
@app.post("/reports/statement")
def generate_statement(req):
    rows = db.query_five_years_of_transactions(req.customer_id)  # 90s
    xlsx = build_excel(rows)  # 40s
    url = s3.upload(xlsx)  # 25s
    return {"download_url": url}  # ~155s sau
```

Load balancer cắt kết nối ở giây thứ 60. Client nhận 504 và retry — khởi động một job 155 giây thứ
hai trong khi job đầu vẫn đang chạy. Sau ba lần retry, năm job giống hệt nhau đang tranh nhau cùng
một database.

Công việc thường *thành công*. Client không bao giờ biết điều đó, vì kênh duy nhất để nhận câu trả
lời là một kết nối không còn tồn tại.

## Vì sao môi trường dev che giấu lỗi này

Ở local dữ liệu nhỏ, nên báo cáo chạy mất 800 ms và trả về gọn gàng. Không có proxy nào ép timeout,
không có client retry, và không có concurrency.

Lỗi tăng theo **kích thước dữ liệu của khách hàng**, nên nó xuất hiện đầu tiên ở những tài khoản lớn
nhất và quan trọng nhất của bạn — vị khách VIP yêu cầu sao kê năm năm chính là request sẽ vỡ.

## Giả định bị phá vỡ

> "Gọi một API thì phải nhận lại ngay kết quả sau cùng."

Với phần lớn endpoint thì điều này ổn. Với bất cứ thứ gì có thời gian chạy phụ thuộc vào khối lượng
dữ liệu hoặc một hệ thống bên ngoài, đó là một **lời nói dối có hạn chót**: mọi tầng giữa client và
server đều áp một timeout mà bạn không kiểm soát — trình duyệt, CDN, load balancer, service mesh,
thư viện client.

Cách nhìn lại vấn đề để sửa nó: response cho một thao tác dài không phải là kết quả. Nó là **xác
nhận rằng công việc đã được tiếp nhận**, cộng với một cách để biết kết quả ra sao.

Một khi client giữ một handle bền vững, kết nối bị rớt không còn đồng nghĩa với mất kết quả. Đó
chính là toàn bộ mục đích — không phải tốc độ, mà là **khả năng phục hồi**.

## Thao tác như một resource

Hãy cho công việc một danh tính, đúng như trong
[api-resource-modeling](api-resource-modeling.md):

```http
POST /reports
Idempotency-Key: 8f2c...          ← để retry không khởi động job thứ hai

202 Accepted
Location: /operations/op_7c1a
{
  "id": "op_7c1a",
  "status": "pending",
  "created_at": "2026-08-31T10:00:00Z"
}
```

```http
GET /operations/op_7c1a

200 OK
{
  "id": "op_7c1a",
  "status": "running",           # pending | running | succeeded | failed | cancelled
  "progress": { "percent": 40 },
  "retry_after": 5
}
```

Khi hoàn tất, thao tác mang theo kết quả của nó — hoặc lỗi của nó:

```json
{
  "status": "succeeded",
  "result": {
    "download_url": "https://..."
  },
  "completed_at": "...",
  "expires_at": "..."
}

{
  "status": "failed",
  "error": {
    "code": "source_unavailable",
    "message": "...",
    "retryable": true
  }
}
```

Ba đặc tính giúp cách này hoạt động, và mỗi đặc tính là một mục cần review:

- **`202` kèm `Location`** — client có một handle bền vững trước khi bất cứ điều gì có thể hỏng.
- **Idempotency key khi gửi yêu cầu** — một POST được retry trả về thao tác *đã có* thay vì khởi
  động một bản trùng. Đây là chi tiết quan trọng nhất; thiếu nó, retry sẽ nhân bản những công việc
  tốn kém. Xem [payment-state-and-idempotency](payment-state-and-idempotency.md).
- **Trạng thái cuối được lưu bền.** Bản ghi thao tác sống lâu hơn worker, nên một client mất kết nối
  cả tiếng đồng hồ vẫn có thể lấy được kết quả.

## Polling mà không gây hại

Polling ngây thơ thay một request dài bằng hàng nghìn request ngắn:

- **Nói cho client biết nên poll bao lâu một lần** — `retry_after` trong body, hoặc header
  `Retry-After`. Đừng để khoảng thời gian đó cho client tự tưởng tượng.
- **Gợi ý backoff** — lúc đầu nhanh (thao tác có thể xong sớm), sau đó chậm dần.
- **Làm cho endpoint trạng thái thật rẻ.** Nó sẽ được gọi nhiều hơn hẳn endpoint gửi yêu cầu; nó
  nên đọc một dòng, không bao giờ tính lại tiến độ. Một endpoint trạng thái đi query dữ liệu bên dưới
  là tự phá mục đích của chính nó.
- **Hỗ trợ `ETag` / `304`** để những lần poll không có gì thay đổi gần như không tốn gì.
- **Rate limit nó riêng** với endpoint gửi yêu cầu — xem [rate limiting](rate-limiting.md) — với hạn
  mức cao hơn, vì polling là hành vi được dự kiến.

## Phục hồi mới là yêu cầu thực sự

Một API bất đồng bộ là một **workflow có trạng thái**, nên các câu hỏi thiết kế đều là câu hỏi về
phục hồi:

- **Trạng thái nằm ở đâu?** Trong một nơi lưu trữ bền vững, không phải bộ nhớ của worker. Worker khởi
  động lại giữa chừng không được làm mất thao tác.
- **Chuyện gì xảy ra khi worker chết?** Thao tác phải lấy lại được — một lease có timeout, hoặc một
  lượt quét phát hiện thao tác bị kẹt. Nếu không, nó nằm mãi ở `running`, và đây là bug phổ biến
  nhất trong những hệ thống kiểu này. Xem [distributed-locks](distributed-locks.md).
- **Công việc có chạy tiếp được hay chạy lại được không?** Cái nào cũng được; "không cái nào" thì
  không được. Một job năm phút không thể chạy tiếp thì ít nhất phải chạy lại được một cách an toàn,
  nghĩa là các side effect phải idempotent.
- **Kết quả dở dang có bị nhìn thấy không?** Nếu job vừa chạy vừa ghi, một lần thất bại sẽ để lại
  output dở dang mà ai đó có thể đọc. Hoặc ghi một cách atomic ở cuối, hoặc đánh dấu output là chưa
  hoàn chỉnh.
- **Kết quả được giữ bao lâu?** Nêu rõ `expires_at`, và ép buộc nó — nếu không, bản ghi thao tác và
  các file được sinh ra sẽ tích tụ mãi mãi.
- **Có hủy được không?** Nếu có, việc hủy tự nó là một chuyển trạng thái có trạng thái riêng, không
  phải một tín hiệu bắn đi rồi quên.

**Thao tác bị kẹt cần được phát hiện, không phải trông chờ.** Hãy alert khi có thao tác nằm ở trạng
thái chưa kết thúc quá một ngưỡng; cùng lập luận với alert cho `pending` bị kẹt trong
[payment-reconciliation](payment-reconciliation.md).

## Thông báo thay vì polling

Polling là lựa chọn mặc định đáng tin cậy; thông báo là sự tối ưu:

- **Webhook** — bạn gọi client khi thao tác xong. Loại bỏ hoàn toàn polling, và giờ bạn phải chịu
  trách nhiệm về đảm bảo giao nhận. Xem [webhook-provider-design](webhook-provider-design.md).
- **Server-sent events / WebSocket** — tốt cho tiến độ trực tiếp, nhưng chúng ghim client vào một
  instance, khiến việc deploy phức tạp hơn. Xem
  [health-checks-and-load-balancing](health-checks-and-load-balancing.md).

**Luôn giữ endpoint polling** kể cả khi bạn có cung cấp webhook. Một client bỏ lỡ webhook, hoặc hoàn
toàn không nhận được webhook, cần một cách để biết kết quả — và đội hỗ trợ cũng cần điều đó.

## Ranh giới nằm ở đâu

| Thời gian chạy           | Hình dạng                                                  |
|--------------------------|------------------------------------------------------------|
| < 1s                     | Đồng bộ                                                    |
| 1–10s                    | Đồng bộ, với timeout được thông báo cho client             |
| 10s – vài phút           | **Resource thao tác bất đồng bộ**                          |
| Vài phút – vài giờ       | Thao tác bất đồng bộ + webhook, có tiến độ                 |
| Không giới hạn / theo lịch | Một resource job với vòng đời và lịch sử riêng           |

Hai điều buộc phải dùng bất đồng bộ bất kể thời gian đo được: **công việc phụ thuộc vào bên thứ ba**
mà bạn không kiểm soát được latency, và **thời gian chạy tăng theo dữ liệu khách hàng**, nghĩa là
khách hàng lớn nhất của bạn định nghĩa trường hợp xấu nhất. Cả hai đều có nghĩa đường nhanh hôm nay
là timeout ngày mai.

## Checklist review

- [ ] Thao tác có thời gian chạy tăng theo dữ liệu hoặc phụ thuộc bên thứ ba được làm bất đồng bộ
- [ ] Gửi yêu cầu trả về `202` kèm một handle thao tác bền vững
- [ ] Gửi yêu cầu chấp nhận idempotency key; retry trả về thao tác đã có
- [ ] Trạng thái thao tác được lưu bền, sống sót qua việc worker khởi động lại
- [ ] Trạng thái cuối được giữ đủ lâu để client mất kết nối vẫn lấy được kết quả
- [ ] Lỗi mang theo mã máy đọc được và cờ cho biết có retry được không
- [ ] Endpoint trạng thái rẻ và không tính lại tiến độ
- [ ] Client được báo khoảng thời gian poll (`Retry-After` / `retry_after`)
- [ ] Polling trạng thái được rate limit riêng với việc gửi yêu cầu
- [ ] Worker chết sẽ nhả thao tác của nó (lease hoặc lượt quét phát hiện bị kẹt)
- [ ] Có alert cho thao tác bị kẹt ở trạng thái chưa kết thúc
- [ ] Thời hạn giữ kết quả (`expires_at`) được nêu rõ và ép buộc
- [ ] Có đường polling kể cả khi đã cung cấp webhook

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P8–P9 ([collection](https://www.patreon.com/techcraft_official)). Việc coi giả định "phải
nhận lại ngay kết quả sau cùng" là một cái bẫy, sự chuyển dịch từ quản lý một request sang quản
lý một workflow, và cách nhìn về khả năng chống chịu là của TechCraft; cơ chế resource thao tác và
checklist là của repository này.*
