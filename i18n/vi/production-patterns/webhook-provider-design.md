---
source_sha256: 3ce7f910b8d2a513845e4d268646218ce2c10f20cc54d97e5e948df805b68a92
---

# Thiết kế phía cung cấp webhook

**Quy tắc: gửi một webhook một lần không có nghĩa là đã giao được nó.** Không có chữ ký, retry và
cách để phát lại, thứ bạn xây ra là một thông báo mà consumer không thể tin tưởng hay đối soát được.

Thuộc nhóm thiết kế API: [hợp đồng & versioning](api-contracts.md) ·
[mô hình hóa resource](api-resource-modeling.md) · [endpoint danh sách](api-list-endpoints.md) ·
[thao tác bất đồng bộ](api-async-operations.md) · **webhook provider** (trang này) ·
[rate limiting](rate-limiting.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Ký nó, và để họ xác minh](#sign-it-and-let-them-verify)
- [Retry có backoff, và bỏ cuộc một cách có chủ đích](#retry-with-backoff-and-give-up-deliberately)
- [Một consumer chậm không được làm bạn chậm theo](#a-slow-consumer-must-not-slow-you)
- [Thiết kế payload](#payload-design)
- [Thứ tự và bản trùng là việc của consumer — hãy giúp họ](#ordering-and-duplicates-are-the-consumers-problem--help-them)
- [Cho họ cách để bắt kịp](#give-them-a-way-to-catch-up)
- [Làm một bên gửi đi tử tế](#being-a-good-outbound-citizen)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
def on_payment_succeeded(payment):
    requests.post(customer.webhook_url,  # nằm trong transaction
                  json={"event": "payment.succeeded",
                        "payment_id": payment.id},
                  timeout=30)  # blocking, 30s
```

Mọi thứ có thể sai đều sai: endpoint của consumer sập và event **mất vĩnh viễn**; payload không có
chữ ký nên bất kỳ ai biết URL đều có thể giả mạo; timeout 30 giây trên một worker dùng chung nghĩa là
một consumer chậm chiếm hết năng lực xử lý của bạn; và nó nằm trong một transaction, nên một lần
rollback sẽ thông báo một việc chưa từng xảy ra.

Đây là [payment-provider-and-webhooks](payment-provider-and-webhooks.md) nhìn từ phía bên kia. Bài
đó dặn consumer đừng tin webhook. **Bài này nói về việc xứng đáng được tin.**

## Vì sao môi trường dev che giấu lỗi này

Consumer dùng để test là một endpoint local luôn trả `200` trong một mili giây. Nó không bao giờ
sập, không bao giờ timeout, không bao giờ trả `500`, và không bao giờ nhận một event hai lần — nên
một cách gửi kiểu bắn rồi quên ngây thơ trông có vẻ đầy đủ và đúng đắn.

Consumer thật thì deploy, khởi động lại, hết quota, có bug, và offline hàng giờ. Độ tin cậy của việc
giao nhận là một đặc tính của hạ tầng *của họ*, mà đó chính là thứ bạn không thể test.

## Ký nó, và để họ xác minh

Endpoint webhook là một URL công khai. Một body JSON trông có vẻ đúng không chứng minh được gì, nên
hãy ký mọi request:

```
X-Webhook-Signature: t=1756640400,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8bd
```

Ký `timestamp + "." + raw_body` bằng HMAC-SHA256 với một secret riêng cho từng consumer. Những chi
tiết quan trọng:

- **Đưa timestamp vào phần được ký**, để một request bị bắt được không thể bị phát lại về sau. Dặn
  consumer từ chối mọi thứ nằm ngoài một cửa sổ dung sai (thường là năm phút).
- **Ký trên các byte thô**, không phải một object đã serialize lại — bất kỳ khác biệt nào về thứ tự
  key hay khoảng trắng đều làm hỏng việc xác minh, và nó sẽ hỏng ở đúng một consumer có parser khác
  biệt.
- **Hỗ trợ hai secret cùng hoạt động** để việc xoay vòng secret không đòi hỏi hai bên phải deploy
  cùng lúc.
- **Ghi tài liệu chính xác về thuật toán**, kèm một đoạn code mẫu để xác minh. Một cơ chế chữ ký mà
  consumer không cài đặt được sẽ bị bỏ qua, và khi đó nó chẳng bảo vệ được ai.

## Retry có backoff, và bỏ cuộc một cách có chủ đích

Chỉ gửi một lần nghĩa là việc giao nhận phụ thuộc vào chuyện consumer có đang chạy đúng lúc đó hay
không. Hãy retry khi lỗi kết nối, timeout, `5xx` và `429`:

```
attempt 1   immediately
attempt 2   +10s      exponential, with jitter
attempt 3   +1m
attempt 4   +10m
attempt 5   +1h
attempt 6   +6h
then        dead-letter, and notify the consumer out of band
```

**Jitter không phải là tùy chọn.** Một sự cố chung nghĩa là hàng nghìn lượt giao đang chờ; không có
jitter, chúng retry đồng loạt và bạn dội một cơn lũ đồng bộ vào consumer ngay khi họ vừa hồi phục —
xem [timeouts-and-retries](timeouts-and-retries.md).

**Đừng** retry các `4xx` khác `429`: một `400` hay `422` nghĩa là consumer đã từ chối payload và sẽ
từ chối y hệt như vậy mãi mãi.

Và retry phải có điểm dừng. Hãy định nghĩa nó, phơi ra trạng thái cuối của lượt giao, và **báo cho
consumer biết họ đang lỗi** — bằng email hoặc alert trên dashboard, không phải im lặng. Một consumer
ba tuần sau mới phát hiện mình đã ngừng nhận event là một sự cố hỗ trợ mà lẽ ra bạn đã ngăn được.

## Một consumer chậm không được làm bạn chậm theo

Việc giao nhận phải **bất đồng bộ và được cô lập**, vì bạn đang gọi vào hạ tầng mà bạn không kiểm
soát:

- **Không bao giờ gửi bên trong một database transaction.** Đưa vào hàng đợi qua outbox; transaction
  commit, rồi một worker mới giao đi. Xem [message-delivery-semantics](message-delivery-semantics.md).
- **Không bao giờ gửi trên request path.** Lời gọi API kích hoạt event không được phải chờ một bên
  thứ ba.
- **Timeout ngắn** — 5–10 giây là quá đủ. Một consumer cần tới 30 giây thì nên xác nhận nhanh và xử
  lý bất đồng bộ, đó chính là lời khuyên bạn dành cho họ.
- **Cô lập theo từng consumer.** Lỗi của một consumer không được nuốt hết pool giao nhận dùng chung —
  một bulkhead, giới hạn concurrency theo từng đích, và lý tưởng là một circuit breaker ngừng dội vào
  một endpoint đã chết. Xem [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).

## Thiết kế payload

- **Kèm event id và event type**, và giữ cả hai ổn định. Id là thứ consumer dùng để khử trùng lặp.
- **Kèm timestamp của event**, không phải của lượt gửi — một lần retry một tiếng sau không được trông
  như một event mới.
- **Ưu tiên một tham chiếu kèm dữ liệu tối thiểu hơn là một snapshot đầy đủ.** Một object lớn nhúng
  sẵn đã cũ khi tới nơi, và khuyến khích consumer coi nó là nguồn chân lý. Gửi
  `{payment_id, status}` rồi để họ tự lấy trạng thái hiện tại sẽ tránh được điều đó; gửi mọi thứ thì
  giúp họ bớt một lời gọi. Hãy nói rõ bạn đang làm theo cách nào, vì nó thay đổi cách họ phải đối xử
  với dữ liệu.
- **Không bao giờ đưa secret hay PII đầy đủ** vào payload được giao tới một URL có thể bị proxy ghi
  log.
- **Version payload**, và đối xử với hình dạng của nó cẩn thận như mọi contract khác — schema của
  webhook chính là một API. Xem [api-contracts](api-contracts.md).

## Thứ tự và bản trùng là việc của consumer — hãy giúp họ

Bạn không thể đảm bảo thứ tự qua các lần retry: một event được retry một tiếng sau sẽ tới sau những
event xảy ra muộn hơn nó. Thay vì giả vờ điều ngược lại:

- **Nói thẳng rằng việc giao nhận là at-least-once và không theo thứ tự.** Consumer biết điều này sẽ
  xây dựng đúng; consumer giả định khác đi sẽ xây ra bug.
- **Cung cấp một số thứ tự tăng đơn điệu** theo từng subscription khi có thể, để consumer phát hiện
  được khoảng trống và event đến sai thứ tự.
- **Giữ event id ổn định qua các lần retry** — cùng một lượt giao được retry phải mang cùng id, nếu
  không việc khử trùng lặp phía consumer không thể hoạt động.
- **Làm cho event tự đủ thông tin về trạng thái.** Kèm trạng thái của resource tại thời điểm xảy ra
  event để consumer khi áp dụng một event đến sai thứ tự có thể nhận ra nó đã cũ.

## Cho họ cách để bắt kịp

Phần mà hầu hết provider bỏ qua, và cũng là phần biến webhook từ một gánh nặng thành một hệ thống:

- **Một endpoint danh sách cho các event đã qua** (`GET /events?since=...`), để consumer từng bị sập
  có thể tự đối soát mà không cần liên hệ hỗ trợ.
- **Phát lại thủ công** — cho họ yêu cầu giao lại một event cụ thể hoặc một khoảng event, từ
  dashboard hoặc qua một endpoint.
- **Lịch sử giao nhận theo từng event** — các lần thử, mã response, và thời gian. Đây là thứ kết thúc
  cuộc tranh cãi "chúng tôi chưa bao giờ nhận được" bằng một query thay vì mất cả ngày.

Webhook là một tối ưu dạng push đặt trên một nguồn chân lý dạng pull. Nếu con đường duy nhất để có
một event là một lần push mà bạn có thể đã giao thất bại, consumer của bạn không bao giờ có thể đúng.

## Làm một bên gửi đi tử tế

- **Công bố các IP nguồn** hoặc cung cấp egress tĩnh, để consumer có thể đưa vào allowlist.
- **Dùng một `User-Agent` ổn định** cho biết service và version của bạn.
- **Cho phép cấu hình URL theo từng môi trường**, và xác minh quyền sở hữu khi URL được đặt — một
  webhook URL chưa xác minh là một lỗ hổng SSRF, và một worker giao nhận đi theo redirect có thể bị
  chĩa vào các service nội bộ. Không đi theo redirect; resolve địa chỉ và từ chối các dải địa chỉ
  private.
- **Cho consumer đăng ký theo event type** thay vì nhận tất cả.

## Checklist review

- [ ] Mọi webhook đều được ký (HMAC trên timestamp + body thô) với secret riêng cho từng consumer
- [ ] Timestamp được ký và consumer được dặn ép buộc một cửa sổ dung sai
- [ ] Hai secret có thể cùng hoạt động để xoay vòng
- [ ] Giao nhận bất đồng bộ qua outbox — không bao giờ bên trong transaction hay trên request path
- [ ] Retry dùng exponential backoff **có jitter**, và có trạng thái cuối được định nghĩa
- [ ] Các response `4xx` khác `429` không được retry
- [ ] Consumer được báo qua kênh khác khi việc giao nhận thất bại kéo dài
- [ ] Cô lập theo từng consumer ngăn một endpoint hỏng nuốt hết năng lực dùng chung
- [ ] Timeout giao nhận ngắn (5–10s)
- [ ] Payload mang event id ổn định và timestamp của event; id ổn định qua các lần retry
- [ ] Giao nhận at-least-once và không theo thứ tự được ghi tài liệu, lý tưởng là kèm số thứ tự
- [ ] Không có secret hay PII đầy đủ trong payload
- [ ] Có endpoint liệt kê/phát lại event để consumer tự bắt kịp
- [ ] Lịch sử giao nhận (các lần thử, status code) truy vấn được
- [ ] Webhook URL được xác minh; không đi theo redirect và từ chối các dải địa chỉ private

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P10 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "webhook không đáng
tin nếu thiếu retry và chữ ký" và khoảng cách giữa một callback để demo với một hệ thống giao nhận ở
production là của TechCraft; cơ chế ký, lịch retry, các endpoint bắt kịp và checklist là của
repository này.*
