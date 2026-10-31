---
source_sha256: 4e68b57cfac84ec8f89f51a7ad5daaaa822135c50d269039cb0f9794e99fd2bb
---

# Health check, Discovery & Load Balancing

**Quy tắc: thêm server không làm hệ thống scale — phân phối đúng mới làm được.** Và một service trả
`200` cho health check trong khi không làm được việc gì còn tệ hơn một service sập hẳn.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
[bộ nhớ & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed lock](distributed-locks.md) ·
**health check & load balancing** (trang này) ·
[deploy & cấu hình](deployment-and-config.md) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Liveness không phải readiness](#liveness-is-not-readiness)
- [Health check nên kiểm tra gì](#what-a-health-check-should-test)
- [Những health check gây ra sự cố](#health-checks-that-cause-outages)
- [Service discovery](#service-discovery)
- [Load balancing không phải scale miễn phí](#load-balancing-is-not-free-scaling)
- [State là thứ làm nó vỡ](#state-is-what-breaks-it)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
@app.get("/health")
def health():
    return {"status": "ok"}        # trả 200 miễn là process còn tồn tại
```

Process vẫn sống. Connection pool tới database đã cạn, dependency phía downstream đang timeout, và nó
không phục vụ nổi một request thật nào — nhưng load balancer vẫn tiếp tục gửi traffic, vì nó chỉ hỏi
đúng câu duy nhất mà endpoint này trả lời được.

Một **zombie service**: còn sống theo mọi tín hiệu đang được đo, vô dụng trên thực tế. Tệ hơn crash,
vì pod bị crash sẽ được thay thế, còn cái này âm thầm hút vào và làm hỏng một phần toàn bộ traffic.

Sai lầm theo chiều ngược lại:

```yaml
livenessProbe:
  httpGet: { path: /health }     # và /health có kiểm tra database
```

Giờ thì một lần database chập chờn ngắn ngủi làm liveness probe thất bại trên **mọi** pod cùng lúc,
orchestrator khởi động lại tất cả, và một vấn đề dependency có thể tự hồi phục biến thành sự cố toàn
diện kèm một lần cold start.

## Vì sao môi trường dev che giấu lỗi này

Chỉ có một instance, không load balancer, không orchestrator. Chẳng có gì gọi tới health endpoint, nên
nó trả về gì cũng không được test và không ai xem xét.

Discovery cũng vắng mặt: `localhost:5432` lúc nào cũng resolve được, nên địa chỉ hardcode chạy hoàn
hảo cho tới môi trường đầu tiên mà các instance chỉ sống tạm thời.

## Liveness không phải readiness

Hai câu hỏi khác nhau với hai cách xử lý khác nhau, và gộp chúng làm một là gốc rễ của cả hai
anti-pattern ở trên:

| Probe         | Câu hỏi                                            | Nếu thất bại               | Nên kiểm tra                    |
|---------------|----------------------------------------------------|----------------------------|---------------------------------|
| **Liveness**  | Process này đã hỏng không thể cứu vãn chưa?        | **Khởi động lại**          | Chỉ trạng thái nội bộ process   |
| **Readiness** | Instance này có phục vụ traffic được *ngay lúc này*? | **Rút khỏi vòng phân phối** | Các dependency mà nó cần        |
| **Startup**   | Nó đã khởi tạo xong chưa?                          | Chờ thêm                   | Việc warm-up đã hoàn tất         |

Quy tắc rút ra: **liveness không được kiểm tra dependency.** Khởi động lại không sửa được database của
người khác, nên bắt liveness phụ thuộc vào nó là biến một vấn đề bên ngoài thành một vòng lặp restart —
và một vòng lặp restart đồng thời trên mọi replica tệ hơn nhiều so với một service xuống cấp.

Readiness thì *nên* kiểm tra dependency, vì rút một instance khỏi vòng phân phối trong khi các instance
khác vẫn phục vụ là phản ứng hoàn toàn đúng. Lưu ý sự bất đối xứng: nếu dependency sập với tất cả mọi
người, readiness thất bại ở mọi nơi và bạn không phục vụ được gì — nên với lỗi của dependency dùng
chung, hãy ưu tiên trả về response xuống cấp thay vì tuyên bố toàn bộ chưa sẵn sàng.

**Startup probe quan trọng hơn vẻ ngoài.** Thiếu nó, một service khởi động chậm (JIT warm-up, nạp sẵn
cache, chạy migration) sẽ bị liveness giết trước khi kịp sẵn sàng, tạo ra một crash loop trông như bug
trong code.

## Health check nên kiểm tra gì

Readiness nên xác minh những thứ instance này cần và có thể đánh giá với chi phí thấp:

- Database connection **lấy được từ pool** — không phải một query đầy đủ, cũng không phải một
  connection mới
- Các cache hoặc broker cần thiết có thể kết nối được
- Migration đã được áp dụng và trạng thái ứng dụng đã khởi tạo
- Hiện không đang trong quá trình tắt

Những gì nó không nên làm: chạy query tốn kém, fan-out tới mọi service downstream, hoặc mất lâu hơn
timeout của probe. Một health check nặng tới mức làm tăng tải tự nó đã là một vấn đề scale, và nó bị
mọi prober gọi liên tục.

Hai điểm implementation còn quan trọng hơn nội dung kiểm tra: **dùng một connection pool riêng** (hoặc
một connection dành riêng) cho health check, để một pool request đã cạn không làm probe thất bại và
loại bỏ một instance vẫn có thể hồi phục; và **cache kết quả trong thời gian ngắn** (một hai giây) để
việc probe dồn dập không nhân chi phí lên.

## Những health check gây ra sự cố

Đáng được review như một nhóm lỗi riêng, vì cơ chế của nó đi ngược trực giác:

- **Pool dùng chung** — pool request cạn, health check không lấy được connection, mọi instance thất bại
  readiness, và tất cả bị rút khỏi vòng phân phối. Xem
  [connection-pools-and-latency](connection-pools-and-latency.md).
- **Readiness dây chuyền** — readiness của A phụ thuộc B, của B phụ thuộc C. C chập chờn một chút và cả
  ba tầng báo chưa sẵn sàng, biến cú trục trặc của một dependency thành sự cố toàn hệ thống.
- **Ngưỡng quá gắt** — timeout một giây và chỉ cho phép một lần thất bại sẽ loại bỏ các instance khoẻ
  mạnh trong lúc GC pause. Xem [bộ nhớ & GC](memory-and-gc.md).
- **Không có graceful shutdown** — instance ngừng nhận việc trước khi load balancer nhận ra, nên các
  request đang xử lý bị lỗi. Readiness nên báo false *trước*, xả hết request, rồi mới thoát.

## Service discovery

Địa chỉ hardcode ngừng hoạt động ngay khi instance trở nên tạm thời — autoscaling, lập lịch lại, rolling
deploy đều liên tục thay đổi địa chỉ.

Một **service registry** giữ tập hợp hiện tại các instance khoẻ mạnh; service tra cứu theo *tên*, không
theo địa chỉ. Có hai kiểu topology:

- **Phía client** — bên gọi truy vấn registry và tự chọn một instance. Bớt một hop, nhưng logic
  discovery phải có mặt trong mọi service và mọi ngôn ngữ.
- **Phía server** — bên gọi đánh vào một địa chỉ ổn định (load balancer, Kubernetes Service, sidecar
  của mesh) và nó định tuyến tiếp. Client giữ được sự đơn giản; load balancer nằm trên đường đi.

Cần kiểm tra gì khi review, dù theo kiểu nào: **việc đăng ký gắn với readiness**, không gắn với lúc
process khởi động — một instance đăng ký trước khi phục vụ được sẽ nhận traffic mà nó sẽ làm hỏng.
**Huỷ đăng ký khi tắt phải tường minh**, nếu không bên gọi vẫn tiếp tục định tuyến tới một instance đã
chết cho tới khi hết TTL. Và registry là một dependency mà chính sự cố của nó cũng cần có phương án —
dùng bản cache tốt gần nhất đã biết tốt hơn là đóng cửa thất bại.

## Load balancing không phải scale miễn phí

> Số máy tăng lên không đồng nghĩa năng lực tăng theo. Phải có load balancing phân phối lưu lượng
> cho hợp lý thì chỗ máy mới đó mới phát huy tác dụng.

Hai instance đứng sau một load balancer dồn 90% traffic vào một cái không có nghĩa là gấp đôi năng
lực. Chiến lược phân phối mới là thứ biến instance thành throughput:

- **Round robin** — chia đều theo số request, không quan tâm chi phí của request. Ổn với công việc đồng
  đều.
- **Least connections** — tốt hơn khi thời lượng request thay đổi; instance chậm sẽ nhận ít hơn.
- **Weighted** — cho các instance có kích thước khác nhau, hoặc để dịch chuyển traffic trong lúc deploy.
- **Consistent hashing** — định tuyến một key cụ thể tới cùng một instance. Cần thiết cho tính cục bộ
  của cache; và lưu ý nó cố tình tạo ra tải không đều khi key bị lệch, chính là vấn đề
  [hot shard](replication-and-sharding.md) dưới một hình dạng khác.

Load balancer cũng phải **ngừng gửi traffic tới instance không khoẻ** — đó là toàn bộ lý do readiness
phải trung thực, và vì sao health check thụ động (quan sát lỗi của request thật) bắt được những thứ mà
probe chủ động bỏ lỡ.

Hai bổ sung quan trọng khi gặp sự cố thật: **outlier detection**, loại bỏ một instance có tỉ lệ lỗi
lệch hẳn dù probe của nó vẫn qua; và **load shedding**, khi trả `503` thật nhanh tốt hơn là xếp hàng
những việc không ai chờ.

## State là thứ làm nó vỡ

Load balancing chỉ chạy gọn gàng khi bất kỳ instance nào cũng phục vụ được bất kỳ request nào. State
nằm trong process phá vỡ điều đó:

- **Session trong bộ nhớ local** — request tiếp theo của người dùng rơi vào instance khác và họ bị đăng
  xuất. Sticky session che đi vấn đề nhưng lại đưa trở lại tải không đều và một bán kính ảnh hưởng khi
  lỗi. Hãy đưa ra ngoài Redis hoặc dùng token stateless có chữ ký.
- **Cache local** — mỗi instance tự làm nóng riêng, nên hit rate giảm khi scale out, và invalidation phải
  tới được mọi instance. Đây là chỗ consistent hashing xứng đáng với độ phức tạp của nó.
- **File upload và file tạm ở local** — một request tiếp theo trên instance khác không tìm thấy file.
  Hãy dùng object storage; xem [large-file-upload](large-file-upload.md).
- **Scheduler trong process** — mọi instance đều chạy cron job. Cần leader election hoặc một cách nhận
  việc atomic; xem [distributed lock](distributed-locks.md).
- **WebSocket và SSE** — connection sống lâu ghim một client vào một instance, nên deploy sẽ làm rớt
  connection và việc broadcast cần một đường pub/sub dùng chung.

## Checklist review

- [ ] Liveness và readiness là hai endpoint riêng với ngữ nghĩa khác nhau
- [ ] Liveness không kiểm tra gì bên ngoài — một lần restart có thể hợp lý sửa được điều nó báo cáo
- [ ] Readiness kiểm tra các dependency mà instance này cần, với chi phí thấp
- [ ] Có startup probe cho các service khởi tạo chậm
- [ ] Health check dùng connection dành riêng hoặc pool riêng, không dùng pool của request
- [ ] Timeout và ngưỡng thất bại của probe chịu được một lần GC pause
- [ ] Readiness báo false trước khi tắt, có xả request trước khi thoát
- [ ] Readiness không phụ thuộc bắc cầu vào readiness của các service khác
- [ ] Instance chỉ đăng ký khi đã sẵn sàng, và huỷ đăng ký tường minh khi tắt
- [ ] Sự cố của registry có phương án dự phòng (bản cache tốt gần nhất đã biết)
- [ ] Chiến lược load balancing phù hợp với độ biến động chi phí request, không mặc định round robin
- [ ] Outlier detection hoặc health check thụ động bổ sung cho probe chủ động
- [ ] Không có session, cache, file upload hay job định kỳ nào phụ thuộc vào state trong process
- [ ] Sticky session, nếu có dùng, là một đánh đổi có chủ đích chứ không phải tình cờ

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P17–P19 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn zombie service, sự phân
biệt liveness với readiness, ý "thêm server không tự động giúp scale", và thách thức của state là của
TechCraft; phân tích những sự cố do health check gây ra và checklist là của repository này.*
