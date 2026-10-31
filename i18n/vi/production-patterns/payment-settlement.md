---
source_sha256: bfec57f7b8b23c91752aec920bebc360f7278ec6cd87ccc9a2f11c4a6d17062d
---

# Quyết toán

**Quy tắc: "thanh toán thành công" chỉ là một ý định; quyết toán mới là lúc tiền thực sự về.** Coi
authorization là quyết toán đồng nghĩa với việc chi ra số tiền mà bạn chưa hề có.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
**quyết toán** (trang này) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Chuyển khoản thành công ≠ tiền đã chuyển](#transfer-succeeded--money-moved)
- [Clearing và quyết toán](#clearing-and-settlement)
- [Netting](#netting)
- [Các mô hình thời điểm quyết toán](#settlement-timing-models)
- [Mô hình hóa](#modelling-it)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
if payment.status == "succeeded":
    merchant.available_balance += payment.amount     # số tiền bạn chưa hề có
    payout_service.transfer(merchant, payment.amount)  # và giờ bạn đã chuyển nó đi
```

Thẻ đã được authorize. Tiền vẫn đang nằm ở acquirer và sẽ về tài khoản ngân hàng của bạn sau một đến
ba ngày làm việc — trừ đi phí, và trừ đi mọi khoản bị đảo ngược trong khoảng thời gian đó.

Bạn đã trả cho merchant bằng tiền đệm (float) của chính mình. Nếu khoản thanh toán đó sau này bị
chargeback, tiền mất theo cả hai chiều: tiền của khách được trả lại cho họ, còn tiền của bạn thì đã
chuyển đi rồi.

## Vì sao môi trường dev che giấu lỗi này

Sandbox của provider trả `succeeded` ngay lập tức và hoàn toàn không có quyết toán — không ngân hàng,
không T+2, không báo cáo quyết toán, không khấu trừ phí. Khái niệm này không hề có mặt trong môi
trường test, nên một mô hình đánh đồng authorization với việc đã nhận tiền vẫn qua mọi bài test.

Trên production nó cũng trông đúng trong một thời gian dài. Một hệ thống chi trả bằng tiền đệm vẫn
chạy ổn khi khối lượng còn nhỏ và chargeback còn hiếm, rồi vỡ khi một trong hai thứ đó tăng lên.

## Chuyển khoản thành công ≠ tiền đã chuyển

Ba sự kiện khác nhau mà các hệ thống kiểu CRUD gộp làm một boolean:

| Sự kiện           | Ý nghĩa                                                   | Đảo ngược được không?               |
|-------------------|-----------------------------------------------------------|-------------------------------------|
| **Authorization** | Ngân hàng phát hành xác nhận có tiền và tạm giữ số tiền đó | Có, rất dễ                          |
| **Capture**       | Bạn yêu cầu lấy số tiền đang được giữ                     | Có, thông qua hoàn tiền             |
| **Quyết toán**    | Tiền thực sự về tới tài khoản của bạn                     | Chỉ qua chargeback/tranh chấp       |

Một lệnh chuyển khoản liên ngân hàng được đánh dấu "thành công" trong hệ thống của bạn có nghĩa là *lệnh
đã được chấp nhận*, chứ không phải giá trị đã đổi chủ. Ngân hàng nhận vẫn có thể từ chối, bên trung
gian có thể trả lại, hoặc cả batch có thể thất bại — vài giờ hay vài ngày sau.

Vì vậy một khoản thanh toán có **hai state machine độc lập**: vòng đời authorization
([trạng thái & idempotency](payment-state-and-idempotency.md)) và vòng đời quyết toán. Chỉ mô hình
hóa cái thứ nhất là nguyên nhân sinh ra anti-pattern ở trên.

## Clearing và quyết toán

Cần tách bạch cho chính xác, vì hai khái niệm này hay bị dùng lẫn cho nhau trong khi chúng không
giống nhau:

- **Clearing** — đối chiếu *ai nợ ai bao nhiêu*: khớp giao dịch giữa các bên, tính toán vị thế, thống
  nhất nghĩa vụ. Thông tin dịch chuyển.
- **Quyết toán** — *thực hiện* các nghĩa vụ đó: giá trị thực sự được chuyển giữa các tài khoản. Tiền
  dịch chuyển.

Clearing có thể đã xong trong khi quyết toán vẫn đang chờ, và đó chính là khoảng thời gian mà sổ cái
của bạn phải thể hiện một nghĩa vụ chứ chưa phải tiền mặt. Xem [sổ cái](payment-ledger.md): khoản phải
thu và tiền mặt đã quyết toán là hai tài khoản khác nhau, và đánh đồng chúng là cách bảng cân đối kế
toán hết cân.

## Netting

Quyết toán hiếm khi chuyển số tiền gộp. **Netting** (bù trừ ròng) triệt tiêu các nghĩa vụ hai chiều
và chỉ chuyển phần chênh lệch:

```
Gross:  A owes B 100, B owes A 80   →   two transfers, 180 moved
Net:    A owes B 20                 →   one transfer,   20 moved
```

Ít lệnh chuyển hơn, cần ít thanh khoản hơn, phí thấp hơn. Đây là chuẩn mực trong các mạng thẻ, hệ
thống liên ngân hàng và chi trả cho marketplace.

Hệ quả cho data model của bạn: **một khoản ghi có từ ngân hàng không tương ứng với một giao dịch đơn
lẻ.** Một dòng quyết toán bao trùm hàng nghìn khoản thanh toán, trừ hoàn tiền, trừ phí, trừ các khoản
điều chỉnh. Mọi cách đối soát giả định quan hệ 1:1 giữa một khoản thanh toán và một dịch chuyển ngân
hàng sẽ thất bại ngay — đó là lý do đối soát quyết toán so sánh *số ròng dự kiến* với *số thực nhận*,
chứ không so theo số tiền từng giao dịch. Xem [đối soát](payment-reconciliation.md).

## Các mô hình thời điểm quyết toán

| Mô hình                    | Cách hoạt động                                          | Dùng ở đâu                        |
|----------------------------|---------------------------------------------------------|-----------------------------------|
| **RTGS** (real-time gross) | Mỗi giao dịch được quyết toán ngay, theo số gộp          | Liên ngân hàng giá trị cao        |
| **Deferred net**           | Gom batch, bù trừ ròng, quyết toán cuối chu kỳ           | Mạng thẻ, ACH, phần lớn PSP       |
| **T+n**                    | Quyết toán n ngày làm việc sau giao dịch                 | Chứng khoán, chi trả từ thẻ       |

Hai chi tiết gây ra bug thật:

- **Giờ cut-off.** Giao dịch sau giờ cut-off sẽ được quyết toán ở chu kỳ *kế tiếp*. Một khoản thanh
  toán lúc 23:50 và một khoản lúc 00:10 trông như liền kề nhưng được quyết toán cách nhau một ngày —
  và nếu giờ cut-off nằm ở múi giờ khác với server của bạn, ranh giới sẽ không nằm ở chỗ mà ai cũng
  tưởng.
- **Ngày làm việc.** T+2 vắt qua cuối tuần hoặc ngày lễ là bốn hay năm ngày lịch. Hệ thống tính ngày
  quyết toán bằng cách cộng ngày lịch sẽ sai với một tỷ lệ có thể đoán trước trong mỗi tháng.

## Mô hình hóa

- **Tách hai vòng đời.** Một khoản thanh toán mang cả trạng thái authorization lẫn trạng thái quyết
  toán; không cái nào suy ra được từ cái kia.
- **Giữ tiền như một khoản phải thu cho tới khi quyết toán.** `available_balance` của merchant nên
  phản ánh tiền mặt đã quyết toán; các khoản đang chờ thuộc về một `pending_balance` riêng. Chi trả
  chỉ được rút từ số dư khả dụng.
- **Mô hình hóa batch quyết toán thành một entity** với danh tính riêng, số ròng dự kiến, số thực
  nhận, phí và trạng thái — rồi liên kết các khoản thanh toán tới nó theo quan hệ nhiều-một.
- **Phí là bút toán sổ cái, không phải phép tính.** Số gộp, phí và số ròng mỗi thứ có một bút toán
  riêng, nếu không sổ sách sẽ không giải thích được.
- **Hãy chờ đợi báo cáo quyết toán đến muộn, sai thứ tự và bị điều chỉnh lại.** Nạp chúng một cách
  idempotent, lấy định danh batch của provider làm key.
- **Không bao giờ để một lần chi trả được kích hoạt bởi sự kiện authorization.** Chi trả được dẫn dắt
  bởi số dư đã quyết toán theo lịch, và đó cũng là biện pháp kiểm soát giới hạn rủi ro chargeback.

## Checklist review

- [ ] Authorization, capture và quyết toán là các trạng thái riêng biệt, không phải một boolean
- [ ] Số dư khả dụng của merchant phản ánh tiền **đã quyết toán**; khoản đang chờ được theo dõi riêng
- [ ] Chi trả chỉ rút từ số dư đã quyết toán và không bao giờ được kích hoạt bởi sự kiện authorization
- [ ] Khoản chưa quyết toán được giữ như khoản phải thu trong sổ cái, không phải tiền mặt
- [ ] Batch quyết toán được mô hình hóa thành entity với số tiền dự kiến và thực tế
- [ ] Đối soát so sánh số ròng dự kiến với số thực nhận, không ghép 1:1 giữa thanh toán và lệnh chuyển
- [ ] Phí và các khoản điều chỉnh được ghi thành bút toán sổ cái
- [ ] Việc nạp báo cáo quyết toán là idempotent và lấy batch id của provider làm key
- [ ] Giờ cut-off được ghi rõ, kèm múi giờ của provider
- [ ] Ngày quyết toán được tính theo ngày làm việc, có tính đến cuối tuần và ngày lễ
- [ ] Rủi ro chargeback trên các khoản đã chi trả được giới hạn và theo dõi

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Data Modeling Patterns phần P17 của
TechCraft ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn ý định đối lập với sự
thật, sự phân biệt giữa clearing và quyết toán, netting và cái bẫy giờ cut-off là của TechCraft; cấu
trúc review và checklist là của repository này.*
