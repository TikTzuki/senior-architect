---
source_sha256: bc833d4364ab506c0e4b6619a19b65eeeea8d2f8230bc2a9df65faedb60c6f5e
---

# Thiết kế cho sự cố

**Quy tắc: sự cố là trạng thái mặc định, không phải ngoại lệ.** Một hệ thống quy mô lớn không nhắm tới
ngày mọi thứ trơn tru — mục tiêu thiết kế của nó là để ngày xui xẻo chỉ làm nó yếu đi chứ không đổ sập.

Thuộc nhóm hệ phân tán: [mạng & độ trễ](network-and-latency.md) ·
[timeout & retry](timeouts-and-retries.md) ·
[circuit breaker & bulkhead](circuit-breakers-and-bulkheads.md) ·
[đồng thuận & bầu leader](consensus-and-leader-election.md) ·
**thiết kế cho sự cố** (trang này) ·
[failover đa region](multi-region-failover.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Hai triết lý](#two-philosophies)
- [Liệt kê các kiểu hỏng](#enumerate-the-failures)
- [Suy giảm, đừng sụp đổ](#degrade-do-not-collapse)
- [Bán kính ảnh hưởng](#blast-radius)
- [Biết rằng nó đang xảy ra](#know-that-it-is-happening)
- [Tập dượt sự cố](#practise-failing)
- [Những câu hỏi khó chịu](#the-uncomfortable-questions)
- [Checklist review](#review-checklist)

## Anti-pattern

Không phải một đoạn code — mà là một buổi review thiết kế chưa từng diễn ra.

```python
def order_page(order_id):
    order     = order_service.get(order_id)
    invoice   = billing_service.get_invoice(order_id)
    tracking  = shipping_service.get_tracking(order_id)
    loyalty   = loyalty_service.get_points(order.user_id)
    reviews   = review_service.get_prompts(order_id)
    return render(order, invoice, tracking, loyalty, reviews)
```

Năm dependency, và trang này cần đủ cả năm. Nếu bất kỳ cái nào không khả dụng — kể cả phần hiển thị
điểm thành viên và lời mời viết đánh giá — khách hàng sẽ không xem được đơn hàng của mình.

Phép tính availability không khoan nhượng: năm dependency ở mức 99.9% cho ra một trang ở mức **99.5%**,
tức khoảng 3.6 giờ downtime mỗi tháng, phần lớn do những thứ chẳng ai thấy thiếu.

Không ai quyết định điều này cả. Đó là kết quả khi mỗi dependency được thêm vào mà không ai hỏi chuyện
gì xảy ra khi nó biến mất.

## Vì sao môi trường dev che giấu lỗi này

Trong môi trường dev, dependency hoặc được nối vào, hoặc vắng mặt — không bao giờ *ốm yếu*. Không có gì
hỏng một phần, nên câu hỏi "trang này trông thế nào khi không có điểm thành viên?" không bao giờ bị buộc
phải đặt ra.

Sâu xa hơn: **các failure mode không nằm trong code, nên không thể review chúng bằng cách đọc code.**
Một timeout bị thiếu là vô hình. Một sự cố dependency không được xử lý là vô hình. Việc thiếu fallback
trông y hệt một quyết định rằng không cần fallback.

## Hai triết lý

> Trong một hệ phân tán, mọi thứ rồi sẽ hỏng, không có ngoại lệ.

Có hai cách phản ứng, và chúng dẫn tới hai kiến trúc khác nhau:

- **Tránh sự cố** — xem hỏng hóc là thứ phải diệt trừ, bằng thêm test, hạ tầng bền hơn, code chỉn chu
  hơn. Cách này ổn cho tới ngày nó không ổn nữa, và khi đó trong tay chẳng có phương án nào.
- **Chấp nhận sự cố** — xem hỏng hóc là một phần bình thường của vận hành, và thiết kế cho bước tiếp
  theo.

Cách thứ hai không phải là buông xuôi; nó là cách duy nhất trụ được. Bạn không thể ngăn sự cố của một
dependency, một lần phân vùng mạng, một lần deploy hỏng, hay một ổ đĩa đầy. Nhưng bạn *có thể* quyết
định trước hệ thống của mình sẽ làm gì khi chúng xảy ra.

Câu hỏi thiết kế dịch chuyển từ *"làm sao để cái này không hỏng?"* sang **"khi cái này hỏng, người dùng
trải nghiệm điều gì?"** — một câu hỏi có câu trả lời cụ thể và review được.

## Liệt kê các kiểu hỏng

Với mỗi dependency, các câu trả lời phải có trước khi code được ship:

| Kiểu hỏng            | Câu hỏi                                                                                                                              |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| **Chậm**             | Timeout là bao nhiêu, và chuyện gì xảy ra khi chạm tới nó? (Thường là trường hợp gây hại nhất — xem [network-and-latency](network-and-latency.md)) |
| **Sập**              | Cho request lỗi, hay suy giảm?                                                                                                       |
| **Sai**              | Dữ liệu sai hoặc hỏng định dạng — có được validate không, hay nó làm hỏng trạng thái?                                                |
| **Một phần**         | Nó đã thành công nhưng response bị mất. Retry có an toàn không? Xem [timeouts-and-retries](timeouts-and-retries.md)                  |
| **Quá tải**          | Nó trả về `429`/`503`. Bạn lùi lại, hay khuếch đại thêm?                                                                             |
| **Hồi phục chậm**    | Sau sự cố, đống retry tồn đọng của bạn có đánh gục nó thêm lần nữa không?                                                            |

Dòng cuối cùng là dòng hay bị bỏ sót nhất. Một dependency đang hồi phục sẽ phải hứng cùng lúc toàn bộ số
retry tích tụ của mọi client — đó là lý do jitter và retry budget là cơ chế hồi phục, không phải phép
lịch sự.

## Suy giảm, đừng sụp đổ

Phân loại mọi dependency, và làm cho việc phân loại hiện rõ trong code:

- **Thiết yếu** — thao tác vô nghĩa nếu thiếu nó. Ủy quyền thanh toán cho một lần mua hàng; chính bản ghi
  đơn hàng. Cho request lỗi một cách rõ ràng.
- **Bổ trợ** — làm trải nghiệm tốt hơn. Gợi ý sản phẩm, điểm thành viên, lời mời đánh giá. **Bỏ nó đi và
  phục vụ phần còn lại.**

Với trang đơn hàng ở trên:

```python
order = order_service.get(order_id)                 # thiết yếu — lỗi nếu không khả dụng
invoice = safe(billing_service.get_invoice, order_id, default=None)
tracking = safe(shipping_service.get_tracking, order_id, default=None)
loyalty = safe(loyalty_service.get_points, order.user_id, default=None)
reviews = safe(review_service.get_prompts, order_id, default=[])
return render(order, invoice, tracking, loyalty, reviews)   # render kể cả khi thiếu phần
```

Giờ availability của trang chỉ còn bằng availability của `order_service`, và trang hiển thị những gì
nó có thể.

Tuy vậy, suy giảm phải **tường minh và nhìn thấy được**. Một phần bị thiếu một cách im lặng dạy người
dùng rằng dữ liệu sai; một phần ghi "tạm thời không khả dụng" thì giữ được lòng tin của họ. Và có hai
nhóm **không bao giờ** được suy giảm theo hướng dễ dãi hơn: phân quyền phải fail closed, và tiền không
bao giờ được giả thành công. Xem
[circuit-breakers-and-bulkheads](circuit-breakers-and-bulkheads.md).

## Bán kính ảnh hưởng

Sự cố lan qua những gì dùng chung. Thu hẹp bán kính ảnh hưởng nghĩa là giảm dùng chung:

- **Pool dùng chung** — sự chậm chạp của một dependency vắt kiệt công suất của mọi thứ. Hãy dựng bulkhead
  cho chúng.
- **Database dùng chung** — query nặng của một tenant làm suy giảm mọi tenant. Tách pool, dùng read
  replica, hoặc giới hạn theo tenant.
- **Cache dùng chung** — cache sập biến thành một cơn stampede vào database. Xem
  [caching](caching.md).
- **Chuỗi đồng bộ** — A→B→C→D nghĩa là sự cố của D là sự cố của A. Hãy bẻ chuỗi bằng queue ở những chỗ
  công việc chấp nhận được xử lý bất đồng bộ.
- **Phạm vi deploy** — canary và rollout theo từng giai đoạn để một bản release hỏng chỉ ảnh hưởng một
  phần. Xem [deployment-and-config](deployment-and-config.md).
- **Phạm vi config** — một thay đổi config toàn cục có bán kính ảnh hưởng là tất cả, ngay lập tức.

Câu hỏi cần đặt ra khi review một dependency mới: *khi cái này hỏng, còn gì hỏng theo?* Nếu câu trả lời
là "gần như mọi thứ", thì phát hiện cần ghi nhận là sự ràng buộc, chứ không phải bản thân dependency.

## Biết rằng nó đang xảy ra

Thiết kế cho sự cố bao gồm cả việc nhận ra sự cố. Lỗ hổng cụ thể cần tìm: **suy giảm vốn dĩ im lặng
theo thiết kế** — hệ thống đang chạy đúng như thiết kế, nên chẳng có lỗi nào được ném ra.

- **Cảnh báo theo triệu chứng, không chỉ theo crash.** Tỷ lệ lỗi, độ trễ p99, mức bão hòa. Một service
  có thể hoàn toàn "up" mà vẫn vô dụng.
- **Gắn đo đạc cho các nhánh fallback.** Nếu phần điểm thành viên đã lỗi suốt một tuần, phải có người
  biết. Một fallback không ai giám sát sẽ trở thành vĩnh viễn.
- **Cảnh báo trên trạng thái circuit breaker, tỷ lệ retry, độ sâu queue, và độ sâu dead-letter.** Mỗi
  thứ là một tín hiệu sớm, kêu lên trước khi người dùng phàn nàn.
- **Làm cho trạng thái suy giảm quan sát được** — một metric cho từng dependency về số lần "phục vụ mà
  thiếu nó".
- **Liên kết bằng một request id xuyên suốt các service**, nếu không việc debug một sự cố một phần
  nghĩa là đọc năm file log chẳng liên quan gì đến nhau.

## Tập dượt sự cố

Một quy trình hồi phục chưa từng được chạy thì chỉ là một giả định:

- **Failover** — chủ động promote một replica. Xem
  [replication-and-sharding](replication-and-sharding.md).
- **Restore từ backup** — điều duy nhất biến một bản backup thành backup thật sự.
- **Rollback** — thực hành nó, không chỉ viết tài liệu.
- **Dependency hỏng** — chặn một dependency trên staging và xem người dùng nhận được gì. Đây là nơi fault
  injection chứng minh giá trị của mình; chaos engineering là phiên bản có kỷ luật của nó.
- **Load shedding** — xác minh nó kích hoạt trước khi queue đã quá sâu.

Giá trị không nằm ở việc chứng minh hệ thống sống sót. Nó nằm ở việc phát hiện ra đúng cái điều mà
chưa ai nghĩ tới, trong lúc cái giá còn rẻ.

## Những câu hỏi khó chịu

Đáng hỏi trong mọi buổi review thiết kế, và phần lớn hệ thống trả lời tệ ít nhất một câu:

- Nếu dependency lớn nhất của chúng ta sập một tiếng, cái gì vẫn còn chạy?
- Nếu database chỉ đọc được, chúng ta có phục vụ được gì không?
- Nếu cache trống trơn, chúng ta có chịu nổi tải không?
- Nếu một lần deploy hỏng, mất bao lâu để biết, và bao lâu để rollback?
- Nếu mất trọn một availability zone, chuyện gì xảy ra?
- Nếu một queue consumer đã sập sáu tiếng, đống tồn đọng sẽ hồi phục hay làm mọi thứ tệ hơn?
- Nếu hai instance cùng nghĩ mình là leader, cái gì sẽ hỏng? Xem
  [consensus-and-leader-election](consensus-and-leader-election.md)
- Lúc này đang có gì bị suy giảm âm thầm mà chúng ta không hề hay biết?

## Checklist review

- [ ] Mọi dependency đều được phân loại thiết yếu hay bổ trợ
- [ ] Dependency bổ trợ suy giảm mà không làm lỗi request
- [ ] Auth fail closed; tiền không bao giờ giả thành công
- [ ] Người dùng nhìn thấy được sự suy giảm, không phải im lặng
- [ ] Availability gộp của chuỗi dependency đã được tính toán
- [ ] Mỗi dependency đều có câu trả lời cho các trường hợp chậm, sập, sai, một phần, và quá tải
- [ ] Tải lúc hồi phục sau sự cố (retry tồn đọng) đã được tính đến
- [ ] Pool, cache và database dùng chung có bulkhead hoặc giới hạn theo tenant
- [ ] Chuỗi đồng bộ được bẻ bằng queue ở chỗ chấp nhận được xử lý bất đồng bộ
- [ ] Các nhánh fallback được đo đạc và có cảnh báo
- [ ] Trạng thái circuit breaker, tỷ lệ retry, độ sâu queue và độ sâu dead-letter được giám sát
- [ ] Request id liên kết được xuyên suốt các service
- [ ] Failover, restore và rollback đều đã được thực hành
- [ ] Việc dependency hỏng đã được test bằng cách thực sự làm hỏng nó trong một môi trường an toàn

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Distributed Systems của TechCraft, phần
P19 ([collection](https://www.patreon.com/techcraft_official)). Ý "mọi thứ chắc chắn sẽ hỏng" và cách
đặt vấn đề tránh sự cố hay chấp nhận sự cố là của TechCraft; phần liệt kê các kiểu hỏng, phân tích bán
kính ảnh hưởng, những câu hỏi khó chịu, và checklist là của repository này.*
