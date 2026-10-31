---
source_sha256: 9e65f3706227fd277ea7ce54ef9f7f15f62f804c261388a092cc6e5f6743248d
---

# Sổ cái thanh toán

**Quy tắc: trạng thái thanh toán cho biết giao dịch có thành công hay không; chỉ sổ cái mới cho biết
tiền đã đi đâu.** Mỗi dịch chuyển tiền là hai bút toán có tổng bằng không, và không bút toán nào
được phép sửa hay xóa.

Thuộc nhóm thanh toán: [trạng thái & idempotency](payment-state-and-idempotency.md) ·
[provider & webhook](payment-provider-and-webhooks.md) ·
[nhất quán với đơn hàng](payment-order-consistency.md) · **sổ cái** (trang này) ·
[hoàn tiền](payment-refunds.md) · [đối soát](payment-reconciliation.md) ·
[quyết toán](payment-settlement.md) · [phát hiện gian lận](payment-fraud-detection.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Trạng thái là ảnh chụp; sổ cái là dòng chảy](#status-is-a-snapshot-a-ledger-is-the-movement)
- [Bút toán kép](#double-entry)
- [Bất biến: không UPDATE, không DELETE](#immutability-no-update-no-delete)
- [Một giao dịch sổ cái, nhiều bút toán](#one-ledger-transaction-many-entries)
- [Số dư chỉ là cache](#balances-are-a-cache)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
-- Toàn bộ hồ sơ tài chính của một khoản thanh toán 100,000đ.
UPDATE orders
SET status = 'paid'
WHERE id = :order_id;
UPDATE merchants
SET balance = balance + 100000
WHERE id = :merchant_id;
```

Đến cuối tháng, bộ phận kế toán đặt ra những câu hỏi mà schema này không trả lời nổi:

- 100.000đ đó *lúc này* đang nằm ở đâu?
- Provider đã thu bao nhiêu phí?
- Sau khi trừ phí nền tảng, merchant thực nhận chính xác bao nhiêu?
- Vì sao số dư của merchant lệch 3.000đ so với tổng các đơn hàng của họ?

Một cột `balance` có thể sửa đổi chỉ là một con số cộng dồn không có nguồn gốc. Khi nó sai — và chắc
chắn sẽ sai, chỉ sau một job lỗi hay một race condition — bạn không có gì để tính lại nó, và cũng
không có cách nào biết nó bắt đầu lệch từ lúc nào.

## Vì sao môi trường dev che giấu lỗi này

`balance = balance + amount` đúng với một khoản thanh toán thành công đơn lẻ, và đó là trường hợp duy
nhất môi trường dev tạo ra. Phí, hoàn tiền một phần, chargeback, chia tiền cho nhiều bên, hay các lần
ghi thất bại giữa chừng chưa bao giờ xuất hiện.

Lỗi sổ cái về bản chất cũng im lặng: không có exception nào, không request nào thất bại. Hệ thống báo
thành công trong khi các con số lặng lẽ không còn khớp nhau, và vài tuần sau nó lộ ra dưới dạng một
câu hỏi từ phòng tài chính chứ không phải một alert kỹ thuật.

## Trạng thái là ảnh chụp; sổ cái là dòng chảy

> Trạng thái thanh toán cho biết giao dịch đang ở đâu trong vòng đời của nó. Sổ cái trả lời câu hỏi
> quan trọng hơn: dòng tiền đã đi qua những tài khoản nào?

Trạng thái là một bức ảnh tại một thời điểm. Sổ cái là cả cuộn phim: tiền rời khỏi tài khoản của ai,
đi qua những khoản phí nào, và cuối cùng dừng lại ở đâu. Bạn cần cả hai, và chúng trả lời những câu
hỏi khác nhau — không thể suy ra cái thứ hai từ cái thứ nhất.

## Bút toán kép

Mọi dịch chuyển tài chính được ghi thành ít nhất hai bút toán — một **ghi nợ** (debit) và một **ghi
có** (credit) — ghi cùng lúc. Với một khoản thanh toán 100.000đ:

| Tài khoản                 | Chiều  | Số tiền |
|---------------------------|--------|---------|
| Tiền của người dùng       | debit  | 100.000 |
| Khoản phải thu của merchant | credit | 100.000 |

Bất biến giúp sổ cái tự kiểm tra chính nó:

```
sum(debits) = sum(credits)        -- tương đương: tổng các bút toán bằng không
```

**Nếu không cân, hãy từ chối giao dịch.** Đây là cơ chế khiến phần mềm không thể tự tạo ra hay làm
mất tiền — một bug logic hay một race condition có thể sinh ra một *bút toán* sai, nhưng không thể
sinh ra một *giao dịch* mất cân bằng. Hãy ép buộc nó bằng một constraint hoặc một assertion được kiểm
tra ngay lúc ghi, chứ không phải một báo cáo chạy sau.

Mỗi bút toán tối thiểu cần có: giao dịch sổ cái mà nó thuộc về, tài khoản, chiều, số tiền, loại tiền,
và tham chiếu tới sự kiện nghiệp vụ đã sinh ra nó.

## Bất biến: không UPDATE, không DELETE

Một khi bút toán đã được ghi, nó tồn tại vĩnh viễn. Không bao giờ `UPDATE` số tiền, không bao giờ
`DELETE` một dòng.

Để sửa sai, hãy **ghi một bút toán điều chỉnh** bù trừ cho nó. Cả lỗi lẫn phần sửa lỗi đều hiển thị,
và đó chính xác là điều audit đòi hỏi — một sổ cái chỉnh sửa được thì chẳng chứng minh được gì.

Hoàn tiền cũng vận hành trên đúng nguyên tắc này: hoàn tiền không phải là xóa đi một khoản thanh toán
mà là một dịch chuyển mới theo chiều ngược lại. Xem [hoàn tiền](payment-refunds.md).

Cách ép buộc trong thực tế: thu hồi quyền `UPDATE` và `DELETE` trên các bảng sổ cái ở cấp role của
database. Một quy ước ở tầng ứng dụng sẽ không sống sót qua một bản hotfix lúc 2 giờ sáng.

## Một giao dịch sổ cái, nhiều bút toán

Một khoản thanh toán thực tế hiếm khi chỉ có hai bút toán. Tiền rời khỏi người dùng, một phần về
merchant, một phần thành phí nền tảng:

| Tài khoản                   | Chiều  | Số tiền |
|-----------------------------|--------|---------|
| Tiền của người dùng         | debit  | 100.000 |
| Khoản phải thu của merchant | credit | 97.000  |
| Doanh thu phí nền tảng      | credit | 3.000   |

Tất cả thuộc về **một giao dịch sổ cái**, được ghi **nguyên tử**. Hoặc mọi bút toán đều được ghi,
hoặc không bút toán nào.

Ghi một phần là trạng thái tệ nhất mà sổ cái có thể rơi vào: người dùng đã bị ghi nợ, merchant đã
được ghi có, còn bút toán phí thì thiếu — sổ sách không cân, và không bản ghi riêng lẻ nào cho thấy
bước nào đã hỏng. Hãy gom các bút toán lại và commit cùng nhau.

## Loại tài khoản, và vì sao phương trình luôn cân

Bút toán kép chỉ tự kiểm tra được khi tài khoản có phân loại. Bộ từ vựng tối thiểu:

- **Tài sản (asset)** — giá trị bạn đang kiểm soát (tiền ở acquirer, tài khoản ngân hàng, khoản phải
  thu từ provider)
- **Nợ phải trả (liability)** — giá trị bạn đang nợ (số dư ví của khách hàng, khoản phải trả
  merchant, tiền chưa quyết toán)
- **Vốn chủ sở hữu / doanh thu (equity / revenue)** — phần doanh nghiệp đã kiếm được (phí nền tảng)

Số dư ví của khách hàng là **nợ phải trả**, không phải tài sản. Chỉ riêng việc phân loại đó đã ngăn
được cả một họ lỗi: nó nói rõ rằng giữ tiền của khách là một nghĩa vụ, và việc chi trả khoản đó làm
giảm cả tài sản lẫn nợ phải trả chứ không phải "tiêu" tiền của chính bạn. Xem
[quyết toán](payment-settlement.md) để hiểu vì sao tiền chưa quyết toán là khoản phải thu chứ chưa
phải tiền mặt.

## Sửa sai: bút toán đảo

Đây là dạng vận hành của tính bất biến. Để sửa một bút toán, hãy ghi bản đối xứng chính xác của nó —
cùng số tiền, ngược chiều — rồi ghi bút toán đúng. Lúc này có ba dòng: lỗi, bút toán đảo của nó, và
sự thật.

Cách này tốt hơn hẳn một bút toán "điều chỉnh" duy nhất bù phần chênh lệch, vì audit trail cho thấy
*cái gì đã sai* chứ không chỉ cho biết có gì đó đã thay đổi. Đây cũng chính là cơ chế mà hoàn tiền sử
dụng — xem [hoàn tiền](payment-refunds.md) — nên hoàn tiền không cần một trường hợp đặc biệt nào
trong sổ cái.

## Số dư chỉ là cache

Cộng lại mọi bút toán của một tài khoản mỗi lần đọc thì không trụ được khi quy mô lớn. Vì vậy hãy giữ
một **snapshot số dư** cho mỗi tài khoản để tra cứu nhanh.

Nhưng phải giữ đúng chiều của thẩm quyền: **bút toán sổ cái là nguồn sự thật; snapshot chỉ là
cache.** Nó phải luôn tính lại được từ các bút toán, và phải được đối soát định kỳ với chúng — xem
[đối soát](payment-reconciliation.md).

Failure mode cần để ý khi review là code coi snapshot như nguồn có thẩm quyền: cập nhật số dư mà không
ghi bút toán, hoặc tin vào một số dư đã bị lệch. Nếu snapshot và các bút toán không khớp, các bút toán
mới là đúng.

## Checklist review

- [ ] Dịch chuyển tiền được ghi thành bút toán sổ cái, không phải bằng cách sửa một cột `balance`
- [ ] Mọi giao dịch sổ cái đều cân: `sum(debits) = sum(credits)`, ép buộc ngay lúc ghi
- [ ] Giao dịch mất cân bằng bị từ chối, không phải ghi log rồi chấp nhận
- [ ] Bút toán sổ cái không bao giờ bị sửa hay xóa; sửa sai bằng bút toán điều chỉnh
- [ ] Quyền `UPDATE`/`DELETE` bị thu hồi trên các bảng sổ cái ở cấp database
- [ ] Mọi bút toán của một sự kiện — kể cả phí — được ghi nguyên tử trong một transaction
- [ ] Mỗi bút toán tham chiếu tới sự kiện nghiệp vụ đã sinh ra nó
- [ ] Snapshot số dư được coi là cache, tính lại được từ các bút toán
- [ ] Snapshot được đối soát định kỳ với tổng các bút toán
- [ ] Loại tiền được ghi rõ trên mọi bút toán; không tính toán chéo giữa các loại tiền nếu không có bản ghi quy đổi
- [ ] Tài khoản có phân loại (tài sản / nợ phải trả / vốn chủ sở hữu); số dư của khách hàng được mô hình hóa là nợ phải trả
- [ ] Sửa sai dùng bút toán đảo, không dùng điều chỉnh bù chênh lệch

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Payment System phần P7 của TechCraft
([collection](https://www.patreon.com/collection/2186651)), và Data Modeling Patterns phần P14–P15
([collection](https://www.patreon.com/techcraft_official)). Cách phân biệt trạng thái với dòng tiền,
bất biến của bút toán kép, tính bất biến của sổ cái, số dư như một cache, cách phân loại tài sản/nợ
phải trả và bút toán đảo là của TechCraft; cấu trúc review và checklist là của repository này.*
