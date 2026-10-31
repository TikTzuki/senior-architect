---
source_sha256: 8728a9685cfd1ddf6e0bdf3111eae0f399ddbc44024ca09a189346e90a07c5d2
---

# Ngữ nghĩa phân phối message

**Quy tắc: exactly-once delivery không tồn tại. Thứ bạn có là at-least-once, và việc của bạn là
làm cho *hiệu ứng* chỉ xảy ra một lần.** Outbox ngăn event bị mất; Inbox ngăn bản trùng bị áp dụng.

Thuộc nhóm data modeling: [lưu giữ & lịch sử](data-retention-and-history.md) ·
[dữ liệu thời gian](temporal-data.md) · [event sourcing & CQRS](event-sourcing-and-cqrs.md) ·
[replay event & rebuild](event-replay-and-projection-rebuild.md) ·
[tiến hóa schema event](event-schema-evolution.md) ·
**ngữ nghĩa phân phối message** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Queue là để sống sót, không phải để nhanh](#a-queue-is-for-survival-not-speed)
- [Vì sao exactly-once là chuyện hoang đường](#why-exactly-once-is-a-myth)
- [Outbox: đừng để mất event](#outbox-do-not-lose-the-event)
- [Inbox: đừng áp dụng hai lần](#inbox-do-not-apply-it-twice)
- [Phải có cả hai, nếu không thì cả hai đều vô dụng](#both-or-neither-works)
- [Thứ tự là kẻ thù của song song](#ordering-is-the-enemy-of-parallelism)
- [Những chỗ Inbox vẫn thất bại](#where-inbox-still-fails)
- [Checklist review](#review-checklist)

## Anti-pattern

**Phía producer — publish bên ngoài transaction:**

```python
with db.transaction():
    payment.status = "succeeded"
    db.save(payment)
queue.publish("payment_succeeded", payment.id)   # ← crash ở đây: event mất vĩnh viễn
```

Hoặc tệ hơn, publish bên trong transaction — khi đó một lần rollback sẽ phát ra event cho một việc
chưa từng xảy ra.

**Phía consumer — tin rằng mỗi message chỉ được giao một lần:**

```python
def on_payment_succeeded(event):
    account = db.get(event.account_id)
    account.balance += event.amount      # giao lại lần nữa là cộng tiền hai lần
    db.save(account)
```

Cả hai trông đều đúng. Cả hai đều sai theo cách chỉ lộ ra khi có retry.

## Vì sao môi trường dev che giấu lỗi này

Ở local, broker giao mỗi message một lần, đúng thứ tự, và không có gì crash giữa commit và publish.
Không có consumer timeout gây giao lại, không có rebalance, không có retry at-least-once.

Bug phía consumer đặc biệt khó thấy: nó không phải một lần crash mà là một lần *áp dụng hai lần lặng
lẽ*. Số dư đơn giản là sai, không có lỗi ở đâu cả, và chênh lệch chỉ nổi lên khi đối soát vài tuần
sau.

## Queue là để sống sót, không phải để nhanh

Đáng để đính chính ngay từ đầu, vì nó thay đổi những gì bạn kiểm tra khi review: message queue
không sinh ra để làm hệ thống nhanh hơn. Nó tồn tại để hệ thống **sống sót** qua một đợt tăng đột
biến hoặc một lần hệ thống phía sau ngừng hoạt động — hấp thụ phần tải mà consumer chưa xử lý kịp,
và tách tính sẵn sàng của producer khỏi tính sẵn sàng của consumer.

Cách nhìn lại này kéo theo một hệ quả. Nếu queue bị coi là một tối ưu tốc độ, sẽ không ai theo dõi
độ sâu hay consumer lag, và queue âm thầm trở thành một bộ đệm không đáy chứa những việc sẽ không
bao giờ được làm. Độ sâu và lag mới là metric sức khỏe; throughput thì không.

## Vì sao exactly-once là chuyện hoang đường

Producer gửi một message và ack của broker bị thất lạc trên đường mạng. Producer không thể phân biệt
"broker chưa hề nhận được" với "broker đã nhận nhưng ack biến mất".

- Không gửi lại → có thể **mất message**
- Gửi lại → có thể **trùng message**

Không có lựa chọn thứ ba. Vấn đề y hệt lặp lại giữa broker và consumer: consumer xử lý xong một
message rồi chết trước khi kịp ack, nên broker giao lại.

> Đảm bảo giao đúng một lần phần lớn chỉ là ảo tưởng. Message trùng không phải bug — đó là cách
> hệ phân tán vốn vận hành.

Vì vậy mọi hệ thống thực tế đều chọn **at-least-once delivery** rồi làm cho việc *xử lý* trùng trở
nên vô hại. Thứ đạt được là **hiệu ứng** exactly-once, không phải delivery exactly-once. Những hệ
thống quảng cáo "exactly-once" thực chất đang làm điều này ở bên trong — khử trùng lặp cộng với
commit offset nguyên tử — chứ không đánh bại được mạng.

## Outbox: đừng để mất event

Thay đổi trạng thái và việc phát event phải nguyên tử. Vì chúng không thể trải trên hai hệ thống,
hãy đặt cả hai vào một database:

```sql
BEGIN;
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (id, event_type, payload, created_at)
       VALUES (:uuid, 'payment_succeeded', :payload, now());
COMMIT;
```

Sau đó một **relay** poll các dòng chưa gửi và publish chúng.

Đảm bảo nhận được: *nếu trạng thái đã đổi, event chắc chắn tồn tại.* Không có khoảng hở nào mà tiền
đã dịch chuyển nhưng không có thông báo gì.

Thứ outbox tường minh **không** cho bạn: publish exactly-once. Relay có thể publish xong rồi chết
trước khi đánh dấu dòng đã gửi, nên nó publish lại. Đây là at-least-once có chủ đích — và đó là lý
do phía consumer không phải là tùy chọn.

Những điểm vận hành hay bị bỏ sót: relay phải **idempotent và chỉ một instance chạy tại một thời
điểm** (hai relay sẽ publish trùng — dùng `FOR UPDATE SKIP LOCKED`, xem
[lock-contention](lock-contention.md)); bảng outbox cần được **dọn dẹp** nếu không sẽ phình mãi; và
**độ sâu backlog phải có alert**, vì một relay bị kẹt thì im lặng — xem
[distributed-transactions](distributed-transactions.md).

## Inbox: đừng áp dụng hai lần

Hình ảnh phản chiếu ở phía consumer. Ghi lại mọi message ID đã xử lý, và — đây là toàn bộ mấu chốt
— ghi nó **trong cùng transaction với hiệu ứng nghiệp vụ**:

```sql
BEGIN;
  INSERT INTO inbox (message_id, processed_at) VALUES (:message_id, now());
  -- vi phạm unique ở đây => đã xử lý rồi => rollback và ack
  UPDATE accounts SET balance = balance + :amount WHERE id = :account_id;
COMMIT;
```

Ràng buộc `UNIQUE` trên `message_id` chính là cơ chế. Hai lần giao đồng thời race nhau, một bên
thắng, bên kia insert thất bại — cùng logic "ràng buộc thay cho kiểm tra rồi hành động" như
[payment-state-and-idempotency](payment-state-and-idempotency.md).

**Kiểm tra một kho khử trùng lặp nằm ngoài transaction là vô hiệu hóa nó:**

```python
if inbox.contains(msg.id):   # kiểm tra
    return
process(msg)                 # ← crash ở đây: hiệu ứng đã áp dụng, chưa bao giờ được ghi
inbox.add(msg.id)            # ghi lại
```

Crash giữa hiệu ứng và bản ghi, và lần giao lại sẽ áp dụng nó thêm lần nữa. Kiểm tra rồi hành động
với một khoảng hở, cộng một lần ghi không nguyên tử — dính cả hai failure mode cùng lúc. **Một
transaction, nếu không thì không chạy.**

Các dòng inbox cũng cần được dọn, với giới hạn theo cửa sổ giao lại tối đa của broker chứ không phải
một con số tùy ý.

## Phải có cả hai, nếu không thì cả hai đều vô dụng

Chúng giải quyết hai nửa đối nghịch và không cái nào tự nó là đủ:

|            | Ngăn chặn          | Nếu thiếu nó                            |
|------------|--------------------|-----------------------------------------|
| **Outbox** | Mất event          | Trạng thái đã đổi, không ai được báo    |
| **Inbox**  | Hiệu ứng trùng     | Tiền được cộng hai lần                  |

Outbox mà không có Inbox: không mất event nào, và relay at-least-once áp dụng chúng hai lần.
Inbox mà không có Outbox: không trùng lặp, và event biến mất khi publish thất bại.

Câu hỏi cần đặt ra khi review không phải "chúng ta có outbox chưa?" mà là **"một event có thể bị
mất không, và một event có thể bị áp dụng hai lần không?"** — và hai câu đó có câu trả lời riêng.

## Thứ tự là kẻ thù của song song

Khử trùng lặp không cho bạn thứ tự, mà thứ tự lại là đảm bảo người ta hay mặc định là có sẵn.

> Không có gì bảo đảm message đến nơi theo đúng trình tự chúng được gửi đi.

Vì sao: nhiều partition, nhiều consumer, retry đưa message trở lại cuối hàng, độ trễ mạng khác nhau,
và giao lại sau lỗi. Một `ProfileUpdated{name: "A"}` theo sau bởi `ProfileUpdated{name: "B"}` có
thể đến theo bất kỳ thứ tự nào, và last-write-wins theo thứ tự đến sẽ cho bạn "A" — sai vĩnh viễn,
không có lỗi ở đâu cả.

**Sự đánh đổi không ai nói thành lời:** thứ tự nghiêm ngặt đòi hỏi các message liên quan phải được
xử lý **tuần tự**, mà xử lý tuần tự là điều ngược lại với scale. Thứ tự toàn phần trên một topic
nghĩa là một consumer, một partition — một điểm nghẽn throughput duy nhất mà bạn không thể nới rộng.

Vì vậy câu hỏi thiết kế không phải "làm sao đảm bảo thứ tự?" mà là **"phạm vi nhỏ nhất cần có thứ
tự là gì?"**

| Phạm vi                                                           | Chi phí                                                                                                   |
|-------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| Thứ tự toàn cục                                                   | Một partition, một consumer. Không scale được.                                                            |
| **Thứ tự theo key** (theo tài khoản, theo đơn hàng, theo người dùng) | Partition theo key đó — song song giữa các key, có thứ tự trong một key. Gần như luôn là câu trả lời đúng. |
| Không cần thứ tự                                                  | Song song hoàn toàn                                                                                       |

Partition theo key chính là thứ mà partition key của Kafka, message group ID của SQS FIFO, và
consistent-hash exchange của RabbitMQ đều sinh ra để cung cấp.

Và ở nơi bạn không thể đảm bảo thứ tự, hãy **làm cho handler không phụ thuộc thứ tự** thay vì cầu
may:

- **Số version hoặc sequence** — từ chối hoặc bỏ qua message cũ hơn trạng thái bạn đang giữ.
- **Timestamp trên event**, không phải lúc đến — last-write-wins theo thời gian của *event*, và lưu
  ý rằng độ lệch đồng hồ giữa các producer khiến cách này chỉ là xấp xỉ.
- **Validate chuyển trạng thái** thay vì gán trạng thái — một event dẫn tới chuyển trạng thái không
  hợp lệ thì bị bỏ, đúng như trong
  [payment-provider-and-webhooks](payment-provider-and-webhooks.md).
- **Mô hình hóa thay đổi trạng thái thành delta** khi có thể (`balance += 100`), vì các phép toán
  giao hoán hoàn toàn không quan tâm thứ tự.

Thêm một hệ quả đáng kiểm tra: **message bị retry sẽ mất chỗ của nó.** Ngay cả với thứ tự theo key,
một lỗi khiến message bị đưa lại vào hàng sẽ đẩy nó ra sau những message vốn đến sau nó. Retry tại
chỗ (chặn key đó) hoặc chấp nhận đảo thứ tự — chỉ có hai lựa chọn đó, và các team thường mặc định có
một lựa chọn thứ ba không hề tồn tại.

## Những chỗ Inbox vẫn thất bại

Có cả hai chưa phải là một chứng minh hoàn chỉnh:

- **Side effect không nằm trong transaction.** Gửi email hay gọi API bên thứ ba nằm ngoài
  transaction database của bạn, nên lần insert khử trùng lặp không bao phủ được. Crash sau khi gửi,
  trước khi commit, và lần retry sẽ gửi lại. Những thứ này cần idempotency key riêng ở ranh giới.
- **Nhiều consumer, mỗi bên một inbox.** Khử trùng lặp là theo từng consumer. Điều đó đúng — mỗi
  consumer phải áp dụng event một lần — nhưng nó có nghĩa "đã xử lý" không phải là sự thật toàn cục.
- **Thứ tự vẫn không được đảm bảo.** Khử trùng lặp không sắp thứ tự. Một event cũ có thể đến sau một
  event mới hơn, nên chuyển trạng thái vẫn phải được validate, không được gán thẳng — xem
  [payment-provider-and-webhooks](payment-provider-and-webhooks.md).
- **Poison message.** Một message luôn thất bại sẽ retry mãi mãi hoặc rơi vào dead-letter queue. Cả
  hai đều im lặng trừ khi độ sâu dead-letter được theo dõi.
- **Logic nghiệp vụ không idempotent.** `balance += amount` được inbox bảo vệ là an toàn; một
  workflow giao hàng tạo một shipment mỗi lần được gọi có thể không an toàn, nếu có bước nào thoát
  ra ngoài transaction.

## Checklist review

- [ ] Event được phát qua outbox trong cùng transaction với thay đổi trạng thái
- [ ] Không publish gì bên trong một transaction có thể rollback
- [ ] Relay chỉ chạy một instance tại một thời điểm (`SKIP LOCKED`) và idempotent
- [ ] Có alert cho độ sâu backlog của outbox và tình trạng sống của relay
- [ ] Consumer khử trùng lặp theo message ID với ràng buộc `UNIQUE`
- [ ] Lần insert khử trùng lặp và hiệu ứng nghiệp vụ nằm trong **một** transaction
- [ ] Không khử trùng lặp kiểu kiểm tra rồi hành động trên một kho bên ngoài
- [ ] Side effect không nằm trong transaction mang idempotency key riêng
- [ ] Consumer validate chuyển trạng thái thay vì gán trạng thái (thứ tự không được đảm bảo)
- [ ] Độ sâu dead-letter queue được theo dõi và có owner
- [ ] Bảng outbox và inbox được dọn; thời gian giữ inbox bao phủ cửa sổ giao lại
- [ ] Không thiết kế nào giả định exactly-once delivery
- [ ] Phạm vi thứ tự cần thiết được nêu rõ; không giả định thứ tự toàn cục
- [ ] Message cần thứ tự được partition theo key, không tuần tự hóa toàn cục
- [ ] Handler không phụ thuộc thứ tự ở nơi thứ tự không được đảm bảo (version, thời gian event, hoặc delta)
- [ ] Ảnh hưởng của retry lên thứ tự được hiểu rõ và chấp nhận, hoặc retry tại chỗ
- [ ] Độ sâu queue và consumer lag được theo dõi như metric sức khỏe, không phải throughput

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ Data Modeling Patterns phần P11–P12, Backend
Internals phần P13–P16, và Distributed Systems phần P8, P10–P11 của TechCraft
([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề exactly-once là chuyện
hoang đường, cơ chế insert Inbox trong cùng transaction, và "thứ tự là kẻ thù của song song" là của
TechCraft; cấu trúc review và checklist là của repository này.*
