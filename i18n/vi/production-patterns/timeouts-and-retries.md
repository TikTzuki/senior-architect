---
source_sha256: bd841e8461221099ca68a1fe7d8341f1ca860a61225c71fefff7381ea9c66e6c
---

# Timeout & Retry

**Quy tắc: retry mà không có idempotency thì không phải xử lý lỗi — đó là một bug.** Và một lần retry
bắn ra không có backoff và jitter là một bộ khuếch đại tải nhắm thẳng vào một hệ thống vốn đang chật
vật.

Thuộc nhóm hệ phân tán: [mạng & độ trễ](network-and-latency.md) ·
**timeout & retry** (trang này) ·
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md) ·
[đồng thuận & bầu leader](consensus-and-leader-election.md) ·
[thiết kế cho sự cố](designing-for-failure.md) ·
[failover đa region](multi-region-failover.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Timeout: con số bạn buộc phải chọn](#timeouts-the-number-you-must-choose)
- [Ngân sách timeout](#timeout-budgets)
- [Retry khuếch đại tải](#retry-amplification)
- [Backoff và jitter](#backoff-and-jitter)
- [Cái gì retry được an toàn](#what-is-safe-to-retry)
- [Retry cần idempotency, nếu không thì là bug](#retry-needs-idempotency-or-it-is-a-bug)
- [Retry ở đâu](#where-to-retry)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# "Nếu gọi ngân hàng lỗi hoặc timeout, thử lại ngay, tối đa 3 lần."
for attempt in range(3):
    try:
        return bank_api.charge(amount)     # không timeout, không idempotency key
    except (Timeout, ConnectionError):
        continue                            # không backoff, không jitter
raise PaymentFailed()
```

Đọc theo thiện ý thì đây là "chịu lỗi tốt". Nhưng dưới áp lực thì nó làm thế này: ngân hàng chậm lại,
mọi client đều timeout, mọi client lập tức gửi thêm ba request nữa, và một hệ thống vốn đã chạy hết công
suất giờ nhận **gấp 4 lần lưu lượng đúng vào lúc nó yếu nhất**.

> Không có hacker, cũng chẳng có DDoS — chỉ là mấy dòng retry trông rất "hợp lý".

Và vì không có idempotency key, những lần retry *thực sự* tới nơi sẽ trừ tiền khách hàng nhiều hơn một
lần.

## Vì sao môi trường dev che giấu lỗi này

Stub của dependency luôn thành công, nên vòng lặp retry không bao giờ chạy. Không có timeout nào cần
tinh chỉnh vì chẳng có gì chậm, và không có tải đồng thời, nên hiệu ứng khuếch đại không thể xảy ra.

Logic retry cũng là phần code ít được test nhất: nó chỉ chạy khi có lỗi, mà lỗi lại chính là thứ môi
trường test loại bỏ. Lần đầu tiên nhánh này thực sự chạy là trong một sự cố.

## Timeout: con số bạn buộc phải chọn

"Không có timeout" cũng là một lựa chọn — lựa chọn tệ nhất. Một lời gọi không có timeout sẽ thừa hưởng
bất cứ hành vi nào mà OS rốt cuộc đưa ra, có thể là vài phút, và giữ một thread hoặc kết nối suốt khoảng
đó. Xem [network-and-latency](network-and-latency.md) để hiểu vì sao chậm còn tệ hơn chết.

Hãy đặt timeout từ số đo, không phải từ cảm tính:

- **Connection timeout** ngắn (1–3 s). Việc thiết lập kết nối TCP hoặc thành công nhanh, hoặc host không
  thể tới được.
- **Read/request timeout** suy ra từ **p99 của dependency, cộng thêm biên dự phòng** — không phải giá
  trị trung bình, và không phải một con số tròn mà ai đó thấy thích.
- **Timeout tổng** bao trọn cả thao tác, tính cả retry. Thiếu nó, ba lần retry với timeout 10 giây thành
  một request 30 giây mà không ai có ý định tạo ra.

Cái bẫy tinh vi: **một timeout đặt ở p99 sẽ retry đúng 1% request chậm nhất** — những request vốn đã
đang chật vật. Đặt quá chặt sẽ biến "khỏe nhưng chậm" thành lỗi, và sinh ra chính những lần retry gây
nên sự cố.

## Ngân sách timeout

Timeout phải giảm dần khi đi sâu xuống, nếu không các lời gọi bên trong sẽ sống lâu hơn request bên
ngoài:

```
client        10s
  gateway      9s
    service A  8s
      service B 4s   ← A can retry B twice and still fit
        database 2s
```

Nếu timeout của B lớn hơn của A, A bỏ cuộc trong khi B vẫn làm tiếp — tốn công suất cho một câu trả lời
không ai đọc. Đó cũng là kiểu lãng phí của một
[cache stampede](caching.md) khi tính response cho những client đã timeout.

**Hãy truyền deadline, không phải timeout.** Truyền đi thông tin "bạn còn 3.2 s" (gRPC deadline, header
`Deadline`) để service phía dưới có thể từ chối những việc nó không kịp làm xong thay vì bắt đầu làm.
Một service kiểm tra ngân sách còn lại trước khi thực hiện một lời gọi đắt đỏ đang làm được điều mà
không timeout tĩnh nào làm được.

## Retry khuếch đại tải

Retry là một bộ nhân tải, và phép nhân xảy ra đúng vào thời điểm tệ nhất có thể:

```
normal:      1,000 rps
degraded:    1,000 rps + 3 retries each = 4,000 rps
             → dependency slows further
             → more timeouts → more retries → collapse
```

Đây là một vòng phản hồi dương, và là lý do retry gây ra nhiều sự cố hơn số sự cố nó ngăn được. Ba thứ
giữ cho nó có giới hạn:

- **Retry budget** — giới hạn retry theo một *tỷ lệ* trên tổng số request (chẳng hạn 10%). Khi tỷ lệ
  lỗi cao, retry bị chặn trên toàn hệ thống chứ không phải theo từng lời gọi. Đây là biện pháp kiểm soát
  hiệu quả nhất, và cũng là thứ mà phần lớn các cài đặt còn thiếu.
- **Circuit breaker** — ngừng hẳn việc gọi một dependency đang hấp hối. Xem
  [circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).
- **Chỉ retry ở một tầng** — xem [bên dưới](#where-to-retry).

## Backoff và jitter

```python
delay = min(base * (2 ** attempt), cap)
delay = random.uniform(0, delay)          # full jitter
```

**Exponential backoff** cho dependency thời gian để hồi phục thay vì liên tục dội vào nó.

**Jitter không phải là phần trau chuốt thêm — nó chính là mấu chốt.** Không có jitter, mọi client lỗi
cùng một khoảnh khắc sẽ retry cùng một khoảnh khắc, nên bạn chỉ đổi tình trạng quá tải liên tục lấy
những đợt tăng vọt đồng bộ. Bất kỳ sự cố dùng chung nào cũng đồng bộ hóa đồng hồ của mọi client, đó
chính xác là lúc chúng dễ chạy đều bước nhau nhất.

Ngoài ra, hãy đặt trần cho độ trễ (một lần retry sau một tiếng thường vô dụng), và giới hạn cả tổng thời
gian đã trôi qua, không chỉ số lần thử.

## Cái gì retry được an toàn

| Response                         | Retry?                                                                    |
|----------------------------------|---------------------------------------------------------------------------|
| Connection refused / lỗi DNS     | **Có** — request chưa bao giờ tới nơi                                      |
| Timeout                          | **Chỉ khi idempotent** — có thể nó đã thành công                           |
| `500`, `502`, `503`, `504`       | Có, kèm backoff                                                            |
| `429`                            | Có, tôn trọng `Retry-After` — xem [rate-limiting](rate-limiting.md)        |
| `400`, `422` (validation)        | **Không** — nó sẽ lỗi y hệt mãi mãi                                        |
| `401`, `403`                     | Không (trừ một lần sau khi refresh token)                                  |
| `404`                            | Không                                                                      |
| `409` (conflict)                 | Chỉ sau khi đọc lại trạng thái                                             |

Dòng nguy hiểm là **timeout**, vì nó thực sự mơ hồ: request có thể đã hoàn tất và chỉ có response bị
mất. Retry một thao tác không idempotent sau timeout chính là cách khách hàng bị trừ tiền hai lần — xem
[payment-provider-and-webhooks](payment-provider-and-webhooks.md), nơi sự mơ hồ này được mô hình hóa
thành `UNKNOWN`.

## Retry cần idempotency, nếu không thì là bug

> Cài retry mà thiếu idempotency thì không phải là xử lý lỗi; đó là một bug nghiêm trọng.

Trước khi thêm retry, thao tác phải an toàn khi chạy hai lần. Các lựa chọn, theo thứ tự ưu tiên:

1. **Idempotent tự nhiên** — một `GET`, hoặc một `PUT` ghi trọn trạng thái.
2. **Idempotency key** — server khử trùng lặp và trả lại response ban đầu. Xem
   [payment-state-and-idempotency](payment-state-and-idempotency.md).
3. **Ghi có điều kiện** — `UPDATE ... WHERE status = 'pending'`, có kiểm tra số row bị ảnh hưởng.
4. **Hỏi rồi mới quyết** — hỏi dependency xem chuyện gì đã xảy ra trước khi hành động.

Nếu không cách nào áp dụng được, **đừng retry**. Ghi nhận sự không chắc chắn, đưa nó ra ngoài, và đối
soát — xem [payment-reconciliation](payment-reconciliation.md). Một trạng thái chưa rõ mà không retry
thì vẫn khắc phục được; một lần trừ tiền trùng thì không.

## Retry ở đâu

Retry chồng lên nhau theo cấp số nhân, và đây là sai lầm biến 3 lần retry thành 27:

```
client retries 3 ×  gateway retries 3 ×  service retries 3  =  27 requests
```

Chọn **một** tầng và chủ động tắt các tầng còn lại. Thường thì tầng gần chỗ lỗi nhất có nhiều thông tin
nhất, nhưng dù chọn tầng nào, các tầng khác phải được tắt một cách tường minh — kể cả những cơ chế retry
ẩn bên trong SDK, HTTP client và service mesh, vốn thường bật sẵn theo mặc định và không hề hiện ra
trong code của bạn.

Hãy rà soát các mặc định đó. Một thư viện tự retry ba lần bên dưới vòng lặp ba lần của chính bạn là
nguồn phổ biến nhất của những đợt khuếch đại tải không giải thích được.

## Checklist review

- [ ] Mọi lời gọi từ xa đều có connection timeout và read timeout
- [ ] Timeout được suy ra từ p99 đo được, có một giới hạn tổng bao trọn cả retry
- [ ] Ngân sách timeout giảm dần theo độ sâu; deadline được truyền xuống
- [ ] Retry dùng exponential backoff **có jitter** và độ trễ có trần
- [ ] Cả tổng số lần retry lẫn tổng thời gian trôi qua đều có giới hạn
- [ ] Có retry budget giới hạn retry theo tỷ lệ lưu lượng
- [ ] Chỉ retry những response retry được; `4xx` (trừ `429`) thì không
- [ ] `429` tôn trọng `Retry-After`
- [ ] Mọi thao tác được retry đều idempotent, nhờ key, ghi có điều kiện, hoặc tự bản chất
- [ ] Với thao tác không idempotent, timeout được coi là UNKNOWN, không phải lỗi
- [ ] Retry diễn ra ở đúng một tầng; mặc định của SDK/mesh/client đã được rà soát và tắt
- [ ] Tỷ lệ retry là một metric được giám sát, có cảnh báo khi tăng

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Distributed Systems của TechCraft, các
phần P4–P5 và P11 ([collection](https://www.patreon.com/techcraft_official)), và API Design Patterns
P11. Câu chuyện "đoạn code retry hợp lý đã giết chết hệ thống", cách đặt vấn đề về sự không chắc chắn,
và ý "retry mà không có idempotency là bug" là của TechCraft; cơ chế budget/jitter, bảng response retry
được, và checklist là của repository này.*
