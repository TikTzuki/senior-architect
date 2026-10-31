---
source_sha256: 486381b5c7ac4c3e577e690b1a763be1f88ad10895d03892966fde081ff494e7
---

# Phát hiện gian lận

**Quy tắc: hợp lệ về mặt kỹ thuật không đồng nghĩa với đáng tin, và chặn quá tay còn tốn kém hơn cả
gian lận.** Phát hiện gian lận là một tầng ra quyết định về rủi ro, không phải một câu lệnh `if`.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · [sổ cái](payment-ledger.md) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · **phát hiện gian lận** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Quyết định nên nằm ở đâu](#where-the-decision-belongs)
- [Rule trước, chấm điểm sau](#rules-then-scoring)
- [Kiểm tra velocity](#velocity-checks)
- [Bài toán kinh tế của false positive](#the-false-positive-economics)
- [Vùng xám: review bất đồng bộ](#the-grey-zone-async-review)
- [Vì sao công cụ của provider là chưa đủ](#why-the-providers-tool-is-not-enough)
- [Khả năng audit](#auditability)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
if amount > 10_000_000:
    block()  # một rule hard-code, và là rule duy nhất
charge(amount)  # còn lại thì đi thẳng tới bước chuyển tiền
```

Một tài khoản vừa tạo năm phút trước, một chiếc thẻ hoàn toàn mới, vài đơn hàng giá trị cao trong
vài giây từ một IP chưa từng thấy. Mọi trường dữ liệu đều hợp lệ. Thẻ là thẻ thật, còn hạn, xác thực
đúng. Giao dịch *hợp lệ* — và không nên được cho đi tiếp.

Một ngưỡng duy nhất cũng rất dễ bị dò ra: kẻ tấn công tìm được ranh giới và ngồi ngay dưới nó. Một
rule mà không deploy thì không đổi được là một rule mà kẻ tấn công có thể kiên nhẫn chờ lâu hơn bạn.

## Vì sao môi trường dev che giấu lỗi này

Ở môi trường dev không có đối thủ. Dữ liệu test đúng định dạng và vô hại, nên con đường duy nhất được
chạy là con đường hợp lệ. Các mẫu gian lận mang tính *hành vi* — chúng nằm trong mối quan hệ giữa
nhiều giao dịch theo thời gian — còn database dev thì không có lịch sử nào để so sánh.

Mặt false positive còn vô hình hơn nữa: chặn nhầm khách tốt không sinh ra lỗi nào, không một dòng log
nào. Nó chỉ hiện ra dưới dạng tỷ lệ chuyển đổi đi xuống mà chẳng ai quy cho các rule chống gian lận.

## Quyết định nên nằm ở đâu

Một **tầng quyết định rủi ro** nằm giữa checkout và bước chuyển tiền, và nó chạy ở nhiều hơn một
thời điểm trong vòng đời:

| Giai đoạn                                | Câu hỏi                                                      |
|------------------------------------------|--------------------------------------------------------------|
| **Trước thanh toán**                     | Có nên thử trừ tiền khoản này không?                         |
| **Sau authorization, trước capture**     | Tiền đang được giữ. Có thực sự nên lấy không?                |
| **Sau thanh toán**                       | Đánh dấu để review, đưa vào watchlist, hoặc làm đầu vào chấm điểm sau này |

Điểm móc trước capture là điểm giá trị nhất và cũng hay bị bỏ sót nhất: authorization đảo ngược được
với chi phí thấp, capture thì không. Một hệ thống chỉ quyết định trước thanh toán chỉ có một cơ hội
để đúng, và phải quyết với lượng thông tin ít nhất mà nó từng có.

Câu hỏi chuyển từ *khoản thanh toán này có đủ tiền không?* sang **khoản thanh toán này có xứng đáng
được đi tiếp không?**

## Rule trước, chấm điểm sau

**Dựa trên rule** là điểm khởi đầu của mọi người, và nó có những ưu điểm thật: đơn giản, giải thích
được, audit được — bạn có thể nói chính xác cho khách hàng, cơ quan quản lý, hoặc đội vận hành của
mình vì sao một giao dịch bị chặn.

```
amount > 10,000,000 AND account_age < 1 day  ->  flag for review
```

Điểm yếu của nó là sự cứng nhắc. Rule là nhị phân và có thể bị học ra.

**Chấm điểm rủi ro** gộp nhiều tín hiệu yếu thành một con số:

- **Tài khoản** — tuổi tài khoản, lịch sử giao dịch, hành vi sạch trước đó
- **Giao dịch** — số tiền, loại tiền, danh mục hàng hóa
- **Kỹ thuật** — device fingerprint, uy tín IP, phát hiện proxy/VPN
- **Địa chỉ** — địa chỉ giao hàng lệch với địa chỉ thanh toán
- **Lịch sử** — chargeback trước đây hoặc lạm dụng hoàn tiền

Ba kết quả, không phải hai: **cho qua** (thấp), **review thủ công** (trung bình), **chặn** (cao).
Dải ở giữa chính là thứ khiến việc chấm điểm đáng với độ phức tạp của nó — xem
[review bất đồng bộ](#the-grey-zone-async-review).

Hãy giữ rule song song với chấm điểm thay vì thay thế nó. Rule thể hiện chính sách mà bạn phải nói ra
được một cách rõ ràng; điểm số thể hiện mức nghi ngờ mà bạn có thể tinh chỉnh.

## Kiểm tra velocity

Tín hiệu thời gian thực sắc bén nhất, vì nó đo hành vi chứ không đo thuộc tính:

- 5 giao dịch trong một phút
- 10 thẻ khác nhau được thử trên một tài khoản
- Nhiều đơn hàng từ một IP hoặc một thiết bị trong khoảng thời gian ngắn

Card testing và chiếm đoạt tài khoản đều trông hoàn toàn bình thường khi xét từng giao dịch, nhưng
lộ rõ khi nhìn tổng thể. Velocity là thứ biến một tập các khoản thanh toán hợp lệ riêng lẻ thành một
mẫu có thể phát hiện — chính là loại thông tin mà một câu `if` theo từng request không thể nhìn thấy.

## Bài toán kinh tế của false positive

Đây là phần mà kỹ sư sai một cách có hệ thống: **chi phí của việc chặn quá tay thường vượt quá chi
phí của gian lận.**

Tăng độ nhạy lên là bạn chặn cả khách hàng trung thực. Họ không báo bug — họ sang mua ở đối thủ. Một
hệ thống chống gian lận chỉnh quá chặt có thể làm mất 1–2% GMV, con số mà với phần lớn doanh nghiệp
lớn hơn rất nhiều so với khoản gian lận nó ngăn được.

Vì vậy "chặn nhiều hơn" không phải mặc định an toàn, và một rule chống gian lận được đưa lên mà chưa
đo tỷ lệ false positive là việc chưa làm xong. Mỗi ngưỡng là một sự đánh đổi kinh doanh giữa bảo mật
và chuyển đổi, và nó cần một người chịu trách nhiệm nhìn thấy cả hai con số.

## Vùng xám: review bất đồng bộ

Không phải quyết định nào cũng cần tức thì. Với giao dịch rủi ro trung bình, hãy chạy **review bất
đồng bộ**: hiển thị cho khách một trạng thái đang chờ, đưa giao dịch vào queue review thủ công, và
quyết định mà không làm khựng checkout.

Cách này mua thêm thời gian phân tích mà không cộng thêm độ trễ vào checkout — và biến một phỏng đoán
nhị phân bị ép buộc thành một quyết định có sự phán đoán của con người.

**Quy tắc quan trọng: không bao giờ giao hàng trước khi có quyết định về gian lận.** Đặc biệt với
hàng có giá trị bán lại cao hoặc giao tức thì — thẻ quà tặng, mã nạp tiền, license phần mềm — nơi
việc giao hàng diễn ra ngay và không thể đảo ngược.

## Vì sao công cụ của provider là chưa đủ

Stripe Radar và các công cụ tương đương rất mạnh, nhưng chúng không đủ, vì **provider không hiểu sản
phẩm của bạn.**

Họ có thể cho bạn biết một chiếc thẻ có rủi ro. Họ không thể biết món hàng trong giỏ này là món đang
có thị trường bán lại sôi động, rằng SKU này là thứ các đường dây gian lận nhắm tới, hay hành vi của
khách hàng này là bất thường *đối với nghiệp vụ của bạn*. Tín hiệu của provider nói về phương tiện
thanh toán; rule của bạn nói về domain của bạn. Bạn cần cả hai.

## Khả năng audit

Khi một chargeback đến sau 30 ngày, hoặc đội vận hành hỏi một rule có thực sự hữu ích không, bạn cần
dựng lại được quyết định. Với mỗi lần đánh giá, hãy lưu:

- **Tín hiệu** — giá trị thô thu thập tại thời điểm quyết định, không tính lại về sau
- **Điểm rủi ro** — con số và trọng số của từng thành phần
- **Các rule đã khớp** — cụ thể những rule nào đã kích hoạt
- **Quyết định và người quyết định** — cho qua/review/chặn, và người hay phiên bản hệ thống nào đã
  chọn
- **Timestamp** — cho từng giai đoạn

Lưu tín hiệu *đúng như lúc đó* là phần hay bị bỏ qua, và lại chính là thứ làm bản ghi có giá trị: tuổi
tài khoản và số chargeback trước đó đều thay đổi theo thời gian, nên không thể giải thích một quyết
định bằng dữ liệu của hôm nay. Log này cũng là đầu vào để huấn luyện và tinh chỉnh nhằm giảm false
positive.

## Checklist review

- [ ] Ra quyết định rủi ro là một tầng riêng, không phải các câu điều kiện viết lẫn trong luồng thanh toán
- [ ] Đánh giá diễn ra trước thanh toán **và** trước capture, không chỉ ở checkout
- [ ] Ngưỡng và rule cấu hình được mà không cần deploy
- [ ] Chấm điểm kết hợp tín hiệu về tài khoản, giao dịch, kỹ thuật, địa chỉ và lịch sử
- [ ] Có kiểm tra velocity theo tài khoản, thẻ, IP và thiết bị
- [ ] Hỗ trợ ba kết quả: cho qua / review thủ công / chặn
- [ ] Tỷ lệ false positive được đo và có người chịu trách nhiệm, không chỉ đếm số vụ gian lận bắt được
- [ ] Giao dịch vùng xám được đưa vào review bất đồng bộ thay vì ép quyết định tức thì
- [ ] Không giao gì trước khi có quyết định về gian lận, đặc biệt với hàng giao tức thì
- [ ] Có rule ở cấp nghiệp vụ bên cạnh công cụ của provider
- [ ] Tín hiệu được lưu đúng như lúc thu thập, cùng điểm số, các rule đã khớp, quyết định và người quyết định

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P10 của TechCraft
([collection](https://www.patreon.com/collection/2186651)). Cách nhìn tầng rủi ro, các giai đoạn trong
vòng đời, tín hiệu velocity và bài toán kinh tế của false positive là của TechCraft; cấu trúc review
và checklist là của repository này.*
