---
source_sha256: 7d83d60c0ba8e16a4158e6686ecfe9600e10ac5ed247028c7b8936a8bf553a42
---

# Rate Limiting

**Quy tắc: không có giới hạn, sai lầm của một client sẽ thành sự cố của tất cả mọi người.** Rate
limit không phải là thái độ thù địch với consumer — nó là contract cho phép bạn tiếp tục phục vụ
được tất cả bọn họ.

Thuộc nhóm thiết kế API: [hợp đồng & versioning](api-contracts.md) ·
[mô hình hóa resource](api-resource-modeling.md) · [endpoint danh sách](api-list-endpoints.md) ·
[thao tác bất đồng bộ](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
**rate limiting** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Năng lực có hạn, và không chỉ ở CPU](#capacity-is-finite-in-more-ways-than-cpu)
- [Các thuật toán](#the-algorithms)
- [Đặt key theo cái gì](#what-to-key-on)
- [Không phải endpoint nào cũng tốn như nhau](#not-all-endpoints-cost-the-same)
- [Phản hồi cho client bị giới hạn](#responding-to-a-limited-client)
- [Đếm trong hệ phân tán](#distributed-counting)
- [Rate limiting không phải load shedding](#rate-limiting-is-not-load-shedding)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
@app.get("/orders")            # không có bất kỳ giới hạn nào
def list_orders(): ...
```

Vị khách hàng mà ai cũng mong chờ xuất hiện, tích hợp đầy hào hứng, và poll endpoint này trong một
vòng lặp dày đặc từ hai mươi worker. Bug của họ ngốn toàn bộ năng lực database của bạn, và **mọi
khách hàng khác giờ đều sập theo** — kể cả những người không làm gì sai.

> "Cứ trả tiền là tài nguyên không giới hạn."

Niềm tin đó chính là thứ một API không giới hạn dạy cho consumer. Không có chuyện đó: năng lực là
hữu hạn, và câu hỏi duy nhất là nó được phân bổ có chủ đích, hay bị chiếm bởi kẻ nào lặp nhanh nhất.

## Vì sao môi trường dev che giấu lỗi này

Một client, do chính bạn tạo ra, gọi với tốc độ của con người. Không có tenant thứ hai để bị bỏ đói,
không có vòng lặp retry, không có cron cấu hình sai, và không có scraper.

Lỗi này là vấn đề **công bằng**, mà công bằng thì không thể tồn tại khi chỉ có một consumer. Nó xuất
hiện ngay lần đầu hành vi của một consumer không giống như bạn hình dung — điều mà sớm hay muộn
luôn xảy ra.

## Năng lực có hạn, và không chỉ ở CPU

Rate limiting bảo vệ nhiều loại tài nguyên, và chỉ loại đầu tiên là dễ thấy:

- **Tính toán và database** — chi phí trực tiếp.
- **Chi phí tiền bạc** — tiền trả theo từng request cho API bên thứ ba, token LLM, egress, lưu trữ.
  Ở đây một client mất kiểm soát sinh ra hóa đơn chứ không phải sự cố, và điều đó thường tệ hơn vì
  không có gì phát alert.
- **Quota phía downstream** — provider của bạn giới hạn *bạn*. Một tenant đốt hết quota dùng chung
  sẽ tước mất khả năng đó của mọi tenant.
- **Năng lực vận hành** — con người. Mỗi sự cố tiêu tốn sự chú ý, thứ không co giãn được.

Vì vậy giới hạn nên đặt cho bất cứ thứ gì khan hiếm, không chỉ cho những thứ chậm.

## Các thuật toán

| Thuật toán            | Hành vi                                            | Dùng khi                                                                        |
|-----------------------|----------------------------------------------------|---------------------------------------------------------------------------------|
| **Fixed window**      | Đếm theo từng cửa sổ thời gian cố định             | Đơn giản nhất; cho phép burst gấp 2 lần ở ranh giới cửa sổ                      |
| **Sliding window**    | Đếm trên một khoảng thời gian trượt                | Làm mượt burst ở ranh giới; cần lưu nhiều trạng thái hơn                        |
| **Token bucket**      | Token được nạp lại theo tốc độ; mỗi request tiêu một | **Mặc định tốt nhất** — cho phép burst tới kích thước bucket, tốc độ duy trì bị giới hạn |
| **Leaky bucket**      | Hàng đợi được xả với tốc độ cố định                | Cần đầu ra đều đặn tới một downstream mong manh                                 |
| **Concurrency limit** | N request đang chạy, không phải N mỗi giây         | Request dài hoặc thời gian thất thường, khi thời gian chạy quan trọng hơn số lượng |

Hai ghi chú đáng nhớ. **Fixed window có một lỗ hổng thật ở ranh giới**: client có thể gửi toàn bộ hạn
mức lúc 10:59:59 rồi gửi lại lúc 11:00:00, dồn gấp 2 lần giới hạn vào một khoảnh khắc. Và
**concurrency limit thường mới là thứ bạn thực sự cần** — với các endpoint đắt, ba báo cáo nặng chạy
đồng thời quan trọng hơn số request mỗi phút, và nó ánh xạ thẳng vào năng lực của pool (xem
[connection-pools-and-latency](connection-pools-and-latency.md)).

Token bucket là lựa chọn mặc định đúng vì burst là chính đáng: một client đồng bộ lại sau thời gian
ngừng hoạt động nên được phép bắt kịp, rồi sau đó bị giữ ở tốc độ duy trì.

## Đặt key theo cái gì

Key quyết định ai được cô lập khỏi ai, và chọn sai thì giới hạn trở nên vô dụng:

- **API key / tenant** — mặc định đúng cho một API nghiệp vụ. Mỗi khách hàng có ngân sách riêng.
- **User id** — cho các API hướng tới người dùng, nơi một tenant có nhiều người dùng.
- **IP** — chỉ cho traffic chưa xác thực. Đây là một định danh kém: NAT đặt hàng nghìn người dùng sau
  một địa chỉ, còn kẻ tấn công thì xoay địa chỉ thoải mái.
- **Endpoint + key** — để traffic của một endpoint rẻ không thể vét cạn ngân sách của một endpoint
  đắt.

**Giới hạn theo hạng là mô hình trung thực** cho một API multi-tenant: free, standard và enterprise
có ngân sách khác nhau, được nêu trong contract. Điều đó biến câu "chúng tôi đã throttle bạn" từ một
cuộc tranh cãi thành một kế hoạch.

Và hãy bảo vệ riêng bề mặt trước xác thực. Các endpoint đăng nhập, đăng ký, đặt lại mật khẩu và cấp
token cần giới hạn **chặt hơn**, đặt key theo IP *và* tài khoản, vì chúng là mục tiêu lạm dụng và kẻ
tấn công không có API key nào để mà giới hạn.

## Không phải endpoint nào cũng tốn như nhau

Một giới hạn phẳng theo số request mỗi phút coi một lần tra cứu theo khóa và một báo cáo năm năm là
như nhau. Chúng không như nhau.

**Giới hạn có trọng số theo chi phí** tính cho mỗi request đúng cái giá của nó — một lần tra cứu tốn
1, một danh sách có filter tốn 10, một báo cáo tốn 500. Đây là cách tránh hai failure mode của giới
hạn phẳng: hoặc nó đủ lỏng để endpoint đắt gây hại cho bạn, hoặc đủ chặt để khiến các lời gọi rẻ khổ
sở một cách không cần thiết.

Khi có thể, hãy đưa các dạng query đắt ra sau một [thao tác bất đồng bộ](api-async-operations.md) và
rate limit *việc gửi yêu cầu*. Cách đó cho ra một hàng đợi có giới hạn thay vì một request bị từ
chối, và đó là câu trả lời tốt hơn cho cả hai phía.

## Phản hồi cho client bị giới hạn

Từ chối thôi là chưa đủ; client cần biết phải làm gì:

```http
429 Too Many Requests
Retry-After: 30
RateLimit-Limit: 1000
RateLimit-Remaining: 0
RateLimit-Reset: 1756640430

{ "error": { "code": "rate_limit_exceeded",
             "message": "Limit of 1000 requests/hour exceeded.",
             "retry_after": 30 } }
```

- **`429`, không phải `503`.** `503` nói *tôi đang hỏng*; `429` nói *bạn đã vượt ngân sách*.
  Consumer, và circuit breaker của họ, xử lý hai mã này khác nhau — và một `503` sẽ kích hoạt một
  [circuit breaker](circuit-breakers-and-bulkheads.md) lẽ ra không nên bị kích hoạt.
- **Luôn gửi `Retry-After`.** Thiếu nó, một client có thiện chí sẽ retry ngay lập tức và làm tình
  hình tệ hơn.
- **Phơi ra ngân sách còn lại trên các response thành công**, để client tự điều tiết trước khi bị từ
  chối. Đây là khác biệt giữa một giới hạn mà client hợp tác cùng và một giới hạn mà client đâm sầm
  vào.
- **Đừng bao giờ để `429` diễn ra trong im lặng.** Nếu một client bị giới hạn liên tục, hãy báo cho
  ai đó — cùng lập luận với việc giao webhook thất bại.

## Đếm trong hệ phân tán

Với N instance, một bộ đếm riêng cho mỗi instance cho ra gấp N lần giới hạn dự định. Các lựa chọn:

- **Bộ đếm dùng chung trên Redis** — chính xác, nhưng thêm một chặng mạng và một phụ thuộc vào mọi
  request. Dùng phép tăng atomic có expiry; `GET` rồi `SET` là một race condition, theo
  [transaction-isolation](transaction-isolation.md).
- **Bộ đếm local với giới hạn/N** — không có phụ thuộc, nhưng không công bằng khi load balancing
  không đều.
- **Local kèm đồng bộ định kỳ** — xấp xỉ, rẻ, và thường là đủ tốt.
- **Ở edge** — API gateway, CDN, hoặc service mesh. Từ chối trước khi kịp tiêu tốn bất kỳ năng lực
  nào của bạn, đây là hình thức mạnh nhất.

Quyết định thiết kế cốt lõi: **fail open hay fail closed** khi nơi lưu trữ của limiter không khả
dụng. Fail closed biến một lần Redis chập chờn thành một sự cố toàn diện; fail open để bạn không được
bảo vệ đúng trong sự cố mà sự bảo vệ quan trọng nhất. Với phần lớn API, hãy fail open kèm một
phương án dự phòng local thận trọng và alert thật to.

## Rate limiting không phải load shedding

Hai công cụ liên quan, thường bị nhầm lẫn với nhau:

- **Rate limiting** là về *công bằng*, áp dụng theo từng client, phần lớn không phụ thuộc vào tải
  hiện tại. Một tenant vượt ngân sách bị từ chối kể cả khi hệ thống đang rảnh.
- **Load shedding** là về *sống sót*, áp dụng khi hệ thống quá tải, bất kể ngân sách của ai còn lại.
  Nó từ chối những công việc bạn không thể hoàn thành kịp thời.

Bạn cần cả hai. Chỉ có rate limit sẽ không cứu bạn khỏi tổng traffic hợp lệ; chỉ có shedding thì để
một tenant bỏ đói các tenant khác. Và hãy shed **trước khi** hàng đợi trở nên sâu — công việc mà
client của nó đã timeout là năng lực tiêu vào chỗ không đâu, cùng kiểu lãng phí với stampede trong
[caching](caching.md).

## Checklist review

- [ ] Mọi endpoint công khai đều có rate limit; không có đường nào không giới hạn
- [ ] Giới hạn được đặt key theo tenant/API key, không chỉ theo IP
- [ ] Endpoint xác thực (đăng nhập, đăng ký, đặt lại mật khẩu) có giới hạn chặt hơn theo IP **và** tài khoản
- [ ] Endpoint đắt được giới hạn theo trọng số chi phí hoặc concurrency, không theo số request phẳng
- [ ] Giới hạn được đếm đúng trên nhiều instance (nơi lưu trữ dùng chung hoặc gateway)
- [ ] Fail open hay fail closed khi limiter không khả dụng là một lựa chọn có chủ đích và được ghi tài liệu
- [ ] Trả về `429` — không phải `503` — kèm `Retry-After`
- [ ] Ngân sách còn lại được phơi ra trên các response thành công
- [ ] Giới hạn và các hạng được ghi tài liệu như một phần của contract API
- [ ] Một client bị giới hạn liên tục sẽ kích hoạt alert hoặc thông báo
- [ ] Có load shedding bên cạnh rate limiting, và shed trước khi hàng đợi trở nên sâu
- [ ] Client nội bộ và client dạng máy cũng bị giới hạn, với ngân sách riêng

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P12 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn rate limiting như một
contract ở production, quan niệm sai lầm "cứ trả tiền là tài nguyên không giới hạn", và việc coi
năng lực vận hành là một tài nguyên có hạn là của TechCraft; phần so sánh thuật toán, thiết kế
response và checklist là của repository này.*
