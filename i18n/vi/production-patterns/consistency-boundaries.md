---
source_sha256: a45361353dd663abf60735522f9e57bd29751e497613263c7e2dcabfa3307d8e
---

# Ranh giới nhất quán

**Quy tắc: hãy quyết định những invariant nào phải đúng *ngay lúc này*, và để mọi thứ khác tự hội
tụ.** Consistency là một khoản ngân sách chi vào những chỗ mà sai thì đắt — không phải một thiết lập
vặn lên mức cao ở khắp mọi nơi.

Thuộc nhóm transaction:
[atomicity & durability](transaction-atomicity-and-durability.md) ·
[isolation](transaction-isolation.md) · [locking](optimistic-vs-pessimistic-locking.md) ·
[distributed transaction](distributed-transactions.md) ·
**ranh giới nhất quán** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Chữ C trong ACID không phải là nhất quán phân tán](#the-c-in-acid-is-not-distributed-consistency)
- [Invariant, phát biểu tường minh](#invariants-stated-explicitly)
- [Vạch ranh giới](#drawing-the-boundary)
- [Các hệ thống thực tế vạch nó ở đâu](#where-real-systems-draw-it)
- [Consistency engineering: bốn tầng](#consistency-engineering-four-layers)
- [Consistency SLI](#consistency-slis)
- [Đây là một quyết định kinh doanh](#it-is-a-business-decision)
- [Checklist review](#review-checklist)

## Anti-pattern

Hai kiểu hỏng ngược chiều nhau, và kiểu thứ hai là kiểu mà các team còn tự khen mình.

**Mọi thứ đều eventual:**

```python
# One transaction, one service. Balance can go negative and nothing notices.
wallet.balance -= amount
db.commit()
```

Các tài khoản trôi xuống âm hàng triệu. Không exception nào từ database, không alert nào — database đã
thực thi mọi constraint được giao, và nó chưa bao giờ được giao constraint này.

**Mọi thứ đều strong:**

```python
with distributed_transaction():  # holds locks across five services
    update_balance(...)
    update_ledger(...)
    update_analytics_dashboard(...)  # ← does this need to be atomic with the balance?
    update_recommendation_index(...)
    update_loyalty_points(...)
```

Đúng, nhưng không thể scale quá vài trăm lần ghi mỗi giây. Dashboard analytics cũ đi 300 ms chẳng hại
ai; bắt nó phải transactional cùng với số dư thì biến một hệ thống toàn cầu thành một hệ thống bị giới
hạn bởi bên tham gia chậm nhất.

## Vì sao môi trường dev che giấu lỗi này

Một database, một node, không replica, không partition — nên *mọi thứ* đều strongly consistent một cách
tình cờ. Không có khác biệt nào quan sát được giữa một invariant bạn bảo vệ có chủ đích và một invariant
tình cờ đúng chỉ vì chỉ có một bên ghi.

Cái giá của việc nhất quán quá mức cũng vô hình y như vậy: tính đúng được giữ nguyên, nên không có gì
trông có vẻ sai. Hóa đơn tới dưới dạng một trần scale mãi về sau, khi thiết kế đã đắt đỏ để thay đổi.

## Chữ C trong ACID không phải là nhất quán phân tán

Từ bị lạm dụng nhiều nhất trong nghề, mang hai nghĩa chẳng liên quan gì tới nhau:

- **Chữ C của ACID** — sau một transaction, các *constraint bạn đã khai báo* vẫn được giữ. Không hơn.
- **Nhất quán phân tán (CAP)** — các replica có đồng ý với nhau về một giá trị tại một thời điểm hay
  không.

Database là một cỗ máy không hiểu biết gì. Nó không biết số dư không bao giờ được âm, rằng tổng tiền
của một đơn hàng phải bằng tổng các dòng của nó, hay rằng một chỗ ngồi không thể bán hai lần. **Nó thực
thi những gì bạn đã khai báo và không gì trong số những thứ bạn chỉ định làm.**

> Database xanh không có nghĩa là nghiệp vụ còn sống.

Vì vậy câu hỏi khi review không bao giờ là "database có nhất quán không?" Mà là *những quy tắc nghiệp vụ
nào phải không thể bị vi phạm, và chúng có thực sự được thực thi ở chỗ mà code không thể đi vòng qua
không?*

## Invariant, phát biểu tường minh

Invariant là một mệnh đề phải đúng ở mọi thời điểm quan sát được:

- `balance >= 0`
- `sum(ledger entries per transaction) = 0`
- `seats sold <= seats available`
- `order.total = sum(order lines)`
- một khoản thanh toán đã ở trạng thái cuối thì không bao giờ thay đổi nữa

Với mỗi invariant, hãy quyết định nó được thực thi **ở đâu**. Theo thứ tự độ tin cậy giảm dần:

1. **Database constraint** — `CHECK`, `UNIQUE`, `EXCLUDE`, foreign key. Đứng vững trước mọi code path,
   mọi migration, mọi lần sửa tay lúc 2 giờ sáng.
2. **Một câu lệnh có điều kiện atomic** — `UPDATE ... WHERE balance >= :amount`, có kiểm tra số dòng
   bị ảnh hưởng.
3. **Một lần chuyển trạng thái có điều kiện chặn dưới isolation level phù hợp** — xem
   [transaction-isolation](transaction-isolation.md).
4. **Code ứng dụng** — yếu nhất, vì service, script hay backfill kế tiếp ghi vào cùng bảng sẽ đi vòng
   qua nó.

Một invariant chỉ được "thực thi" trong code ứng dụng là một quy ước, và quy ước không sống sót nổi khi
gặp bên ghi thứ hai.

## Vạch ranh giới

> Hãy hình dung ranh giới nhất quán như một phòng an toàn: ở trong, từng con số phải chính xác. Ra
> khỏi cửa là tới sảnh chờ công cộng — tin tức tới trễ, và mọi người chấp nhận rằng dữ liệu dần dần
> mới khớp lại với nhau.

Bên trong ranh giới: đồng bộ, có transaction, invariant được thực thi, một bên ghi duy nhất.
Bên ngoài: event, replica, cache, projection — tất cả đều được phép trễ.

Điều này khớp với **aggregate** trong DDD: aggregate là đơn vị mà bên trong nó các invariant đúng ngay
lập tức, và là ranh giới transaction tự nhiên. Nhất quán giữa các aggregate là eventual theo thiết kế,
không phải do thỏa hiệp.

Kỷ luật kỹ thuật là giữ cho ranh giới **nhỏ**. Thêm một entity vào bên trong là mỗi lần ghi phải trả
thêm bằng throughput và availability. Strong consistency đắt — chỉ dành nó cho thứ thực sự cần.

## Các hệ thống thực tế vạch nó ở đâu

| Lĩnh vực        | Trong ranh giới (strong)                           | Ngoài ranh giới (eventual)                                          |
|-----------------|----------------------------------------------------|---------------------------------------------------------------------|
| **Ngân hàng**   | Số dư, sổ cái, quyết toán                          | Chiến dịch marketing, analytics, phân khúc khách hàng               |
| **Thương mại điện tử** | Tồn kho, trạng thái thanh toán, trạng thái đơn hàng | Sản phẩm vừa xem, xếp hạng tìm kiếm, gợi ý, dashboard người bán |
| **Giao dịch chứng khoán** | Danh mục nắm giữ, log giao dịch, quyết toán | Dashboard hồ sơ người dùng, biểu đồ lịch sử, chiến dịch           |

Cách lập luận đồng nhất, và nó mang tính thương mại hơn là kỹ thuật: analytics sai 1% thì vẫn sống
được; sổ cái sai là một sự kiện về pháp lý và niềm tin. Mục "sản phẩm vừa xem" chậm một giây thì
không ai khiếu nại; còn thanh toán xong rồi mới nhận thông báo hết hàng thì khách sẽ đưa nhau ra tòa.

Nghịch lý của app ngân hàng là minh họa rõ nhất: số dư cập nhật tức thì trong khi biểu đồ chi tiêu thì
trễ. Đó không phải bug. Đó là ranh giới, được vạch ra có chủ đích.

## Consistency engineering: bốn tầng

Bất nhất quán trong một hệ phân tán là một thuộc tính, không phải bug — nên hãy xây bộ máy tìm ra và
sửa nó:

**1. Phát hiện** — tầng quan trọng nhất và cũng hay bị thiếu nhất. Nó trả lời câu hỏi: *hạ tầng không báo
lỗi gì cả, vậy bằng cách nào ta nhận ra dữ liệu đã lệch?* Thứ cần theo dõi là **invariant**, không
phải CPU:

```sql
-- Confirmed payments with no ledger entry after a grace window. Any row is an incident;
-- the window keeps payments whose ledger write is still in flight out of the result.
SELECT p.id, p.amount, p.confirmed_at
FROM payments p
WHERE p.status = 'SUCCESS'
  AND p.confirmed_at < now() - interval '5 minutes'
  AND NOT EXISTS (SELECT 1 FROM ledger_entries e WHERE e.payment_id = p.id);
```

**2. Đối soát** — định kỳ so sánh các nguồn độc lập. Trong tài chính là đối chiếu ba bên: service với
service, dữ liệu nội bộ với sao kê của provider, và số đã ghi nhận với số quyết toán thực tế từ ngân
hàng. So từng dòng, không chỉ so tổng — các lỗi bù trừ nhau sẽ triệt tiêu trong một con số tổng. Xem
[payment-reconciliation](payment-reconciliation.md).

**3. Khôi phục** — sửa bằng công cụ, không bao giờ sửa tay. Replay idempotent một event hoặc workflow,
ép state machine chuyển trạng thái để kích hoạt lại một luồng, và các script bù trừ được chuẩn bị sẵn.
Một người chạy `UPDATE` trên production không phải là một tầng khôi phục.

**4. Audit** — lịch sử bất biến của mọi thay đổi, để bạn trả lời được *vì sao con số tổng lệch một cent
lúc 02:00 ngày 15?* Lưu số dư hiện tại thì không trả lời được gì; lưu các biến động tạo ra nó thì trả
lời được tất cả. Xem [payment-ledger](payment-ledger.md).

## Consistency SLI

Thôi nói "khá nhất quán" và hãy đo nó:

- **MTTD** — thời gian trung bình để *phát hiện* một sự bất nhất quán. Mất cả ngày mới phát hiện thì
  tiền đã thất thoát đáng kể trước khi ai kịp ra tay. Hệ thống trưởng thành đặt mục tiêu dưới một phút.
- **MTTR** — sau khi phát hiện, mất bao lâu để công cụ sửa chữa khôi phục tính đúng.
- **Tỷ lệ bất nhất quán** — tỷ lệ giao dịch rốt cuộc bị lệch.

Đây là những con số biến consistency từ một cuộc tranh luận thành một mục tiêu kỹ thuật.

## Đây là một quyết định kinh doanh

Cùng một lỗi kỹ thuật có những mức độ nghiêm trọng khác nhau, và chỉ phía kinh doanh mới nói được mức
nào:

> Một lần network partition làm lệch 10.000 điểm thưởng (~100 đô) và 10.000 đô tiền của khách trong sổ
> cái. Về kỹ thuật là hai bug y hệt nhau — trạng thái bất nhất quán sau một lỗi một phần. Marketing nói
> "sáng mai đối soát, gửi lời xin lỗi." Tài chính và Pháp chế lập tức tham gia cuộc gọi, vì đây là rủi
> ro kiểm toán và rủi ro với quan hệ ngân hàng.

Vì vậy consistency không phải nhị phân và không phải một thiết lập của database. Nó là một **ngân
sách**, được phân bổ vào chỗ mà sai thì đắt — và việc phân bổ thuộc về phía kinh doanh, dựa trên thông
tin bạn cung cấp. Các công ty công nghệ lớn không xây hệ thống không bao giờ lệch; họ quyết định chỗ nào
chấp nhận được sự lệch và đầu tư vào việc phát hiện và sửa chữa phần còn lại.

**Khi review, câu hỏi cần buộc phải đưa ra công khai:** *điều gì phải đúng ngay khi lời gọi này trả về,
và điều gì được phép đuổi theo sau?* Một thiết kế chưa trả lời câu hỏi này thường đã vô tình trả lời là
"mọi thứ, ngay lập tức" — và sẽ không scale được — hoặc vô tình trả lời là "không gì cả, không bao giờ",
và đang âm thầm mất tiền.

## Checklist review

- [ ] Các invariant nghiệp vụ được viết ra tường minh, không ngầm định qua code
- [ ] Mỗi invariant được thực thi bằng constraint hoặc câu lệnh atomic, không chỉ bằng code ứng dụng
- [ ] Ranh giới nhất quán đã được xác định: cái gì đồng bộ, cái gì được phép trễ
- [ ] Ranh giới nhỏ nhất có thể; không có gì nằm trong nó theo mặc định
- [ ] Nhất quán giữa các aggregate/service là eventual theo thiết kế, với một khoảng trễ có giới hạn
- [ ] Các query phát hiện giám sát invariant, không chỉ sức khỏe hạ tầng
- [ ] Đối soát so sánh các nguồn độc lập theo từng dòng, không theo số tổng
- [ ] Việc sửa chữa đi qua công cụ idempotent, không bao giờ qua các câu `UPDATE` thủ công
- [ ] Một audit trail bất biến có thể dựng lại quá trình một giá trị hình thành
- [ ] MTTD, MTTR và tỷ lệ bất nhất quán được đo, có mục tiêu
- [ ] Mức dữ liệu cũ chấp nhận được cho từng loại dữ liệu đã được thống nhất với phía kinh doanh, không phải do engineering tự giả định

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Transaction & Consistency của TechCraft
phần P4 và P18–P20, đối chiếu với Distributed Systems phần P14
([collection](https://www.patreon.com/collection/2256242)). Cách ví von căn phòng an toàn/sảnh chung,
bảng ranh giới cho ba lĩnh vực, khung Consistency Engineering bốn tầng, Consistency SLI, và lập luận về
ngân sách consistency là của TechCraft; cấu trúc review và checklist là của repository này.*
