---
source_sha256: c6b2ab3dc055e2107b4e83334f73f7f3d29dc2a208e993f667fee5e1003a8c96
---

# Distributed Transaction

**Quy tắc: ACID dừng lại ở ranh giới database, và "localhost là một lời nói dối vĩ đại."** Khi đi qua
nhiều service, bạn phải chọn giữa chặn mọi thứ lại (2PC) và bù trừ cho những gì đã xảy ra (Saga) —
không có lựa chọn thứ ba nào giữ được atomicity mà không mất gì.

Thuộc nhóm transaction:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
**distributed transaction** (trang này) ·
[ranh giới nhất quán](consistency-boundaries.md)

Cơ chế chịu lỗi cho các lời gọi giữa service nằm ở
[timeout & retry](timeouts-and-retries.md) và
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md).

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Ranh giới của ACID](#the-boundary-of-acid)
- [UNKNOWN mới là vấn đề thật](#unknown-is-the-real-problem)
- [Two-phase commit, và vì sao hiếm khi dùng](#two-phase-commit-and-why-it-is-rare)
- [Saga: bù trừ thay vì chặn](#saga-compensate-instead-of-block)
- [Eventual consistency và outbox](#eventual-consistency-and-the-outbox)
- [Khi "rồi sẽ nhất quán" thành "không bao giờ"](#when-eventually-becomes-never)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
@transactional                                  # ← guards this service's rows only
def buy_fund(order):
    Order.create(status="processing")           # local DB
    payment_service.debit(order.user, order.amount)   # remote, commits independently
    portfolio_service.add_units(order.units)          # remote, commits independently
    order.status = "completed"
```

`payment_service.debit()` commit trong database *của nó* ngay khi lời gọi trả về. Nếu sau đó
`portfolio_service.add_units()` bị timeout, `@transactional` cục bộ sẽ rollback đơn hàng — còn khoản
trừ tiền thì ở lại. Khách hàng đã trả tiền mà không sở hữu gì.

Annotation trông như thể nó bao trọn cả method. Thực ra nó chỉ bao một database.

## Vì sao môi trường dev che giấu lỗi này

Ở local, mọi service chạy trên cùng một máy, phản hồi trong một mili giây, và không bao giờ bị chia cắt
mạng. Nên chuỗi thao tác lúc nào cũng hoàn tất, và nhánh rollback không bao giờ chạy trong khi đã có
một commit ở phía remote.

> Localhost là một lời nói dối vĩ đại.

Nó gỡ bỏ đúng cái thuộc tính khiến distributed transaction trở nên khó: khả năng một bên tham gia đã
thành công mà bạn không thể biết được.

## Ranh giới của ACID

Một transaction là *một transaction, một database, một nguồn sự thật.* WAL, lock và rollback đều hoạt
động trong phạm vi đó.

Chia một thao tác nghiệp vụ ra nhiều service thì mỗi service vẫn có ACID cục bộ hoàn hảo, trong khi
thao tác tổng thể thì không có chút nào. Không ai nắm bức tranh toàn cục, và không bên tham gia nào có
thể rollback commit của bên khác.

Đây không phải lỗ hổng để vá. Nó là thuộc tính định nghĩa nên kiến trúc này, và câu hỏi thiết kế trở
thành *làm sao giữ được tính đúng mà không có atomicity toàn cục?*

## UNKNOWN mới là vấn đề thật

Nỗi ám ảnh đáng có không phải "làm sao để rollback?" mà là **"làm gì khi tôi không biết?"**

Một lần timeout khi gọi service khác nghĩa là lời gọi có thể đã thành công. Coi nó là thất bại và bù
trừ sẽ đảo ngược một thứ chưa từng xảy ra; coi nó là thành công sẽ xác nhận một thứ có thể chưa hề diễn
ra. Cả hai đều làm hỏng trạng thái.

Vì vậy UNKNOWN phải là một trạng thái được mô hình hóa, được giải quyết bằng cách truy vấn bên tham gia
hoặc chờ event của nó — không bao giờ đoán. Đây là cùng một kỷ luật như trong
[payment-provider-and-webhooks](payment-provider-and-webhooks.md), được tổng quát hóa.

## Two-phase commit, và vì sao hiếm khi dùng

2PC theo đuổi atomicity toàn cục bằng một coordinator:

```
Phase 1 — PREPARE:  coordinator asks every participant "can you commit?"
                    each locks resources and promises it can
Phase 2 — COMMIT:   if all promised, coordinator says commit; otherwise abort
```

Nó thực sự mang lại strong consistency. Cái giá của nó là lý do gần như không ai dùng nó cho giao tiếp
giữa các service:

- **Lock bị giữ suốt cả giao thức**, kể cả qua các vòng round trip mạng. Throughput sụp đổ khi tải cao,
  và đây là tranh chấp lock ở dạng tệ nhất — xem [lock-contention](lock-contention.md).
- **Coordinator hỏng thì không có giới hạn.** Nếu coordinator chết sau khi các bên tham gia đã hứa
  nhưng trước khi nó kịp quyết định, các bên tham gia ngồi đó với lock trong tay, không commit được cũng
  không abort được, chờ một chỉ thị có thể không bao giờ tới. Hệ thống thành zombie: còn sống, giữ lock,
  không tiến triển gì.
- **Nó cần các bên tham gia hiểu giao thức.** Một payment provider hay một ngân hàng sẽ không tham gia
  vào phase prepare của bạn. Ngay khi có bên thứ ba dính vào, 2PC hoàn toàn không dùng được.
- **Theo CAP, nó chọn consistency thay vì availability** — một lần network partition làm hệ thống đứng
  hẳn thay vì chỉ suy giảm.

2PC vẫn hợp lý *bên trong* một ranh giới tin cậy mà bạn kiểm soát hoàn toàn — ví dụ hai database trong
cùng một datacentre dùng XA. Khi vượt qua ranh giới service và ranh giới tổ chức, nó là công cụ sai.

## Saga: bù trừ thay vì chặn

Saga thay một transaction atomic bằng một chuỗi transaction cục bộ, mỗi transaction đi kèm một
**hành động bù trừ** đảo ngược nó về mặt ngữ nghĩa:

```
reserve inventory  →  charge payment  →  create shipment
      ↓ compensate         ↓ compensate        ↓ compensate
release inventory     refund payment      cancel shipment
```

Nếu bước ba thất bại, chạy các hành động bù trừ cho bước hai rồi bước một theo thứ tự ngược. Không có
gì bị lock xuyên suốt chuỗi, nên hệ thống vẫn sẵn sàng phục vụ.

Sự thay đổi trong tư duy: **thôi cố gắng ngăn lỗi xảy ra, mà hãy xây thứ gì đó sống sót và tự lành sau
khi lỗi xảy ra.**

Hai kiểu điều phối:

- **Orchestration** — một coordinator điều khiển từng bước. Tường minh, dễ debug, dễ thấy toàn bộ luồng;
  coordinator là một dependency và có thể trở thành nút thắt cổ chai.
- **Choreography** — mỗi service phản ứng với event. Ít ràng buộc và dễ scale; luồng end-to-end không
  nằm trọn ở bất kỳ chỗ nào, khiến lỗi khó truy vết.

Ưu tiên orchestration khi luồng có tính sống còn với nghiệp vụ và cần audit được — thanh toán, đơn hàng.

**Điều khiến Saga thất bại trên production:**

- **Bù trừ không phải rollback.** Một lần hoàn tiền là một giao dịch mới, khách hàng nhìn thấy được, và
  bản thân nó cũng có thể thất bại. Thu hồi một email đã gửi hay một món hàng đã giao có thể là bất khả
  thi.
- **Hành động bù trừ phải idempotent và retry được**, vì chúng chạy đúng vào lúc hệ thống đã đang không
  khỏe.
- **Không có isolation.** Các trạng thái trung gian đều nhìn thấy được: hàng đã giữ cho một đơn hàng sắp
  bị hủy. Bên đọc khác có thể hành động dựa trên những trạng thái sắp bị đảo ngược — đúng loại anomaly mà
  isolation thường che đi. Xem [transaction-isolation](transaction-isolation.md).
- **Một hành động bù trừ thất bại để lại một Saga bị kẹt.** Những Saga này cần được phát hiện và có
  đường xử lý thủ công, chứ không phải retry vô hạn.

## Eventual consistency và outbox

Từ bỏ tính đồng thời, giữ lại sự hội tụ: các bên tham gia bất đồng trong chốc lát và đạt cùng một trạng
thái trong một khoảng thời gian có giới hạn.

Cơ chế khiến nó đáng tin là **outbox** — thay đổi trạng thái và event được ghi trong cùng một
transaction cục bộ, rồi một worker publish sau đó:

```sql
BEGIN;
  UPDATE payments SET status = 'succeeded' WHERE id = :id AND status = 'pending';
  INSERT INTO outbox (event_type, payload) VALUES ('payment_succeeded', :payload);
COMMIT;
```

Nếu trạng thái đã đổi thì event tồn tại. Việc giao nhận là at-least-once, nên **mọi consumer phải
idempotent.** Trình bày đầy đủ trong
[payment-order-consistency](payment-order-consistency.md).

Sự đánh đổi được đưa ra một cách tường minh: ép nhất quán đồng bộ giữa các service sẽ phải trả bằng
availability và throughput — thread dồn đống chờ các lời gọi remote cho tới khi pool cạn. Eventual
consistency mua lại cả hai, với cái giá là một khoảng dữ liệu cũ nhìn thấy được mà sản phẩm phải được
thiết kế để chịu đựng.

## Khi "rồi sẽ nhất quán" thành "không bao giờ"

Eventual consistency là một lời hứa về sự hội tụ, và một lời hứa không được giám sát chỉ là một mong
ước. Nó âm thầm trở thành bất nhất quán vĩnh viễn khi:

- Một consumer gặp poison message và dừng lại, hoặc đẩy nó vào dead-letter ở chỗ không ai ngó tới
- Outbox worker chết và không có gì cảnh báo về các dòng chưa được publish
- Một hành động bù trừ thất bại và Saga bị bỏ dở giữa chừng
- Replica lag tăng không giới hạn trong lúc có sự cố

Không trường hợp nào sinh ra lỗi trên đường xử lý request. Cách sửa không phải retry nhiều hơn — mà là
**phát hiện**: đặt alert trên outbox backlog, consumer lag, độ sâu dead-letter và tuổi của Saga bị
kẹt, và đối soát độc lập. Xem [ranh giới nhất quán](consistency-boundaries.md) và
[payment-reconciliation](payment-reconciliation.md).

## Checklist review

- [ ] Không có annotation kiểu `@transactional` nào bị mặc định là bao được các lời gọi remote
- [ ] Các thao tác nghiệp vụ trải qua nhiều service dùng Saga hoặc outbox, không dùng một transaction bao ngoài
- [ ] Timeout trên lời gọi remote được coi là UNKNOWN và được giải quyết bằng truy vấn hoặc event
- [ ] 2PC chỉ được dùng trong một ranh giới tin cậy được kiểm soát, không bao giờ qua bên thứ ba
- [ ] Mỗi bước Saga có một hành động bù trừ được định nghĩa rõ, idempotent và retry được
- [ ] Những hành động bù trừ không thể thành công (email đã gửi, hàng đã giao) được chỉ ra ngay trong thiết kế
- [ ] Các trạng thái Saga trung gian mà bên đọc khác quan sát được đã được liệt kê và chấp nhận được
- [ ] Saga bị kẹt được phát hiện và có đường xử lý thủ công
- [ ] Dùng outbox pattern để phát event; consumer idempotent
- [ ] Có alert cho outbox backlog, consumer lag, độ sâu dead-letter và tuổi của Saga bị kẹt
- [ ] Khoảng dữ liệu cũ của eventual consistency có giới hạn và sản phẩm chấp nhận được

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Transaction & Consistency của TechCraft
phần P14–P17, đối chiếu với Data Modeling Patterns phần P11 và P13 và Distributed Systems phần P12–P13
([collection](https://www.patreon.com/collection/2256242)). Cách đặt vấn đề "ranh giới của ACID" và
"localhost là một lời nói dối vĩ đại", kịch bản zombie khi coordinator 2PC hỏng, và sự chuyển dịch từ
ngăn chặn sang tự lành là của TechCraft; cấu trúc review và checklist là của repository này.*
