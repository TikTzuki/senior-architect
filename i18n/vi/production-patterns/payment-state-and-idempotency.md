---
source_sha256: db197267b68b833178f75319bca1bc272fdf331cd8d20d0e09955cc3291f23ce
---

# Trạng thái thanh toán & Idempotency

**Quy tắc: một khoản thanh toán là một state machine kèm idempotency key, không bao giờ là một
boolean.** `paid = true` không thể biểu diễn một giao dịch mà bạn chưa biết kết quả.

Thuộc nhóm thanh toán: **trạng thái & idempotency** (trang này) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [State machine](#the-state-machine)
- [Đừng sao chép trạng thái của provider](#never-copy-the-providers-states)
- [Một đơn hàng, nhiều lần thanh toán](#one-order-many-payment-attempts)
- [Idempotency: key và response đã lưu](#idempotency-the-key-and-the-stored-response)
- [Request hash: cùng key phải là cùng request](#request-hash-same-key-must-mean-same-request)
- [Lỗ hổng concurrency](#the-concurrency-hole)
- [Audit trail](#audit-trail)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# Hai lỗi riêng biệt trong bốn dòng.
def pay(order_id, amount):
    charge = provider.charge(amount)          # không có idempotency key
    if charge.status == "success":
        db.execute("UPDATE orders SET paid = true WHERE id = ?", order_id)
```

**Lỗi thứ nhất — boolean.** `paid` không có giá trị nào mang nghĩa "đã gửi lệnh trừ tiền nhưng
chưa biết kết quả". Vì vậy ngay khi provider phản hồi chậm, hệ thống buộc phải nói dối: hoặc báo
thất bại (trong khi người dùng đã bị trừ tiền), hoặc báo thành công (trong khi họ chưa bị trừ).

**Lỗi thứ hai — không có idempotency.** Người dùng bấm hai lần, hoặc client retry khi timeout, và
`provider.charge()` chạy hai lần. Hai lần trừ tiền cho một đơn hàng.

Cả hai đều vô hình khi đọc code. Nó trông như một happy path hoàn toàn đúng.

## Vì sao môi trường dev che giấu lỗi này

Một provider giả lập ở local trả lời trong một mili giây và luôn thành công. Không có timeout để
sinh ra trạng thái "chưa biết", không có cú click thứ hai trong lúc request đang chạy, và không có
retry. Mọi chuyển trạng thái quan sát được ở local đều là `created → succeeded` — đúng con đường duy
nhất không cần thiết kế gì.

Lỗi chỉ xuất hiện khi có độ trễ, concurrency, hoặc provider gặp sự cố — những thứ môi trường dev
không bao giờ cung cấp.

## State machine

Tiền đi qua các trạng thái, và chính các trạng thái trung gian mới là điều quan trọng:

| Trạng thái           | Ý nghĩa                                                   |
|----------------------|-----------------------------------------------------------|
| `created`            | Bản ghi đã tồn tại, đã sinh ID nội bộ, chưa gửi gì đi      |
| `pending`            | Đã gửi tới provider, đang chờ kết quả                     |
| `succeeded`          | Trạng thái cuối. Đã thu được tiền.                        |
| `failed`             | Trạng thái cuối. Bị từ chối.                              |
| `cancelled`          | Trạng thái cuối. Bị bỏ hoặc hết hạn trước khi xử lý.       |
| `refunded`           | Đã hoàn lại sau khi thành công                            |
| `partially_refunded` | Đã hoàn lại một phần số tiền                              |

`created` quan trọng hơn vẻ ngoài: đó là nơi ID nội bộ và idempotency key được sinh ra, **trước**
mọi lời gọi tới provider. Bạn không thể biến một lời gọi thành idempotent sau khi đã gọi.

**Ép buộc các chuyển trạng thái hợp lệ, và ép buộc ngay trong database:**

```sql
UPDATE payments SET status = 'succeeded'
WHERE id = :id AND status = 'pending';    -- sau đó kiểm tra số dòng bị ảnh hưởng
```

- Hợp lệ: `created→pending`, `pending→succeeded`, `pending→failed`, `succeeded→refunded`
- **Không hợp lệ: `failed→succeeded`, `refunded→succeeded`, `cancelled→succeeded`**

Trạng thái cuối là trạng thái cuối. Một webhook đến muộn báo thành công cho một khoản `failed`
phải bị từ chối, không được áp dụng — xem [provider & webhook](payment-provider-and-webhooks.md).
Nếu người dùng muốn thử lại, đó là một **lần thanh toán mới**, không phải hồi sinh lần cũ.

**Không bao giờ mở khóa sản phẩm ở trạng thái `pending`.** Giao hàng hay kích hoạt dịch vụ đòi hỏi
`succeeded`.

## Đừng sao chép trạng thái của provider

Stripe có `authorized`/`captured`, các provider khác có `settled`, VNPay và MoMo có bộ trạng thái
riêng. Lưu thẳng chúng vào hệ thống sẽ trói domain của bạn vào từ vựng của một nhà cung cấp, và vỡ
ngay khi bạn thêm provider thứ hai.

Giữ trạng thái nội bộ của riêng bạn và **ánh xạ** trạng thái provider vào đó. Lưu nguyên trạng thái
thô của provider bên cạnh, không ánh xạ, để phục vụ audit — nhưng logic chỉ được rẽ nhánh theo
trạng thái nội bộ.

## Một đơn hàng, nhiều lần thanh toán

Mô hình đơn hàng → thanh toán là **1:N, không phải 1:1.** Một thẻ bị từ chối, người dùng thử thẻ
khác và thành công. Họ không nên phải tạo đơn hàng mới, và bạn phải giữ lại cả hai lần thử.

Lịch sử đó là thứ giúp bạn trả lời "khách hàng này thực sự đã thử những gì?" khi có tranh chấp, và là
đầu vào bắt buộc cho đối soát.

## Idempotency: key và response đã lưu

Retry không phải trường hợp hiếm — đó là hành vi bình thường của mạng. Idempotency là thứ biến một
lần retry từ một lần trừ tiền trùng thành một lần phục hồi an toàn.

Mỗi lần thanh toán mang một **idempotency key** duy nhất, sinh ra ở `created` (ví dụ
`checkout_123_attempt_1`, hoặc một UUID). Mọi lần retry của lần thanh toán đó gửi *cùng* key.

Phía server không chỉ kiểm tra key có tồn tại hay không — bạn phải lưu và trả lại đúng response:

| Cột               | Mục đích                                    |
|-------------------|---------------------------------------------|
| `idempotency_key` | **UNIQUE.** Danh tính của lần thanh toán.    |
| `request_hash`    | Dấu vân tay của body request                |
| `status`          | in-progress / completed                     |
| `response_body`   | Chính xác response đã trả về lần đầu         |

- **Chưa có key:** xử lý, gọi provider, lưu response, trả về.
- **Đã có key:** **không** chạy lại logic thanh toán. Trả về nguyên văn `response_body` đã lưu —
  cùng `payment_id`, cùng timestamp. Phía gọi không được phép phân biệt được lần retry với lần gốc.

Trả về một response vừa tính lại thay vì response đã lưu là một biến thể tinh vi của cùng lỗi đó:
client thấy hai payment ID khác nhau cho một lần thanh toán và không biết cái nào là thật.

## Request hash: cùng key phải là cùng request

Chuyện gì xảy ra nếu cùng một key đến với số tiền đổi từ 100k thành 1.000k — do lỗi client, hoặc
do tấn công?

Nếu chỉ kiểm tra key, hệ thống sẽ trả về response cũ và âm thầm chấp nhận request đã bị thay đổi.
Vì vậy hãy hash các trường quan trọng (số tiền, loại tiền, mã đơn hàng) và lưu lại. **Key khớp
nhưng hash khác → từ chối request ngay.** Key là lời khẳng định đây là cùng một thao tác; body khác
chứng minh điều ngược lại.

## Lỗ hổng concurrency

`SELECT` rồi `INSERT` là một race condition, không phải một phép kiểm tra:

```
Thread A: SELECT key -> absent
Thread B: SELECT key -> absent      (A has not inserted yet)
A and B both call the provider  ->  DOUBLE CHARGE
```

Đây là race read-modify-write tổng quát — xem
[transaction-isolation](transaction-isolation.md) — chỉ khác là có tiền đi kèm.

**Cách sửa đáng tin cậy là ràng buộc `UNIQUE` trên `idempotency_key`.** Để cả hai thread cùng
insert; database từ chối thread thứ hai, và bên thua đọc bản ghi của bên thắng. Một ràng buộc trong
database luôn giữ đúng, bất kể code path, lần deploy hay thao tác tay nào.

Một distributed lock (Redis) có thể giảm tranh chấp như một lớp chặn đầu tiên, nhưng nó không phải
là sự đảm bảo — lock có thể hết hạn, node có thể bị phân vùng mạng. Hãy giữ ràng buộc ở bên dưới.

## Idempotency ở phía provider

Idempotency của bạn không bao phủ chặng gọi sang provider. Nếu lời gọi của bạn thành công nhưng
response bị timeout, bạn không được phát một lệnh trừ tiền mới với cùng thông tin.

1. **Truyền idempotency key của bạn xuống provider** nếu họ hỗ trợ. Khi đó retry của bạn an toàn
   từ đầu đến cuối.
2. **Nếu không, hãy truy vấn trước.** Gọi API trạng thái của provider để biết chuyện gì đã xảy ra
   trước khi quyết định có retry hay không. Không bao giờ trừ tiền lại với giả định rằng lần trước
   đã thất bại.

## Audit trail

Chỉ riêng trạng thái là vô giá trị khi có tranh chấp ba tháng sau. Với mỗi lần chuyển trạng thái,
hãy lưu lại một cách bất biến:

- `provider_payment_id` — mã tham chiếu của nhà cung cấp
- `provider_response` — toàn bộ JSON thô, không chỉnh sửa
- `raw_status` — trạng thái gốc của provider trước khi ánh xạ
- timestamp, và ai hoặc cái gì đã gây ra thay đổi

Response thô của provider không phải dữ liệu thừa; đó là bằng chứng duy nhất bạn có khi tranh luận
với provider hoặc kiểm toán viên. Hãy lưu chúng trước khi diễn giải.

## Checklist review

- [ ] Trạng thái thanh toán là một state machine với các trạng thái tường minh, không phải boolean
- [ ] Bản ghi nội bộ và idempotency key được tạo **trước** mọi lời gọi provider
- [ ] Chuyển trạng thái được ép buộc bằng `UPDATE` có điều kiện và kiểm tra số dòng bị ảnh hưởng
- [ ] Không thể rời khỏi trạng thái cuối; thử lại tạo ra một lần thanh toán mới
- [ ] Không bao giờ mở khóa sản phẩm / giao hàng ở trạng thái `pending`
- [ ] Trạng thái nội bộ được ánh xạ từ trạng thái provider, không sao chép
- [ ] Đơn hàng → thanh toán là 1:N và mọi lần thử đều được giữ lại
- [ ] `idempotency_key` có ràng buộc `UNIQUE`
- [ ] Response đầu tiên được lưu và trả lại nguyên văn khi retry
- [ ] `request_hash` được kiểm tra; cùng key nhưng khác body thì bị từ chối
- [ ] Idempotency key được truyền xuống provider, hoặc API trạng thái được truy vấn trước khi retry
- [ ] Response thô của provider và mọi lần chuyển trạng thái được lưu bất biến

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P1–P3 của
TechCraft ([collection](https://www.patreon.com/collection/2186651)), phần cuối của Data Modeling
Patterns về Idempotency Pattern, và API Design Patterns phần P7
([collection](https://www.patreon.com/techcraft_official)). Mô hình trạng thái, cơ chế idempotency
và các kịch bản lỗi là của TechCraft; cấu trúc review và checklist là của repository này. Việc khử
trùng lặp ở phía consumer được trình bày trong
[message-delivery-semantics](message-delivery-semantics.md).*
