---
source_sha256: 64fb0fe910d208af9aa8e42746a59c9f69396a6c37555f3fb32720134d6e9000
---

# Đối soát

**Quy tắc: đối soát là một nguồn thay đổi chính danh, không phải một bản vá âm thầm.** Mọi lần sửa
mà nó thực hiện đều phải đi qua bước kiểm tra chuyển trạng thái, ghi audit log và publish event —
y hệt như một request từ người dùng.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · **đối soát** (trang này) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Vì sao cần đối soát](#why-it-exists)
- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Bốn loại sai lệch](#the-four-mismatches)
- [Nhắm vào bản ghi rủi ro cao, không quét cả bảng](#target-high-risk-records-not-the-whole-table)
- [Không bao giờ vá âm thầm](#never-patch-silently)
- [Đối soát sổ cái và quyết toán](#ledger-and-settlement-reconciliation)
- [Những gì con người phải quyết định](#what-a-human-must-decide)
- [Checklist review](#review-checklist)

## Vì sao cần đối soát

Idempotency, state machine và outbox pattern đều làm giảm sự lệch pha. Không cái nào loại bỏ được
nó. Worker chết giữa chừng khi đang xử lý event, provider gặp sự cố, webhook bị mất, và deploy rơi
đúng vào những thời điểm xui xẻo.

Đối soát là tấm lưới an toàn tìm ra những gì luồng thời gian thực đã bỏ sót. Nó không phải việc vặt
của back-office để làm sau — nó là cơ chế duy nhất trả lời được câu hỏi *tiền đang ở đâu?* khi luồng
chính đã hỏng.

## Anti-pattern

```python
# Job chạy hằng đêm.
for payment in db.query("SELECT * FROM payments"):          # quét toàn bộ bảng
    remote = provider.get_payment(payment.provider_id)      # mỗi dòng một lời gọi API
    if remote.status != payment.status:
        db.execute("UPDATE payments SET status = ? WHERE id = ?",
                   remote.status, payment.id)               # ghi đè âm thầm
```

Ba lỗi cộng dồn:

- **Nó quét mọi thứ.** Với hàng triệu giao dịch mỗi ngày, job này làm nghẽn database và cạn rate
  limit của provider, và chạy lâu hơn cả chu kỳ lặp của chính nó.
- **Nó ghi đè trạng thái một cách mù quáng.** Không kiểm tra chuyển trạng thái, nên một bản ghi cũ
  phía provider có thể đẩy `succeeded → failed`, hoặc một race condition có thể hồi sinh một trạng
  thái cuối.
- **Nó không để lại dấu vết và không báo cho ai.** Không có gì ghi lại rằng job đã thay đổi dòng đó,
  và không event nào được publish — nên service đơn hàng, khâu giao hàng và thông báo không bao giờ
  biết một khoản thanh toán đã chuyển sang `succeeded`. Database thì đã được sửa, còn nghiệp vụ vẫn
  hỏng.

## Vì sao môi trường dev che giấu lỗi này

Ở local chẳng có gì để đối soát: không webhook nào bị mất, provider không gặp sự cố, worker không
chết. Database dev chỉ có vài dòng, nên quét toàn bộ cũng tức thì và job trông có vẻ hiệu quả. Và vì
đối soát chỉ có ý nghĩa *sau khi* một lỗi khác đã xảy ra, một hệ thống thiếu nó trông hoàn toàn khỏe
mạnh cho tới tận sự cố đầu tiên.

## Bốn loại sai lệch

| Nội bộ              | Provider           | Ý nghĩa                                      | Mức độ nghiêm trọng                                  |
|---------------------|--------------------|----------------------------------------------|------------------------------------------------------|
| `pending`           | `succeeded`        | Webhook thất lạc, hoặc event xử lý lỗi       | Khách mất tiền mà tay trắng                          |
| `succeeded`         | `failed`           | **Thất thoát hàng hóa**                      | **Nghiêm trọng nhất** — mất doanh thu, không thu hồi được |
| refund `processing` | refund `succeeded` | Trạng thái hoàn tiền bị lệch                 | Báo cáo tài chính sai, người dùng bối rối            |
| —                   | —                  | Tổng sổ cái ≠ số tiền thanh toán             | Sổ cái mất cân bằng — một lỗi kế toán               |

**Nội bộ `succeeded` nhưng provider `failed` là trường hợp cần thiết kế để phòng chống kỹ nhất.** Đơn
hàng đã được mở khóa hoặc hàng đã giao dựa trên một khoản thanh toán chưa bao giờ hoàn tất. Khác với
trường hợp đầu — vốn khắc phục được bằng cách hoàn thành nốt phần khách đã trả tiền — trường hợp này
là tiền đã mất. Phát hiện nhanh hay chậm quyết định đó là một đơn hàng hỏng hay một lỗ rò có hệ thống.

Loại thứ tư khác về bản chất — nó không phải bất đồng trạng thái với provider mà là lỗi kế toán nội
bộ, khi các bút toán sổ cái không cộng lại bằng khoản thanh toán mà chúng đại diện. Xem
[sổ cái](payment-ledger.md).

## Nhắm vào bản ghi rủi ro cao, không quét cả bảng

Đối soát nên là một query hẹp, không phải một lần quét sạch. Chỉ nhìn vào những chỗ có khả năng lệch:

- Các khoản thanh toán **ở `pending` quá TTL bình thường** — tín hiệu giá trị cao nhất
- Các khoản thanh toán mà lời gọi provider **đã ghi nhận timeout** (nhóm UNKNOWN)
- **Webhook event đã về tới nơi nhưng trạng thái xử lý chưa phải là thành công**
- Các giao dịch nằm trong một **báo cáo quyết toán** vừa về

Mỗi điều kiện trên là một query có index và có giới hạn. Nó cũng cho job một nhịp chạy tự nhiên: gần
thời gian thực với các khoản kẹt ở pending, cuối ngày với quyết toán.

## Không bao giờ vá âm thầm

Quy tắc vàng, và cũng là quy tắc mà phần lớn các bản triển khai làm sai:

> **Đừng sửa dữ liệu trong im lặng.** Job đối soát cũng là một *tác nhân gây thay đổi*, và phải
> chịu đúng những ràng buộc như mọi tác nhân khác trong hệ thống.

Mọi lần sửa phải:

1. **Kiểm tra chuyển trạng thái.** Job đối soát không được miễn trừ khỏi state machine. Nó không được
   đẩy `failed → succeeded` khi không có bằng chứng từ provider, và không được ghi đè một trạng thái
   cuối chỉ vì một bản ghi cũ nói khác đi.
2. **Ghi một dòng audit log.** Hệ thống nào đã đổi cái gì, từ giá trị nào sang giá trị nào, vì sao,
   dựa trên bằng chứng gì, và vào lúc nào. Một thay đổi trạng thái không truy được nguyên nhân thì
   không phân biệt được với dữ liệu bị hỏng.
3. **Publish event tương ứng.** Sau khi chuyển một khoản thanh toán sang `succeeded`, hãy phát
   `payment_succeeded` để service đơn hàng, khâu giao hàng và thông báo phản ứng. Sửa dòng thanh toán
   mà để đơn hàng vẫn kẹt là chữa đúng triệu chứng bạn đo được nhưng không giải quyết được chút gì
   vấn đề của khách.

Bước 3 là bước hay bị bỏ qua nhất, và đó là lý do đôi khi "đã chạy đối soát rồi" mà chẳng ai thấy có
gì thay đổi.

## Đối soát sổ cái và quyết toán

Khớp trạng thái thôi là chưa đủ; số tiền cũng phải khớp.

**Đối soát sổ cái** — mọi khoản thanh toán thành công đều có các bút toán cân bằng, và tổng khớp với
số tiền thanh toán. Tiền bị ghi nợ từ người dùng mà không được ghi có vào đâu cả là một bug logic mà
việc so sánh trạng thái sẽ không bao giờ phát hiện ra.

**Đối soát quyết toán** — phép kiểm tra cuối ngày với báo cáo quyết toán của provider:

```
expected payout  vs  actual payout received (after provider fees)
```

Đây là thứ làm cho báo cáo tài chính cuối kỳ đáng tin, và nó bắt được những lỗi tính phí cũng như các
điều chỉnh phía provider mà không phép kiểm tra theo từng giao dịch nào phát hiện được.

## Giờ cut-off

Loại sai lệch mà thực ra không phải sai lệch, và nó đốt hàng giờ điều tra.

Báo cáo quyết toán của provider bao trùm một khung thời gian đóng lại vào giờ cut-off *của họ*, theo
múi giờ *của họ*. Một giao dịch ngay sau mốc đó sẽ xuất hiện trong báo cáo kế tiếp. Nếu bạn so ngày
của mình với báo cáo của họ theo ngày lịch, ngày nào bạn cũng sẽ thấy "chênh lệch" — những giao dịch
bạn có mà họ không có, và ngược lại, tất cả đều hoàn toàn đúng.

Hãy đối soát theo **khung thời gian mà provider công bố**, không theo nửa đêm giờ local của bạn, và
ghi lại mỗi giao dịch được quyết toán trong batch nào. Xem [quyết toán](payment-settlement.md).

## Những gì con người phải quyết định

Tự động sửa là mục tiêu cho những trường hợp rõ ràng. Các trường hợp dưới đây không rõ ràng, và một
script không bao giờ được tự giải quyết chúng:

- **Lệch số tiền** — dù chỉ một đơn vị. Đây có thể là dấu hiệu bị can thiệp, và một job "sửa" nó sẽ
  xóa luôn bằng chứng.
- **Lệch loại tiền** giữa request và kết quả.
- **Xung đột chargeback** — đang tranh chấp, nhiều bên liên quan, và thường có hạn chót.
- **Sổ cái mất cân bằng** — cần bộ phận tài chính điều tra nguyên nhân gốc. Một script "cân lại" sổ
  sách là đang che giấu bug, không phải sửa bug.

Hãy chuyển những trường hợp này vào một queue review thủ công, kèm đầy đủ bằng chứng.

## Checklist review

- [ ] Có job đối soát, và nó không bị hoãn sang "để sau"
- [ ] Nó chỉ query các bản ghi rủi ro cao — không quét toàn bảng, không gọi provider theo từng dòng ở quy mô lớn
- [ ] Thời gian kẹt ở `pending` được theo dõi và alert độc lập
- [ ] Phát hiện được cả bốn loại sai lệch, kể cả sổ cái mất cân bằng
- [ ] Nội bộ `succeeded` nhưng provider `failed` kích hoạt alert mức nghiêm trọng cao
- [ ] Mọi lần sửa đều qua kiểm tra chuyển trạng thái; job không được miễn trừ khỏi state machine
- [ ] Mọi lần sửa đều ghi audit nêu rõ tác nhân, bằng chứng và lý do
- [ ] Mọi lần sửa đều publish event tương ứng để các hệ thống phía sau hội tụ
- [ ] Số dư sổ cái và tổng quyết toán được đối soát, không chỉ trạng thái
- [ ] Các trường hợp lệch số tiền, loại tiền, chargeback và mất cân bằng được chuyển cho người xử lý, không bao giờ tự sửa
- [ ] Khung so sánh theo giờ cut-off và múi giờ của provider, không theo ngày lịch local

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P9 của TechCraft,
được đối chiếu với Data Modeling Patterns phần P16
([collection](https://www.patreon.com/collection/2186651)). Bốn trường hợp sai lệch, chiến lược nhắm
vào bản ghi rủi ro cao, quy tắc "nguồn thay đổi / không bao giờ vá âm thầm" và ranh giới review thủ
công là của TechCraft; cấu trúc review và checklist là của repository này.*
