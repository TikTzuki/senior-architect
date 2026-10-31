---
source_sha256: d89e9e8381211f6fd257d8b35e9ab704f2e847a94ac997866806676d77894c24
---

# Deploy & cấu hình

**Quy tắc: config đánh sập production nhanh hơn code, vì nó đi vòng qua mọi lớp bảo vệ mà code phải
vượt qua.** Và một lần deploy là một bài toán thiết kế, không phải một câu lệnh.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
[bộ nhớ & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed lock](distributed-locks.md) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
**deploy & cấu hình** (trang này) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Vì sao config nguy hiểm hơn code](#why-config-is-more-dangerous-than-code)
- [Kỷ luật với config](#config-discipline)
- [Feature flag và zombie flag](#feature-flags-and-zombie-flags)
- [Deploy là một thiết kế, không phải một câu lệnh](#a-deploy-is-a-design-not-a-command)
- [Các chiến lược](#the-strategies)
- [Deploy thực sự làm hỏng những gì](#what-deploys-actually-break)
- [Checklist review](#review-checklist)

## Anti-pattern

```yaml
# Thay đổi một ký tự. Không test. Thẳng lên production.
database:
  pool_size: 5          # trước là 50
  timeout_ms: 100       # trước là 5000
```

Không có dòng code nào thay đổi, nên chẳng có gì chạy: không unit test, không type checker, không
checklist review code, không canary. Pool cạn ngay lập tức và timeout 100 ms kích hoạt một cơn bão
retry kết liễu nốt phần còn lại.

Và phiên bản tương đương ở phía deploy:

```bash
kubectl set image deploy/api api=myapp:v2      # "lần deploy"
```

Được đối xử như một câu lệnh thay vì một thiết kế. Request đang xử lý bị rớt, migration của phiên bản
mới chưa chạy — hoặc đã chạy và không tương thích với các pod cũ vẫn đang phục vụ — và không có con
đường quay lui nào được định nghĩa.

## Vì sao môi trường dev che giấu lỗi này

Config ở môi trường dev là một file được commit với những giá trị chạy được, hiếm khi thay đổi và do
chính người hiểu chúng thay đổi. Không có giá trị production riêng nào để lệch đi, và không có override
lúc runtime.

Deploy ở local nghĩa là khởi động lại một process không có traffic. Không có gì đang xử lý dở, không
connection nào cần xả, không schema nào dùng chung với phiên bản trước, và rollback chỉ là
`git checkout`. **Mọi đặc tính khiến deploy production trở nên khó đều vắng mặt ngay từ cấu trúc.**

## Vì sao config nguy hiểm hơn code

> Thứ đánh sập production nhanh nhất thường không nằm trong code mà nằm trong config.

Code đi qua biên dịch, kiểm tra kiểu, test, review, CI và rollout theo giai đoạn. Config thường chẳng
đi qua cái nào trong số đó, vậy mà nó điều khiển cùng những hành vi ấy:

- **Timeout và retry** — timeout đặt quá thấp biến một dependency chậm thành một cơn bão retry; retry
  đặt quá cao khuếch đại sự cố thay vì giúp vượt qua nó.
- **Kích thước pool và các giới hạn** — xem
  [connection-pools-and-latency](connection-pools-and-latency.md) và
  [server-concurrency-model](server-concurrency-model.md).
- **Feature flag** — lật hành vi ứng dụng ngay tức khắc, cho tất cả mọi người, không cần deploy.
- **Endpoint và credential** — trỏ production vào nhầm database, hoặc nhầm hẳn môi trường.

Nó còn **toàn cục và tức thời**. Một lần deploy code lỗi được rollout từng pod một và có thể bị canary
phát hiện. Một giá trị config sai có thể áp dụng ở mọi nơi chỉ trong thời gian bấm lưu, không có phơi
nhiễm dần dần và thường cũng không có audit trail.

## Kỷ luật với config

- **Không bao giờ hardcode; luôn inject** — biến môi trường, một config service, hoặc secret được
  mount vào, để cùng một artifact chạy được ở mọi môi trường. Đó chính là mục đích của việc tách biệt.
- **Validate lúc khởi động, và fail fast.** Parse và kiểm tra khoảng giá trị của mọi biến khi process
  khởi động. Một pool size là `5` trong khi mức tối thiểu khả dụng là `20` thì nên từ chối khởi động,
  chứ không phải xuống cấp một cách khó hiểu khi có tải. Chỉ riêng thói quen này đã bắt được phần lớn
  sự cố liên quan đến config.
- **Coi thay đổi config là thay đổi thật.** Quản lý version, review và rollout theo giai đoạn — cùng con
  đường với code. "Chỉ là đổi config thôi mà" là câu nói thường đi trước một sự cố.
- **Không bao giờ log hay để lộ secret.** Che đi tại ranh giới logging; một endpoint dump config hoặc
  một dòng debug log in ra config đã nạp chính là rò rỉ credential.
- **Chủ động quyết định giá trị mặc định.** Một mặc định an toàn ở dev (pool nhỏ, timeout ngắn, log chi
  tiết) thường sai ở production, và việc thiếu một override là thứ không ai nhìn thấy.
- **Luôn nghĩ tới bán kính ảnh hưởng với config động.** Những giá trị có thể nạp lại lúc runtime rất
  mạnh và đi vòng hoàn toàn qua các lớp an toàn của deploy; những giá trị có thể gây sự cố xứng đáng
  được rollout cẩn thận như code.

## Feature flag và zombie flag

Flag tách việc deploy khỏi việc phát hành, và điều đó thực sự có giá trị: ship ở chế độ ẩn, bật dần
dần, tắt mà không cần rollback.

Cái giá là **zombie flag** — những flag sống lâu hơn mục đích của chúng và không bao giờ bị gỡ. Mỗi flag
là một nhánh điều kiện đang sống, nên `n` flag nghĩa là có tới `2ⁿ` đường đi có thể chạy tới, phần lớn
chưa từng được test cùng nhau. Flag cũ còn tự nó trở thành một mối nguy về độ tin cậy: không ai nhớ
phía "off" làm gì, và không test nào bao phủ nó.

Hãy coi mỗi flag như có hạn sử dụng: một người chịu trách nhiệm, một ngày gỡ bỏ, và việc dọn dẹp là một
phần của công việc chứ không phải một ticket để sau mà chẳng bao giờ làm. Một flag vẫn còn đó một năm
sau khi đã rollout toàn bộ là nợ kỹ thuật có gắn kèm công tắc.

## Deploy là một thiết kế, không phải một câu lệnh

Yêu cầu định hình mọi thứ: trong một lần rolling deploy, **hai phiên bản ứng dụng của bạn chạy đồng
thời trên cùng một database**. Vì vậy:

- Schema phải tương thích với cả hai phiên bản — đó chính là
  [expand và contract](schema-migrations.md).
- Message do phiên bản mới phát ra phải đọc được bởi các consumer vẫn còn ở phiên bản cũ, và ngược
  lại. Xem [message-delivery-semantics](message-delivery-semantics.md).
- Mọi giá trị cache do một phiên bản ghi phải được phiên bản kia hiểu được, hoặc key phải có version.
  Xem [caching](caching.md).

## Các chiến lược

| Chiến lược     | Cách làm                                                    | Cái giá                                                                                         |
|----------------|-------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| **Rolling**    | Thay instance theo từng đợt                                 | Hai phiên bản cùng sống một lúc; rollback chậm                                                  |
| **Blue-green** | Hai môi trường đầy đủ, chuyển traffic                       | Rollback tức thì; gấp đôi hạ tầng, và yêu cầu tương thích với database dùng chung vẫn còn nguyên |
| **Canary**     | Chuyển một % nhỏ sang phiên bản mới, theo dõi, rồi mở rộng  | Tỉ lệ rủi ro/phơi nhiễm tốt nhất; cần metric thật và tự động hoá                                 |

Canary thường là mặc định đúng cho một service đang chịu tải, và thứ nó phụ thuộc là những tín hiệu
trung thực: tỉ lệ lỗi, latency, và một metric nghiệp vụ được so sánh giữa hai phiên bản. Một canary mà
không ai đo đạc chỉ là rolling deploy thêm vài bước.

Dù chọn chiến lược nào, **rollback phải được test.** Một đường rollback chưa từng được chạy thử chỉ là
một giả định — cùng loại với một bản backup chưa test hay một lần failover chưa diễn tập trong
[replication-and-sharding](replication-and-sharding.md).

## Deploy thực sự làm hỏng những gì

Những phần nằm ngoài container image, và vì thế là những phần người ta hay quên:

- **Request đang xử lý.** Process phải bắt `SIGTERM`, báo readiness thất bại, xả request, rồi mới thoát
  — và grace period của orchestrator phải dài hơn request lâu nhất. Nếu không, lần deploy nào cũng rải
  lỗi ra ngoài.
- **Connection sống lâu.** Client WebSocket và SSE bị rớt sau mỗi lần deploy; chúng cần kết nối lại có
  backoff ở phía client, hoặc cơ chế xả connection.
- **Background job và consumer.** Một worker bị kill giữa chừng khi đang xử lý message phải được giao
  lại việc một cách an toàn — điều này đòi hỏi idempotency như trong
  [message-delivery-semantics](message-delivery-semantics.md).
- **Migration.** Phải chạy theo thứ tự an toàn cho cả hai phiên bản, và không được lock bảng vào giờ cao
  điểm. Xem [schema-migrations](schema-migrations.md).
- **Tác vụ định kỳ.** Một lần deploy giữa lúc đang chạy có thể làm bỏ lỡ hoặc chạy trùng một job.
- **Cold start.** Instance mới có cache local trống, JIT còn nguội, và pool chưa được làm nóng. Deploy
  tất cả cùng lúc nghĩa là mọi instance cùng chậm một lúc — và nếu tầng cache cũng vừa bị khởi động
  lại, đó là một vụ [cache avalanche](caching.md).

## Checklist review

- [ ] Không có config hardcode; tất cả được inject theo từng môi trường
- [ ] Mọi giá trị config được validate lúc khởi động, fail fast khi sai hoặc thiếu
- [ ] Giá trị timeout, retry và pool được review cẩn thận như code
- [ ] Thay đổi config được quản lý version, review và rollout theo giai đoạn
- [ ] Secret không bao giờ bị log hay để lộ qua một endpoint
- [ ] Giá trị mặc định cho production được ghi tường minh, không thừa hưởng từ dev
- [ ] Mỗi feature flag có người chịu trách nhiệm và ngày gỡ bỏ; flag lỗi thời bị xoá
- [ ] Lần deploy được thiết kế với giả định hai phiên bản cùng chạy trên một database
- [ ] Thay đổi schema theo expand/contract; migration an toàn cho cả hai phiên bản
- [ ] Định dạng message và cache tương thích theo cả hai chiều trong lúc rollout
- [ ] `SIGTERM` kích hoạt readiness-false, rồi xả request, rồi thoát — trong phạm vi grace period
- [ ] Grace period dài hơn request lâu nhất dự kiến
- [ ] Connection sống lâu kết nối lại có backoff
- [ ] Rollback đã được test, không chỉ được viết thành tài liệu
- [ ] Quyết định canary dựa trên tỉ lệ lỗi và latency đo được
- [ ] Đã tính tới chi phí cold start; không thay toàn bộ instance cùng một lúc

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P20–P21 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "config gây sự cố nhanh
hơn một bug trong code", lập luận deploy là một thiết kế, phần so sánh rolling/blue-green/canary, và
mối nguy zombie flag là của TechCraft; cấu trúc review và checklist là của repository này.*
