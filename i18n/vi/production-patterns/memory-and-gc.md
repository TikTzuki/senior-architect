---
source_sha256: 01374a97aaa1bf4e3fa6aa25db4c9c791ed95da7d4ffef4a8b506f069e35f60a
---

# Bộ nhớ & Garbage Collection

**Quy tắc: phần lớn RAM của server không phải là dữ liệu của bạn, và chi phí của bộ thu gom được trả
bằng latency mà bạn không hề viết ra.** Tốc độ cấp phát, chứ không phải kích thước heap, mới là thứ
khiến GC gây đau.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
**bộ nhớ & GC** (trang này) · [caching](caching.md) ·
[distributed lock](distributed-locks.md) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
[deploy & cấu hình](deployment-and-config.md) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [RAM thực sự đi đâu](#where-the-ram-actually-goes)
- [GC không miễn phí](#gc-is-not-free)
- [Code của bạn quyết định GC đau đến mức nào](#your-code-decides-how-much-gc-hurts)
- [Những vụ leak không phải leak](#leaks-that-are-not-leaks)
- [Container làm mọi thứ tệ hơn](#containers-make-it-worse)
- [Checklist review](#review-checklist)

## Anti-pattern

```java
// Nạp tất cả, biến đổi, trả về. Đúng, và sẽ OOM.
List<Order> all = orderRepository.findAll();          // 4 triệu dòng vào heap
return all.stream()
          .map(this::toDto)                            // thêm 4 triệu object nữa
          .collect(Collectors.toList());               // và một bản sao thứ ba
```

```java
// Một cache không giới hạn. Về mặt kỹ thuật là cache; về chức năng là leak.
private static final Map<String, Report> CACHE = new HashMap<>();
```

Cả hai đều không phải bug theo nghĩa thông thường. Cả hai đều chạy được, qua test, và hoạt động tốt
trong nhiều tháng — cho tới khi bảng phình to hoặc không gian key mở rộng.

## Vì sao môi trường dev che giấu lỗi này

Dữ liệu ở môi trường dev nhỏ, nên `findAll()` trả về 200 dòng và cache không giới hạn chỉ chứa dăm ba
key. Chẳng có gì tích tụ vì process liên tục bị khởi động lại.

Hành vi của GC thì vô hình vì một lý do khác: **một process còn non với heap nhỏ thì thu gom rất rẻ.**
Thời gian pause tăng theo kích thước live set và tốc độ cấp phát, mà cả hai chỉ lộ ra khi có traffic
production và uptime dài. Một bài load test chạy hai phút sẽ không cho thấy heap sau tám tiếng trông
như thế nào.

## RAM thực sự đi đâu

Một server dùng 4 GB hiếm khi đang giữ 4 GB dữ liệu nghiệp vụ:

- **Stack của thread** — mỗi cái 512 KB–1 MB. 500 thread là 250–500 MB trước khi tính bất cứ thứ gì
  khác. Đây là chi phí ẩn của mô hình thread-per-request; xem
  [mô hình concurrency](server-concurrency-model.md).
- **Buffer của connection** — buffer đọc và ghi cho mỗi socket, cộng thêm trạng thái TLS session. Với
  64 KB mỗi connection, 10.000 connection là 640 MB.
- **Overhead của runtime** — metaspace của JVM, code cache, code đã được JIT biên dịch, các cấu trúc
  của interpreter.
- **Overhead trên mỗi object** — header của một object JVM là 12–16 byte. Một `Long` tốn 16–24 byte để
  chứa 8 byte giá trị. Một entry của `HashMap` tốn thêm ~32–48 byte ngoài key và value. Những cấu trúc
  dữ liệu chứa nhiều object nhỏ tốn gấp mấy lần kích thước dữ liệu chúng chứa.
- **Phân mảnh của allocator** — bộ nhớ được giải phóng trả về cho allocator, thường không trả về cho
  OS, nên RSS vẫn cao sau một đợt tăng đột biến. **Bộ nhớ không trả về OS là bình thường, không phải
  leak** — và nhầm lẫn hai thứ này khiến các team đi săn một con bug không tồn tại.
- **Off-heap** — direct byte buffer, file được memory-map, thư viện native. Monitoring heap không thấy
  được chúng, đó là lý do một container có thể bị OOM-kill trong khi đồ thị heap trông yên ả.

## GC không miễn phí

Bộ thu gom phải tìm ra các object còn sống, và công việc đó tranh tài nguyên với việc xử lý request
của bạn.

**GC pause** là phần hiện ra trong latency. Các collector hiện đại chạy song song với ứng dụng ở hầu
hết các pha nhưng vẫn có những điểm stop-the-world. Dấu hiệu trên production là **p99 tệ hơn p50 rất
nhiều mà không rõ lý do** — phần lớn request vẫn ổn, còn những request kém may mắn rơi đúng vào một
lần pause.

Các collector phân thế hệ tận dụng thực tế là phần lớn object chết trẻ: một lượt thu gom thế hệ trẻ
rẻ tiền sẽ thu hồi chúng, còn object sống sót thì được thăng cấp lên thế hệ già. Từ đó rút ra quy tắc
quan trọng:

> Một object chết ngay gần như không tốn gì. Một object sống đủ lâu để được thăng cấp rồi mới chết thì
> rất đắt.

Vì vậy pattern bệnh hoạn là **object sống trung bình** — được cấp phát cho mỗi request, sống sót qua
lượt thu gom thế hệ trẻ, được thăng cấp, rồi bị bỏ đi. Chúng buộc collector phải làm việc ở thế hệ già,
loại việc đắt đỏ.

## Code của bạn quyết định GC đau đến mức nào

Bạn không thể tune để thoát khỏi một vấn đề cấp phát, và đây là phần có thể review được:

- **Tốc độ cấp phát là đòn bẩy chính.** Giảm một nửa lượng cấp phát thì tần suất thu gom cũng giảm
  khoảng một nửa. Hãy tìm việc tạo object liên tục cho mỗi request trong vòng lặp, nối chuỗi trên hot
  path, boxing kiểu nguyên thuỷ, và những bản sao phòng thủ không ai đọc.
- **Đừng vật chất hoá toàn bộ tập kết quả.** Stream, phân trang, hoặc xử lý theo batch. `findAll()`
  trên một bảng đang lớn dần là quả bom hẹn giờ mà đồng hồ được đặt bởi tốc độ tăng dữ liệu.
- **Để ý đường thăng cấp.** Buffer và collection lớn cho mỗi request sống lâu hơn một lượt thu gom thế
  hệ trẻ chính là thứ tạo áp lực lên thế hệ già. Định cỡ collection đúng ngay từ đầu tránh được việc
  lặp đi lặp lại phình-rồi-sao-chép, vốn sinh ra rác *và* cả thăng cấp.
- **Cẩn thận với object lớn.** Ở một số collector, object vượt quá một ngưỡng được cấp phát thẳng vào
  vùng già (hoặc thành các region "humongous"), bỏ qua hoàn toàn con đường rẻ.
- **Giới hạn mọi cache.** Giới hạn kích thước hoặc số entry kèm eviction, không dùng một map trần. Xem
  [caching](caching.md).

Tuning có chỗ đứng của nó — chọn collector, định cỡ heap, kích thước region — nhưng nó chỉ phân bổ lại
chi phí. Giảm rác mới loại bỏ được chi phí.

## Những vụ leak không phải leak

Trong một managed runtime, leak thật sự là hiếm; giữ tham chiếu ngoài ý muốn thì rất phổ biến:

- **Cache và map không giới hạn** — nguyên nhân thường gặp nhất, bỏ xa phần còn lại.
- **Collection static** chỉ có tăng mà không bao giờ giảm.
- **Listener và callback không bao giờ được huỷ đăng ký**, giữ luôn cả scope bao quanh chúng.
- **`ThreadLocal` trên thread nằm trong pool** — thread sống lâu hơn request, nên giá trị bị giữ vô
  thời hạn. Hãy xoá nó trong `finally`.
- **View kiểu substring/slice** giữ lại một buffer gốc lớn hơn nhiều.
- **Closure bắt nhiều hơn dự định** trong JavaScript, khiến các object lớn vẫn còn reachable.

Dấu hiệu nhận biết là **live set tăng đơn điệu qua các chu kỳ GC**. Heap hình răng cưa quay về cùng
một mức đáy là khoẻ mạnh; mức đáy cứ dâng lên là có giữ tham chiếu. Hãy alert theo kích thước live set
sau thu gom, không phải theo tổng heap đã dùng — con số sau vốn dĩ phải dao động.

## Container làm mọi thứ tệ hơn

Hai cái bẫy cụ thể đáng kiểm tra khi review:

- **Runtime phải thấy được giới hạn của container.** Một JVM đời cũ đọc bộ nhớ của host và định cỡ
  heap cho một cỗ máy nó không có, rồi bị OOM-kill. JVM hiện đại có nhận biết container
  (`MaxRAMPercentage`); hãy kiểm chứng thay vì giả định.
- **Heap không phải là giới hạn.** `container_limit ≥ heap + metaspace + code cache + thread stacks +
  direct buffers + native`. Đặt `-Xmx` bằng giới hạn container đảm bảo sớm muộn gì cũng bị kill, và
  lần kill đó trông như một vụ crash không có exception — SIGKILL không để lại stack trace, đó là lý
  do những sự cố này rất hay bị chẩn đoán sai.

## Checklist review

- [ ] Không có kết quả query không giới hạn nào được nạp vào bộ nhớ; đọc lượng lớn thì stream, phân trang hoặc chia batch
- [ ] Mọi cache và map sống lâu đều có giới hạn kích thước và chính sách eviction
- [ ] Giá trị `ThreadLocal` được xoá trong `finally` khi thread nằm trong pool
- [ ] Listener, callback và subscription đều được huỷ đăng ký
- [ ] Collection được định cỡ sẵn ở những chỗ đã biết trước kích thước
- [ ] Tốc độ cấp phát được đo trên hot path, không chỉ mức dùng heap
- [ ] Kích thước live set sau thu gom được giám sát và có alert, không phải tổng heap
- [ ] Độ chênh latency giữa p99 và p50 được điều tra như một khả năng GC pause
- [ ] Giới hạn bộ nhớ của container tính cả heap **cộng** metaspace, stack và off-heap
- [ ] Runtime nhận biết container, đã được kiểm chứng chứ không giả định
- [ ] Số thread được hiểu là một chi phí bộ nhớ, không chỉ là chi phí lập lịch

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P6–P7 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "RAM không chỉ là code của
bạn", phân tích chi phí cấp phát so với giải phóng, và ý "code của bạn trực tiếp trừng phạt GC" là
của TechCraft; cấu trúc review và checklist là của repository này.*
