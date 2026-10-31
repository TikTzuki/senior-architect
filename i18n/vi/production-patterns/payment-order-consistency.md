---
source_sha256: 36e2e5fa9049e9191de87ecd5918518e2c8e090f06b7e2d90caca9c387c3aa2a
---

# Nhất quán giữa đơn hàng & thanh toán

**Quy tắc: tiền đã chuyển và đơn hàng đã cập nhật là hai sự thật nằm ở hai nơi, và không có
transaction nào bao trùm được cả hai.** Hãy làm cho việc đổi trạng thái và việc phát event nguyên tử
ở phía local, rồi để hai bên hội tụ dần.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
**nhất quán với đơn hàng** (trang này) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Vì sao không dùng distributed transaction](#why-not-a-distributed-transaction)
- [Hai domain, hai state machine](#two-domains-two-state-machines)
- [Outbox pattern](#the-outbox-pattern)
- [Consumer idempotent](#idempotent-consumer)
- [Đối soát](#reconciliation)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
charge = payment_service.charge(order.total)  # tiền đã đi — không đảo ngược được
if charge.succeeded:
    order_service.mark_paid(order.id)  # ← bất cứ sự cố nào ở đây cũng làm đứt liên kết
```

Khoảng hở giữa hai dòng đó chính là nơi tiền "biến mất". Nếu `mark_paid` bị timeout, ném exception,
hoặc process chết, khách hàng đã trả tiền còn đơn hàng thì nằm mãi ở `payment_pending`.

Trường hợp ngược lại còn tệ hơn về mặt kinh doanh: đơn hàng được mở khóa và giao đi, rồi sau đó
khoản thanh toán lại thất bại ở phía provider.

Cùng hình dạng, theo chiều khác: publish `payment_succeeded` lên queue *bên trong* transaction
thanh toán. Nếu transaction đó rollback, bạn đã loan báo một event cho một việc chưa từng xảy ra.

## Vì sao môi trường dev che giấu lỗi này

Cả hai service đều chạy, nhanh, và ở local. Khoảng thời gian giữa lệnh trừ tiền và lệnh cập nhật đơn
hàng chưa tới một mili giây và không có gì hỏng trong khoảng đó. Không có phân vùng mạng, không có
deploy giữa chừng request, không bị OOM kill, không có queue tồn đọng — nên hai lần ghi trông như
nguyên tử, trong khi thực ra chúng chỉ đơn giản là chưa từng bị ngắt quãng.

## Vì sao không dùng distributed transaction

Phản xạ đầu tiên là dùng 2PC trên cả hai database. Ở đây cách đó không dùng được, vì một lý do còn
căn bản hơn độ phức tạp: **tiền đã chuyển trong một hệ thống mà bạn không kiểm soát.** Bạn không thể
kéo ngân hàng hay provider thanh toán vào giao thức commit của mình, không thể giữ pha prepare trong
lúc chờ một webhook đến sau vài phút, và không thể rollback một lần capture đã hoàn tất.

Vậy mô hình đúng không phải là "làm cho hai lần ghi nguyên tử". Mà là **eventual consistency với
việc giao event được đảm bảo, cộng thêm đối soát để bắt những gì vẫn lệch nhau.**

## Hai domain, hai state machine

Đơn hàng và thanh toán liên quan đến nhau nhưng là hai thứ khác nhau:

- **Đơn hàng** — ý định của khách: họ muốn gì, bao nhiêu, giao tới đâu.
- **Thanh toán** — dòng tiền: tiền có thực sự được thu hay chưa.

Mỗi bên có state machine riêng (`created, payment_pending, paid, fulfilled, cancelled, refunded`
cho đơn hàng; xem [trạng thái & idempotency](payment-state-and-idempotency.md) cho thanh toán).

**Service đơn hàng không bao giờ được tự suy ra trạng thái thanh toán.** Nó phản ứng với các event do
service thanh toán phát ra. Một service đơn hàng tự đoán — "lời gọi trừ tiền đã trả về, vậy là đã
thanh toán" — chính là anti-pattern ở trên khoác thêm một cái state machine.

## Outbox pattern

Vấn đề cốt lõi: cập nhật trạng thái thanh toán và publish event là hai thao tác, và bất kỳ sự tách
rời nào giữa chúng cũng làm mất event.

Giải pháp — ghi cả hai **trong cùng một transaction local**:

```
BEGIN
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (event_type, payload) VALUES ('payment_succeeded', :payload);
COMMIT
```

Sau đó một **outbox worker** riêng sẽ poll bảng này và publish lên message queue, đánh dấu các dòng
đã gửi.

Điều được đảm bảo: nếu trạng thái thanh toán đã đổi, event chắc chắn tồn tại. Cả hai nằm trong một
database, nên một lần commit bao trọn cả hai. Không có khoảnh khắc nào mà tiền đã đi nhưng chưa có gì
được loan báo.

Hai tính chất cần nhớ:

- Việc giao là **at-least-once**, không phải exactly-once. Worker có thể publish xong rồi chết trước
  khi kịp đánh dấu dòng, nên event bị gửi lại. Đó là lý do consumer phải idempotent.
- Việc publish diễn ra **sau** commit, không bao giờ ở bên trong transaction — xem
  [lock-contention](lock-contention.md) để hiểu vì sao gọi mạng bên trong transaction là một failure
  mode riêng.

## Consumer idempotent

Ngữ nghĩa giao nhận, Inbox pattern, và lý do exactly-once không tồn tại được trình bày trong
[message-delivery-semantics](message-delivery-semantics.md). Dưới đây là những điểm riêng cho thanh
toán.

Vì event đến ít nhất một lần, service đơn hàng phải coi các bản trùng là no-op:

```sql
UPDATE orders
SET status = 'paid'
WHERE id = :order_id
  AND status = 'payment_pending';
-- không dòng nào bị ảnh hưởng: đã thanh toán rồi. Không phải lỗi — không làm gì cả.
```

Đặt điều kiện trên trạng thái hiện tại mong đợi và kiểm tra số dòng bị ảnh hưởng. Phiên bản nguy hiểm
là xử lý side effect vô điều kiện: một `payment_succeeded` trùng lặp kích hoạt lại khâu giao hàng sẽ
gửi hàng hai lần.

**Mọi side effect mà consumer kích hoạt — giao hàng, email biên nhận, điểm thưởng — đều cần lớp chặn
idempotency riêng**, chứ không chỉ riêng lệnh ghi trạng thái. Lệnh cập nhật trạng thái idempotent
nhưng email thì không, thì vẫn có hai email được gửi đi.

## Đối soát

Mọi thứ ở trên chỉ giảm sự lệch pha; không thứ nào loại bỏ được nó. Webhook bị mất, worker có bug,
provider gặp sự cố. Đối soát là phép kiểm tra để tìm ra những gì đã lọt lưới, bằng cách so sánh ba
nguồn:

```
Order state   vs   Payment state   vs   Provider state
```

Trường hợp đặc thù của bài này: **thanh toán ở `succeeded` trong khi đơn hàng vẫn ở
`payment_pending`** nghĩa là event đã bị mất hoặc consumer đã lỗi, và cách sửa là phát lại event chứ
không phải cập nhật trực tiếp dòng đơn hàng.

Phần trình bày đầy đủ — bốn loại sai lệch, cách nhắm vào các bản ghi rủi ro cao thay vì quét toàn
bộ, và quy tắc đối soát không bao giờ được âm thầm vá dữ liệu — nằm ở
[đối soát](payment-reconciliation.md).

## Checklist review

- [ ] Không cố dùng distributed transaction bao trùm thanh toán, đơn hàng và provider
- [ ] Đơn hàng và thanh toán có state machine riêng; service đơn hàng chỉ phản ứng với event
- [ ] Đổi trạng thái và insert event diễn ra trong một transaction local (outbox)
- [ ] Event được publish sau commit, không bao giờ bên trong transaction
- [ ] Outbox worker idempotent và đánh dấu đã gửi tách biệt khỏi bước publish
- [ ] Consumer đặt điều kiện trên trạng thái hiện tại mong đợi và kiểm tra số dòng bị ảnh hưởng
- [ ] Mọi side effect của consumer đều có lớp chặn idempotency riêng, không chỉ lệnh ghi trạng thái
- [ ] Có đối soát và đối soát phát lại các event bị mất (xem [đối soát](payment-reconciliation.md))

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P6 của TechCraft
([collection](https://www.patreon.com/collection/2186651)). Việc tách domain, lập luận về outbox và
đối soát ba chiều là của TechCraft; cấu trúc review và checklist là của repository này.*
