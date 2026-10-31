---
source_sha256: 7ec8982db3b0313c5ce1c7ed4f76bb21229f9c7457c6163a21f594e6c399dfd1
---

# Mô hình hóa resource trong API

**Quy tắc: một API quản lý vòng đời của resource; nó không phơi ra các hành động hay các bảng của
bạn.** Phần lớn API tồi đến từ việc mô hình hóa database hoặc các động từ của sprint hiện tại, chứ
không phải nghiệp vụ.

Thuộc nhóm thiết kế API: [hợp đồng & versioning](api-contracts.md) ·
**mô hình hóa resource** (trang này) · [endpoint danh sách](api-list-endpoints.md) ·
[thao tác bất đồng bộ](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Hỏi xem resource là gì](#ask-what-the-resource-is)
- [Vòng đời, không phải hành động](#lifecycle-not-actions)
- [Khi nào động từ là câu trả lời đúng](#when-a-verb-is-the-right-answer)
- [Một endpoint là một quyền](#one-endpoint-is-one-permission)
- [Bảng không phải là resource](#tables-are-not-resources)
- [Chuyển trạng thái dưới dạng sub-resource](#state-transitions-as-sub-resources)
- [Checklist review](#review-checklist)

## Anti-pattern

```
POST /createOrder
POST /updateOrderStatus
POST /cancelOrderAndRefund
POST /markOrderAsShippedAndNotifyCustomer
POST /approveOrderForVipCustomerWithDiscount
```

Mỗi endpoint đều là cách xử lý hợp lý cho một ticket. Gộp lại, chúng chính là toàn bộ vấn đề: API
là một danh sách **những việc sprint trước cần làm**, không có model nào bên dưới.

Hậu quả cộng dồn lên nhau. Không có cách nào hỏi "đơn hàng này đang ở trạng thái gì?". Mỗi yêu cầu
mới lại thêm một endpoint, nên bề mặt API phình ra không giới hạn. Hai endpoint cùng sửa một trạng
thái với hai bộ validation khác nhau. Và không consumer nào đoán được cái gì đang tồn tại — họ phải
đọc danh sách.

## Vì sao môi trường dev che giấu lỗi này

Từng endpoint đều chạy, đều có test, và đều đáp ứng ticket của nó. Không có gì fail.

Cái giá phải trả nằm ở **cấu trúc và chỉ lộ ra khi nhìn tổng thể** — ở endpoint thứ 5 API trông vẫn
ổn; tới endpoint thứ 60 thì không ai học nổi, và mỗi thay đổi đều có nguy cơ đụng tới một hành vi mà
endpoint khác cũng đang cài đặt. Không có thời điểm nào test chuyển sang đỏ, nên vấn đề này tồn tại
mãi mãi.

## Hỏi xem resource là gì

Câu hỏi ngăn được phần lớn chuyện này: *"Trong hệ thống này, một `Order` thực chất là gì?"*

Nghe thì tầm thường nhưng thường không phải vậy. Đơn hàng là ý định mua của khách, hay là bản ghi
thực hiện giao hàng? Đơn đã hủy có còn là đơn hàng không? Một đơn có ba lần giao là một resource hay
bốn? Đơn hàng sở hữu khoản thanh toán của nó, hay chỉ tham chiếu tới — xem
[payment-order-consistency](payment-order-consistency.md), nơi việc coi chúng là một chính là lỗi.

Trả lời câu này trước khi viết endpoint là thứ biến một danh sách động từ thành một model. Trả lời
nó *sau đó* nghĩa là model sẽ là bất cứ thứ gì các endpoint tình cờ ngụ ý.

## Vòng đời, không phải hành động

> API không phải nơi để phơi ra các hành động. Nó là nơi quản lý vòng đời của một resource.

Vì vậy hãy mô hình hóa danh từ và các trạng thái nó có thể ở, rồi để các thao tác chuyển nó giữa
những trạng thái đó:

```
POST   /orders                     create      → pending
GET    /orders/{id}                read
PATCH  /orders/{id}                modify allowed fields
POST   /orders/{id}/cancellation   pending     → cancelled
POST   /orders/{id}/shipment       paid        → shipped
GET    /orders/{id}/shipments      the shipments that exist
```

Cụ thể, cách này mang lại:

- **State machine hiện ra rõ ràng.** Consumer có thể hỏi resource đang ở trạng thái nào và có những
  chuyển trạng thái nào — xem [payment-state-and-idempotency](payment-state-and-idempotency.md) để
  hiểu vì sao state machine đó quan trọng.
- **Yêu cầu mới thêm field hoặc chuyển trạng thái**, không thêm endpoint. Sự tăng trưởng có giới
  hạn.
- **Mỗi chuyển trạng thái được validate ở đúng một chỗ**, nên hai đường đi không thể mâu thuẫn nhau.
- **Dễ đoán.** Consumer biết một resource thì đoán được resource tiếp theo.

## Khi nào động từ là câu trả lời đúng

Cầu toàn ở đây là một cái bẫy, và ép mọi thao tác vào CRUD sẽ tạo ra API tệ hơn so với việc chấp
nhận động từ ở những chỗ phù hợp:

- **Hành động thực sự, không gắn với resource nào** — `POST /orders/{id}/refund-estimate` tính toán
  một thứ gì đó và không lưu gì cả.
- **Thao tác trên nhiều resource** — một lần chuyển tiền chạm tới hai tài khoản và không thuộc về
  tài khoản nào; `POST /transfers` mô hình hóa nó thành một resource riêng, và đó cũng là câu trả
  lời trung thực.
- **Batch và bulk** — `POST /orders/bulk-import` rõ ràng hơn việc giả vờ đó là một lệnh ghi vào
  collection.
- **Tính toán tốn kém** — `POST /reports` tạo ra một resource job là đúng; xem
  [thao tác bất đồng bộ](api-async-operations.md).

Phép thử không phải "đây có phải danh từ không" mà là **"consumer có đoán được endpoint này tồn tại
không, và nó có đúng một tác động rõ ràng không?"** `POST /orders/{id}/cancellation` và
`POST /orders/{id}/cancel` đều ổn. `POST /cancelOrderAndRefundAndNotify` thì không — không phải vì
ngữ pháp, mà vì nó gói ba tác động mà sau này sẽ cần thay đổi độc lập với nhau.

## Một endpoint là một quyền

Lập luận về việc gói chung ở trên nói về code buộc phải thay đổi cùng nhau. Có một phiên bản sắc bén
hơn của nó, mang tính chất bảo mật, và đó là lý do một field `type` không phải là lựa chọn trung
lập:

```
POST /wallets/transactions   { "type": "DEPOSIT",  "amount": 500000 }
POST /wallets/transactions   { "type": "WITHDRAW", "amount": 500000 }
```

Cùng method, cùng path. **Mọi tầng ra quyết định dựa trên method và path giờ chỉ thấy một thao
tác**: gateway, RBAC policy, rule WAF, rate limit, audit log. Cấp cho nhân viên hỗ trợ quyền cộng
tiền cho khách hàng là bạn đã cấp luôn quyền rút cạn tài khoản của họ — thứ duy nhất ngăn cách hai
việc là một câu `if` bên trong handler, do người thêm giá trị `type` gần nhất bảo trì.

Môi trường dev che giấu điều này hoàn toàn. Một route nghĩa là ít code hơn, một test, một chỗ để
xem, và mô hình phân quyền thường chỉ là một `@requires_auth` trông hoàn toàn đúng. Lỗ hổng chỉ mở
ra khi `type` thứ hai xuất hiện, mà điều này thường xảy ra sau khi thiết kế phân quyền đã xong và
không ai xem lại nữa.

Cái giá ở production:

- **Leo thang quyền bằng cách sửa một field.** Ranh giới phân quyền là một chuỗi trong request body,
  được kiểm tra bởi code ứng dụng chứ không phải bởi tầng policy đã được audit.
- **Công cụ policy bị mù.** Bạn không thể viết "role này được nạp nhưng không được rút" trong bất kỳ
  gateway, service mesh hay IAM policy nào, vì chúng so khớp theo path. Quy tắc phải được cài đặt
  lại bên trong handler, nơi không ai review nó như một phần của phân quyền.
- **Audit log không trả lời được "ai đã rút tiền".** Access log ghi lại
  `POST /wallets/transactions`. Muốn dựng lại ý định thì phải parse các request body đã lưu trữ, với
  giả định là bạn có lưu — mà body lại chính là thứ hay bị che đi.
- **Rate limit không thể đặt theo từng thao tác.** Một thao tác rẻ, tần suất cao và một thao tác
  nguy hiểm, hiếm gặp phải dùng chung một hạn mức.
- **Validation lệch dần giữa các nhánh.** Một request schema phục vụ mọi type, nên một field bắt
  buộc với type này lại là không bắt buộc-và-bị-bỏ-qua với type khác, và một rule viết cho type đầu
  âm thầm áp lên type thứ hai.

Hãy tách path, kể cả khi một handler phục vụ tất cả: `POST /wallets/deposits`,
`/wallets/withdrawals`, `/wallets/transfers`. Dùng chung phần cài đặt thì không sao; dùng chung
*path* thì không được. Phép thử không phải "đây có phải cùng một đoạn code không" mà là **"những thao
tác này có nên được cấp quyền, audit và rate limit riêng không?"** Nếu có, chúng là các resource
riêng biệt, dù code giống nhau đến đâu.

## Bảng không phải là resource

Lưu trữ và contract trả lời những câu hỏi khác nhau, và ánh xạ giữa chúng nên là có chủ đích:

- **Một resource có thể trải trên nhiều bảng.** Một `Order` với các dòng hàng, địa chỉ và tổng tiền
  là một thứ duy nhất đối với consumer.
- **Một bảng có thể phục vụ nhiều resource.** Bảng `users` có thể đứng sau cả `/customers` và
  `/staff` với field và quyền khác nhau.
- **Một số resource không có bảng nào.** Một bản tóm tắt dashboard, một giá trị availability được
  tính toán, một phép tổng hợp.
- **Một số bảng không bao giờ được lộ ra.** Bảng nối, các dòng audit, bảng outbox và inbox, các cờ
  nội bộ. Phơi ra một outbox là phơi ra một cơ chế mà consumer không bao giờ nên thấy.

Hai dấu hiệu khi review cho thấy lưu trữ đã rò rỉ vào contract: một endpoint đặt tên theo bảng nối,
và một response chứa field chỉ có nghĩa khi biết schema của bạn (`is_deleted`, `version`,
`partition_key`, `outbox_status`).

**Đừng phơi định danh hay trạng thái nội bộ thành contract.** Một cờ soft-delete trong
[data-retention-and-history](data-retention-and-history.md) là một lựa chọn triển khai; consumer nên
thấy resource là không tồn tại, chứ không phải nhận một cờ rồi được tin tưởng sẽ tự filter.

## Chuyển trạng thái dưới dạng sub-resource

Đây là pattern mở rộng tốt nhất cho mọi thứ có vòng đời. Thay vì một field status có thể sửa tùy ý,
bị điều khiển bởi bất cứ endpoint nào được gọi:

```
POST /orders/{id}/cancellation     { "reason": "customer_request" }
  → creates a cancellation record, moves the order to cancelled
GET  /orders/{id}/cancellation     → who cancelled it, when, and why
```

Ba đặc tính tự nhiên có được: chuyển trạng thái **có thể làm idempotent** (lần POST thứ hai có thể
trả về bản ghi hủy đã có thay vì hủy hai lần — xem
[payment-state-and-idempotency](payment-state-and-idempotency.md)); nó **audit được**, vì bản ghi
mang theo người thực hiện, thời điểm và lý do thay vì bị mất đi khi ghi đè status (xem
[data-retention-and-history](data-retention-and-history.md)); và nó **truy vấn được**, nên câu hỏi
"vì sao đơn này bị hủy" là một endpoint chứ không phải một ticket hỗ trợ.

So sánh với `PATCH /orders/{id} {"status": "cancelled"}`: nó để client điều khiển state machine trực
tiếp, âm thầm cho phép những chuyển trạng thái không hợp lệ trừ khi bạn validate lại, và không ghi
lại gì về lý do.

## Checklist review

- [ ] Resource được đặt tên theo khái niệm nghiệp vụ, không theo bảng hay bảng nối
- [ ] Câu hỏi "resource này là gì?" có câu trả lời tường minh trước khi có endpoint
- [ ] Các trạng thái và chuyển trạng thái hợp lệ của resource đã được định nghĩa
- [ ] Yêu cầu mới thêm field hoặc chuyển trạng thái, không thêm endpoint dạng động từ
- [ ] Không endpoint nào gói nhiều tác động độc lập
- [ ] Không endpoint nào chọn thao tác dựa trên field `type` hay `action` trong body
- [ ] Các thao tác cần được cấp quyền riêng có path riêng
- [ ] Audit log và rate limit phân biệt được các thao tác mà không cần parse request body
- [ ] Endpoint dạng động từ chỉ tồn tại ở nơi thực sự cần, và dễ đoán
- [ ] Không field nội bộ nào (`is_deleted`, `version`, trạng thái partition hay outbox) xuất hiện trong response
- [ ] Client không thể set status trực tiếp để điều khiển state machine
- [ ] Chuyển trạng thái trong vòng đời được mô hình hóa thành sub-resource ở nơi cần audit
- [ ] Mỗi chuyển trạng thái được validate ở đúng một chỗ

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P3 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "API quản lý vòng đời
của resource, không phải hành động", câu hỏi "một Order thực chất là gì?", và endpoint ví điều phối
theo type đằng sau "Một endpoint là một quyền" là của TechCraft; phần phân tích các ngoại lệ dùng
động từ, cách xử lý chuyển trạng thái dưới dạng sub-resource, các hệ quả về phân quyền/audit/rate
limit được phân tích cho endpoint đó, cùng checklist là của repository này.*
