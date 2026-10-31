---
source_sha256: 2e6deeb9f37212641eafd9e66d1382b6539894fb767e867ccf2931195fd66a7a
---

# Provider thanh toán & Webhook

**Quy tắc: response của provider và webhook là tín hiệu từ bên ngoài, không phải sự thật.** Nguồn
sự thật duy nhất là state machine của chính bạn, và `timeout` nghĩa là UNKNOWN — không bao giờ là
thất bại.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
**provider & webhook** (trang này) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Timeout nghĩa là UNKNOWN](#timeout-means-unknown)
- [Từ chối dứt khoát và lỗi tạm thời](#hard-decline-vs-temporary-failure)
- [Xử lý trạng thái UNKNOWN](#resolving-unknown)
- [Pipeline xử lý webhook](#the-webhook-pipeline)
- [Webhook đến muộn, đến hai lần, và sai thứ tự](#webhooks-arrive-late-twice-and-out-of-order)
- [Webhook cho giao dịch không xác định](#the-unknown-transaction-webhook)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
try:
    result = provider.charge(amount)
except TimeoutError:
    mark_payment_failed(payment_id)        # ← giả định đắt giá
    return {"error": "Payment failed, please try again"}
```

Request bị timeout ở chặng của *bạn*. Nhưng chuỗi xử lý vẫn tiếp tục phía sau:

```
Your service  ──▶  Provider  ──▶  Bank / Card network
      ▲
   timeout here says nothing about what happened downstream
```

Ngân hàng có thể đã trừ tiền khách. Vậy mà bạn lại báo với họ là thất bại và mời họ thử lại — và
nếu lần thử lại đó không mang idempotency key, họ trả tiền hai lần.

Phiên bản tương tự với webhook:

```python
@app.post("/webhook")
def webhook(payload):
    payment = db.get(payload["payment_id"])
    payment.status = payload["status"]      # không kiểm tra chữ ký, không khử trùng lặp,
    db.save(payment)                        # không kiểm tra chuyển trạng thái
    send_receipt_email(payment)             # việc chậm nằm ngay trong request
    return 200
```

Bốn lỗi: ai cũng có thể POST vào đây, một lần gửi trùng sẽ bị xử lý hai lần, một event đến sai thứ
tự sẽ ghi đè lên trạng thái mới hơn, và việc gửi email chậm khiến provider bị timeout rồi retry.

## Vì sao môi trường dev che giấu lỗi này

Stub trả về tức thì và luôn thành công, nên `TimeoutError` không bao giờ xảy ra và UNKNOWN không hề
tồn tại. Webhook ở môi trường dev thường được bắn tay một lần, đúng thứ tự, từ một terminal đáng tin
cậy — nghĩa là không kiểm thử được gì trong số các tình huống gửi trùng, đảo thứ tự, phát lại hay
giả mạo.

## Timeout nghĩa là UNKNOWN

> Với payment, kết quả đáng sợ nhất chưa bao giờ là "failed" — mà là "không rõ chuyện gì đã
> xảy ra".

Hãy biến nó thành một trạng thái thực sự. `unknown` hoặc `pending_verification` phải có mặt trong
state machine, và nó phải chặn mọi hành động vốn giả định đã có kết quả: không giao hàng, không báo
lỗi cho người dùng, không retry nếu thiếu key.

Coi unknown là thất bại chính là nguyên nhân dẫn tới hai kết cục tệ nhất trong thanh toán — khách
bị trừ tiền mà không nhận được gì, hoặc khách bị trừ tiền hai lần.

## Từ chối dứt khoát và lỗi tạm thời

Gộp mọi lỗi thành "thất bại" là vứt bỏ chính sự phân biệt quyết định bước tiếp theo:

| Loại                  | Ví dụ                                              | Hành động                                      |
|-----------------------|----------------------------------------------------|------------------------------------------------|
| **Từ chối dứt khoát** | Thẻ đã quá hạn, tài khoản thiếu tiền, bị khóa do nghi gian lận | Trạng thái cuối. Đánh dấu `failed` một cách chắc chắn. |
| **Lỗi tạm thời**      | Mạng chập chờn, provider đang bảo trì, dính rate limit | **UNKNOWN.** Xác minh trước khi quyết định.    |

Một lần từ chối dứt khoát là thông tin đến từ ngân hàng — giao dịch chắc chắn đã không xảy ra. Còn
lỗi tạm thời là việc bạn thiếu thông tin về chính request của mình.

## Xử lý trạng thái UNKNOWN

Ba cơ chế, xếp theo độ tin cậy giảm dần:

1. **Gửi idempotency key cho provider.** Giúp retry an toàn: gọi mười lần, chỉ trừ tiền một lần.
   Đây là tuyến phòng thủ chính — xem [trạng thái & idempotency](payment-state-and-idempotency.md).
2. **Chủ động hỏi provider.** Gọi `GET /payments/{id}` để biết chuyện gì đã xảy ra, thay vì ngồi
   chờ. Đây là cách giải quyết UNKNOWN nhanh nhất.
3. **Webhook.** Hữu ích, nhưng kém tin cậy nhất: có thể đến muộn, trùng lặp, sai thứ tự và bị giả
   mạo.

Và phía sau cả ba là **đối soát**, tấm lưới cuối cùng — xem
[nhất quán với đơn hàng](payment-order-consistency.md).

## Pipeline xử lý webhook

Webhook handler là một ranh giới bảo mật và là cửa nạp vào queue, không phải nơi đặt business
logic. Thứ tự của năm bước dưới đây chính là thiết kế:

```
1. Verify signature      reject anything unsigned or stale
2. Store raw event       persist before interpreting
3. Dedupe                on provider_event_id
4. Enqueue job           hand off to a worker
5. Return 200 OK         fast, before any business logic runs
```

**1 — Xác minh.** Endpoint là public, nên một body JSON trông hợp lý không chứng minh được điều gì.
Hãy xác minh chữ ký của provider hoặc shared secret. Đồng thời áp dụng **giới hạn sai lệch
timestamp**: thiếu nó, kẻ tấn công chỉ cần bắt được một webhook hợp lệ là có thể phát lại mãi mãi.
Bổ sung IP allowlist và rate limiting ở tầng gateway nếu provider hỗ trợ.

**2 — Lưu bản thô trước.** Ghi lại payload nguyên vẹn ngay lập tức, trước mọi bước parse hay
business logic. Nhờ vậy bạn có thể phát lại event sau khi sửa lỗi handler mà không phải nhờ provider
gửi lại. Dùng một bảng `webhook_events` chứa body thô, event ID của provider, thời điểm nhận và
trạng thái xử lý.

**3 — Khử trùng lặp theo `provider_event_id`.** Provider sẽ retry. Không khử trùng lặp thì bạn đánh
dấu đã thanh toán hai lần, gửi email hai lần, và — tệ nhất — giao hàng hai lần. Nếu event đã được xử
lý rồi, hãy trả về thành công để provider ngừng retry.

**4 và 5 — Xác nhận nhanh, xử lý bất đồng bộ.** Provider có timeout ngắn và sẽ retry bất cứ thứ gì
trông giống lỗi, nên việc chậm trong handler sẽ tạo ra một cơn bão retry đúng vào lúc hệ thống của
bạn đang chật vật. Hãy đưa vào queue và trả 200 ngay. Cách này cũng tách lỗi hạ tầng (khâu nhận) khỏi
lỗi nghiệp vụ (khâu xử lý), để mỗi bên retry độc lập.

## Webhook đến muộn, đến hai lần, và sai thứ tự

Đừng bao giờ giả định thứ tự giao nhận. `refund_succeeded` có thể đến trước `payment_succeeded`; một
event cũ có thể đến sau khi trạng thái mới hơn đã được ghi.

Vì vậy webhook không *gán* trạng thái — nó **yêu cầu một lần chuyển trạng thái**, và yêu cầu đó phải
được kiểm tra:

```
current_state + event_type  ->  is this transition legal?
```

Hợp lệ: `pending → succeeded`. Không hợp lệ: `succeeded → pending`, `refunded → succeeded`. Một event
dẫn tới chuyển trạng thái không hợp lệ sẽ được ghi lại rồi bỏ qua, không được áp dụng.

Vì hai webhook có thể đến đồng thời, bước kiểm tra-và-cập nhật phải nguyên tử — một `UPDATE ...
WHERE status = :expected` có điều kiện kèm kiểm tra số dòng bị ảnh hưởng, hoặc khóa dòng. Một phép
kiểm tra đọc trạng thái rồi ghi lại trong hai câu lệnh riêng là đúng kiểu race condition gặp ở mọi
nơi khác.

## Webhook cho giao dịch không xác định

Bạn sẽ nhận được webhook cho những khoản thanh toán không có trong database. **Đừng trả 404 rồi vứt
chúng đi.**

Nguyên nhân thường gặp là **độ trễ ánh xạ**: provider đã xử lý một khoản thanh toán qua redirect và
bắn webhook trước khi lệnh ghi đơn hàng của chính bạn kịp hoàn tất. Giao dịch là thật, và bạn sắp
đánh mất nó.

Hãy lưu event lại, retry với exponential backoff, và nếu sau vài lần vẫn không khớp được thì đẩy vào
một **queue đối soát** để người xử lý. Một webhook không khớp là tín hiệu về một khoản tiền có thật;
vứt nó đi là biến một sai lệch về thời điểm thành doanh thu bị mất.

## Checklist review

- [ ] Timeout được coi là UNKNOWN và mô hình hóa thành một trạng thái thực sự, không bao giờ là thất bại
- [ ] Từ chối dứt khoát và lỗi tạm thời được phân loại khác nhau
- [ ] UNKNOWN được giải quyết bằng cách truy vấn trạng thái từ provider, không phải trừ tiền lại
- [ ] Chữ ký webhook được xác minh, có giới hạn sai lệch timestamp để chống phát lại
- [ ] Payload webhook thô được lưu trước mọi bước diễn giải
- [ ] Event được khử trùng lặp theo `provider_event_id`
- [ ] Handler trả 200 ngay lập tức; business logic chạy trong worker
- [ ] Webhook chỉ yêu cầu một lần chuyển trạng thái đã được kiểm tra; không bao giờ gán trạng thái trực tiếp
- [ ] Chuyển trạng thái không hợp lệ từ event cũ hoặc sai thứ tự bị từ chối và được ghi lại
- [ ] Xử lý webhook đồng thời là nguyên tử (update có điều kiện hoặc khóa dòng)
- [ ] Webhook không khớp được lưu lại, retry với backoff, rồi chuyển lên người xử lý — không bao giờ bị bỏ
- [ ] Response thô của provider được giữ lại để audit và phát lại

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P4–P5 của
TechCraft ([collection](https://www.patreon.com/collection/2186651)). Cách nhìn về trạng thái
UNKNOWN, cách phân loại lỗi và pipeline webhook năm bước là của TechCraft; cấu trúc review và
checklist là của repository này.*
