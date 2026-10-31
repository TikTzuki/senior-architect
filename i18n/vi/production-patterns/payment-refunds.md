---
source_sha256: d45a9c05038120a998155bf48be89decdbd3bc35800d870770cb32cc5a04b0a2
---

# Hoàn tiền

**Quy tắc: hoàn tiền là một giao dịch mới, không phải thao tác undo.** Nó có state machine riêng,
idempotency key riêng, bút toán sổ cái riêng — và không bao giờ sửa khoản thanh toán gốc.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
**hoàn tiền** (trang này) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Hoàn tiền ghi thêm vào lịch sử, không xóa bỏ nó](#a-refund-is-a-historical-fact-not-an-erasure)
- [Hoàn tiền cần state machine riêng](#refunds-need-their-own-state-machine)
- [Hoàn tiền một phần](#partial-refunds)
- [Hoàn tiền không phải là thu hồi những gì đã giao](#refund-is-not-reverse-logistics)
- [Idempotency, nếu không bạn sẽ hoàn tiền hai lần](#idempotency-or-you-refund-twice)
- [UNKNOWN cũng áp dụng ở đây](#unknown-applies-here-too)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
def refund(payment_id):
    provider.refund(payment_id)
    db.execute("UPDATE payments SET status = 'refunded' WHERE id = ?", payment_id)
```

Ba dòng, ba lỗi:

- **Nó sửa lịch sử.** Bản ghi thanh toán gốc bị ghi đè. Sự thật rằng tiền đã được thu, và thu vào
  lúc nào, biến mất.
- **Không có idempotency.** Người dùng bấm nút liên tục, hoặc một worker retry khi timeout, sẽ phát
  ra nhiều lệnh hoàn tiền cho cùng một yêu cầu. Khách được trả lại tiền hai, ba lần.
- **Nó giả định hoàn tiền là tức thì.** `provider.refund()` trả về không có nghĩa là tiền đã chuyển;
  quyết toán với ngân hàng mất từ vài giây đến vài ngày. Đánh dấu `refunded` ngay lập tức là khẳng
  định một điều bạn không có căn cứ.

Chữ "undo" chính là gốc rễ của lỗi. Không có gì trong hệ thống thanh toán có thể undo — chỉ có thể
bù trừ.

## Vì sao môi trường dev che giấu lỗi này

Stub của provider hoàn tiền tức thì và luôn thành công, nên khoảng chờ `pending` không bao giờ tồn
tại và state machine trông như thừa thãi. Một tester bấm một lần thì không bao giờ sinh ra bản trùng.
Còn hoàn tiền một phần — trường hợp phổ biến trên production — lại hiếm khi có mặt trong dữ liệu test.

## Hoàn tiền ghi thêm vào lịch sử, không xóa bỏ nó

Một khoản thanh toán thành công là một sự kiện đã xảy ra: tiền đã chuyển, các bên đã ghi nhận, sao kê
đã được phát hành. Bạn không thể biến nó thành chưa từng xảy ra.

Vì vậy hoàn tiền là một **giao dịch mới** có danh tính riêng, tham chiếu tới giao dịch gốc. Không bao
giờ xóa dòng thanh toán, không bao giờ ghi lại số tiền của nó, không bao giờ giả vờ nó chưa xảy ra.
Điều này giữ được khả năng truy vết, và là thứ giúp audit trail đứng vững khi có tranh chấp nhiều
tháng sau.

Trong sổ cái, đây là một dịch chuyển ngược chiều, không phải một phép xóa — xem
[sổ cái](payment-ledger.md):

| Tài khoản                   | Chiều  | Số tiền |
|-----------------------------|--------|---------|
| Khoản phải thu của merchant | debit  | 100.000 |
| Tiền của người dùng         | credit | 100.000 |

Cả giao dịch gốc lẫn giao dịch đảo đều được giữ lại, nhờ vậy sổ sách vẫn giải thích được.

## Hoàn tiền cần state machine riêng

Hoàn tiền là bất đồng bộ, nên nó cần các trạng thái của riêng nó — không mượn trạng thái của khoản
thanh toán:

`created → pending → succeeded` / `failed`

Kiểm tra hợp lệ trước khi khởi tạo. Tối thiểu: khoản thanh toán gốc đang ở trạng thái cho phép hoàn
tiền (`succeeded`), số tiền yêu cầu vẫn còn có thể hoàn, loại tiền khớp, và khoản thanh toán còn nằm
trong thời hạn hoàn tiền của provider (nếu có).

Một yêu cầu hoàn tiền không qua được bước kiểm tra thì không được đi tới provider.

## Hoàn tiền một phần

Khách trả lại một trong ba món hàng. Hoàn tiền một phần là chuyện bình thường, và đó là chỗ các phép
tính hay sai.

Theo dõi **tổng số tiền đã hoàn lũy kế** so với khoản gốc và ép buộc:

```
sum(all succeeded refunds for a payment)  <=  original payment amount
```

Hãy ép buộc nó một cách nguyên tử — đây là một read-modify-write trên một tổng cộng dồn, nên hai lệnh
hoàn tiền một phần đồng thời có thể cùng vượt qua một phép kiểm tra ngây thơ và cộng lại vượt quá
khoản gốc. Dùng update có điều kiện hoặc một constraint; xem
[transaction-isolation](transaction-isolation.md).

Trạng thái đơn hàng đi theo: `paid → partially_refunded`, hoặc `→ refunded` khi đã hoàn toàn bộ.

## Hoàn tiền không phải là thu hồi những gì đã giao

Điểm tinh tế nhất, và là nguồn gây thiệt hại kinh doanh thật.

**Đã trả tiền về cho khách không đồng nghĩa với việc lấy lại thứ họ đã nhận.** Thu hồi quyền truy cập nội dung
số, hủy một lô hàng đang trên đường vận chuyển, thu hồi điểm thưởng, và đòi lại khoản đã chi cho
merchant — mỗi việc là một luồng riêng với quy tắc riêng.

Một handler hoàn tiền kiểu "đảo ngược tất cả" sẽ thu hồi quyền truy cập mà khách vẫn còn được hưởng
theo một lần hoàn tiền một phần, hoặc cố hủy một đơn giao hàng đã hoàn tất. Hãy quyết định từng hệ
quả một cách có chủ đích thay vì mặc định đảo ngược toàn bộ.

## Idempotency, nếu không bạn sẽ hoàn tiền hai lần

Mỗi yêu cầu hoàn tiền mang một **idempotency key** riêng — khác với key của khoản thanh toán, vì đây
là một thao tác khác.

Thiếu nó: người dùng bấm hai lần, nhân viên hỗ trợ thử lại, worker bị timeout rồi chạy lại, và tiền
đi ra nhiều lần. Khoản lỗ trực tiếp đã tệ; sổ cái bị hỏng còn tệ hơn, vì lúc này sổ sách không khớp
với thực tế và mọi báo cáo phía sau đều sai.

Cơ chế giống hệt luồng thanh toán — unique constraint trên key, response đã lưu được trả lại nguyên
văn, request hash để bắt trường hợp số tiền bị đổi dưới cùng một key. Xem
[trạng thái & idempotency](payment-state-and-idempotency.md).

## UNKNOWN cũng áp dụng ở đây

Timeout khi gọi API hoàn tiền của provider nghĩa là UNKNOWN, không phải thất bại. **Đừng retry mù
quáng như một request mới.**

Đặt lệnh hoàn tiền ở `pending`, rồi giải quyết theo cùng cách như với thanh toán:

1. **Truy vấn provider** để biết trạng thái của idempotency key đó.
2. **Chờ webhook** (`refund_succeeded` / `refund_failed`), nhớ rằng webhook có thể đến muộn, trùng
   lặp và sai thứ tự — nên các lần chuyển trạng thái phải được kiểm tra, không áp dụng thẳng.

Xem [provider & webhook](payment-provider-and-webhooks.md). Những gì vẫn chưa được giải quyết là việc
của đối soát — xem [đối soát](payment-reconciliation.md).

## Checklist review

- [ ] Hoàn tiền là bản ghi mới tham chiếu tới giao dịch gốc; dòng thanh toán không bao giờ bị sửa
- [ ] Hoàn tiền có state machine riêng với trạng thái bất đồng bộ `pending`
- [ ] Kiểm tra hợp lệ chạy trước khi gọi provider: trạng thái cho phép hoàn, số tiền còn hoàn được, loại tiền
- [ ] Tổng số tiền đã hoàn lũy kế được ép buộc so với khoản gốc, một cách nguyên tử
- [ ] Các lệnh hoàn tiền một phần đồng thời không thể cộng lại vượt quá số tiền gốc
- [ ] Trạng thái đơn hàng phân biệt `partially_refunded` với `refunded`
- [ ] Việc đảo ngược phần đã giao (quyền truy cập, vận chuyển, điểm thưởng, khoản chi cho merchant) được quyết định theo từng mối quan tâm, không đảo ngược hàng loạt
- [ ] Mỗi yêu cầu hoàn tiền có idempotency key riêng với unique constraint
- [ ] Response hoàn tiền được lưu và trả lại khi retry
- [ ] Timeout được coi là UNKNOWN; giải quyết bằng truy vấn provider hoặc webhook, không bao giờ retry mù quáng
- [ ] Dịch chuyển tiền khi hoàn được ghi thành bút toán đảo trong sổ cái, không phải điều chỉnh vào giao dịch gốc

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P8 của TechCraft
([collection](https://www.patreon.com/collection/2186651)). Cách nhìn hoàn tiền như một giao dịch
mới, bất biến của hoàn tiền một phần, và sự phân biệt giữa hoàn tiền với thu hồi những gì đã giao là
của TechCraft; cấu trúc review và checklist là của repository này.*
