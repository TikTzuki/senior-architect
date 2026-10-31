---
source_sha256: 29d7ecb4514305df9d9f156ed067652a97b7280e27240c6bb2534ee7bfa2442d
---

# Caching & Cache Stampede

**Quy tắc: điều tệ nhất một cache có thể làm là để mọi request cùng dựng lại nó vào đúng một thời
điểm.** Cache không chỉ giảm tải — nó dồn tải lại một chỗ, và lúc key hết hạn là lúc hoá đơn tới.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
[bộ nhớ & GC](memory-and-gc.md) · **caching** (trang này) ·
[distributed lock](distributed-locks.md) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
[deploy & cấu hình](deployment-and-config.md) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Cache stampede](#cache-stampede)
- [Cách sửa: lock, hoặc trả dữ liệu cũ](#fixing-it-lock-or-serve-stale)
- [Thêm jitter vào TTL](#jitter-the-ttl)
- [Dữ liệu cũ là cái giá bạn chấp nhận](#staleness-is-the-trade-you-are-making)
- [Invalidation](#invalidation)
- [Hai "bầy đàn" còn lại](#the-other-two-herds)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
def get_product(pid):
    cached = redis.get(f"product:{pid}")
    if cached:
        return json.loads(cached)
    product = db.query_expensive_product(pid)          # 800 ms
    redis.setex(f"product:{pid}", 300, json.dumps(product))
    return product
```

Cache-aside đúng sách giáo khoa, và nó là một khẩu súng đã lên đạn. Với 5.000 request mỗi giây vào
một sản phẩm đang hot, ngay khi key đó hết hạn **cả 5.000 request đồng thời cùng miss một lúc**, và cả
5.000 cùng chạy câu query 800 ms.

Cache đang gánh 5.000 rps. Khi hết hạn, nó đổ toàn bộ lượng đó xuống database trong một khoảnh khắc.

## Vì sao môi trường dev che giấu lỗi này

Mỗi lần một request nghĩa là một lần miss luôn chỉ được phục vụ bởi đúng một lần dựng lại. Pattern
này đúng và trông như đã đầy đủ.

Lỗi chỉ xảy ra khi có **concurrency đúng vào thời điểm hết hạn** — nhiều request đang bay cho cùng một
key bên trong khoảng thời gian dựng lại. Đó là tích của traffic và thời gian dựng lại, mà ở local cả
hai đều gần bằng không. Load test cũng có thể bỏ sót, trừ khi nó tình cờ trùng với ranh giới TTL của
một hot key.

## Cache stampede

Còn được gọi là dog-piling hay thundering herd. Cơ chế như sau:

```
t=0      key expires
t=0..800ms   5,000 requests miss  →  5,000 identical expensive queries
             database CPU saturates
             connection pool exhausted        ← see connection-pools-and-latency
             every other endpoint slows or fails
t=800ms  5,000 responses each write the same key
```

Hãy để ý thiệt hại rơi vào đâu: không phải endpoint được cache mà là **khắp mọi nơi**, vì database và
connection pool là tài nguyên dùng chung. Một vụ stampede trên một key chẳng quan trọng gì cũng có thể
kéo sập luồng checkout.

Rồi hiệu ứng dây chuyền khép vòng: response chậm khiến client retry, retry cộng thêm tải, và những
request đã timeout vẫn tiếp tục được tính toán — năng lực xử lý bị tiêu vào những câu trả lời không ai
đọc.

Phiên bản cộng dồn còn tệ hơn. Nếu nhiều key được nạp cùng lúc — một lần deploy làm nóng cache, một
đợt import theo batch, một lần khởi động lại — chúng có chung TTL và **hết hạn cùng nhau**, nên
stampede phủ lên toàn bộ không gian key cùng một lúc.

## Cách sửa: lock, hoặc trả dữ liệu cũ

**Cách 1 — dựng lại dưới một lock.** Một request dựng lại; số còn lại chờ hoặc trả dữ liệu cũ.

```python
def get_product(pid):
    key = f"product:{pid}"
    cached = redis.get(key)
    if cached:
        return json.loads(cached)

    # Chỉ một bên thắng được dựng lại. SET NX là atomic.
    if redis.set(f"lock:{key}", "1", nx=True, ex=10):
        try:
            product = db.query_expensive_product(pid)
            redis.setex(key, 300, json.dumps(product))
            return product
        finally:
            redis.delete(f"lock:{key}")
    else:
        time.sleep(0.05)              # chờ một chút, rồi đọc lại
        cached = redis.get(key)
        if cached:
            return json.loads(cached)
        return db.query_expensive_product(pid)   # phương án cuối cùng
```

Lock **bắt buộc** phải có TTL, nếu không một bên giữ lock bị crash sẽ chặn key đó mãi mãi — cùng mối
nguy với [distributed lock](distributed-locks.md), và là lý do `SET NX EX` tồn tại như một thao tác
atomic duy nhất.

**Cách 2 — stale-while-revalidate, và thường là câu trả lời tốt hơn.** Lưu một thời điểm hết hạn logic
*bên trong* giá trị và giữ TTL vật lý dài hơn:

```python
entry = redis.get(key)          # TTL vật lý 3600s
if entry:
    if entry["fresh_until"] < now():
        trigger_async_refresh(key)     # một lần refresh, chạy nền
    return entry["value"]              # trả ngay, hơi cũ một chút
```

Không request nào phải chờ dựng lại. Việc dựng lại diễn ra ngoài đường xử lý request, và một lần dựng
lại thất bại chỉ khiến dữ liệu cũ hơn một chút thay vì gây sự cố. *Hơi cũ còn hơn hỏng* — với phần lớn
luồng đọc, đó là đánh đổi đúng.

## Thêm jitter vào TTL

Rẻ, và nó ngăn việc hết hạn đồng loạt:

```python
redis.setex(key, 300 + random.randint(0, 60), value)
```

Không có jitter, các key được ghi cùng lúc sẽ hết hạn cùng lúc. Chỉ một dòng này biến một vách đá
thành một con dốc, và nó nên là mặc định cho mọi cache được nạp hàng loạt.

## Dữ liệu cũ là cái giá bạn chấp nhận

Cache là một quyết định có chủ đích: phục vụ dữ liệu có thể sai. Hãy làm cho quyết định đó tường minh
thay vì để nó xảy ra tình cờ, và đặt TTL dựa trên **dữ liệu được phép sai đến mức nào**, chứ không theo
thói quen:

| Dữ liệu                                  | Mức chấp nhận                                                                   |
|------------------------------------------|---------------------------------------------------------------------------------|
| Mô tả sản phẩm, cây danh mục             | Vài phút đến vài giờ                                                            |
| Tồn kho hiển thị khi duyệt sản phẩm      | Vài giây — và kiểm tra lại từ nguồn chuẩn lúc checkout                          |
| Số dư tài khoản, trạng thái thanh toán   | Không cache; đọc từ nguồn chuẩn                                                 |
| Quyền và role                            | Ngắn, có invalidation tường minh khi thay đổi — một quyền đã cũ là vấn đề bảo mật |

Đây cũng chính là quyết định trong [consistency-boundaries](consistency-boundaries.md): điều gì phải
đúng ngay lúc này, và điều gì được phép trễ.

## Invalidation

- **Chỉ dùng TTL** — đơn giản nhất, và mọi thứ có thể cũ tới hết TTL.
- **Write-through / invalidation tường minh** — xoá hoặc cập nhật key khi nguồn thay đổi. Chính xác,
  nhưng dễ bỏ sót một đường ghi; chỉ cần một chỗ ai đó quên là dữ liệu sai được phục vụ vô thời hạn.
- **Key có version** — đưa version hoặc updated-at vào key, để một thay đổi khiến các entry cũ không
  còn truy cập được thay vì phải xoá. Tránh hẳn được nhóm lỗi quên invalidation, với cái giá là để lại
  rác chờ hết hạn.

Hai failure mode cần kiểm tra cụ thể: **race invalidate-rồi-ghi** (một reader đồng thời có thể nạp lại
giá trị cũ giữa lúc xoá và lúc ghi — hãy invalidate *sau* khi lệnh ghi đã commit), và **negative
caching**, khi một dòng không tồn tại không được cache, nên một loạt request cho các key không có sẽ
đánh vào database mọi lần.

## Hai "bầy đàn" còn lại

Stampede có những người họ hàng đáng gọi tên, vì cách giảm thiểu khác nhau:

- **Cache penetration** — request cho những key không tồn tại, nên không bao giờ có gì được cache và
  mọi request đều xuống tới database. Thường gặp dưới dạng tấn công. Cache kết quả rỗng trong thời gian
  ngắn, hoặc dùng Bloom filter để loại bỏ các key đã biết là không tồn tại.
- **Cache avalanche** — chính tầng cache bị sập hoặc khởi động lại trống rỗng. *Toàn bộ* traffic đổ
  xuống database cùng lúc. Cách giảm thiểu: gộp request, một tầng cache local trong process đặt trước
  tầng dùng chung, và một bộ load shedding để database xuống cấp thay vì chết hẳn. Một cache mà hệ thống
  không sống sót nổi khi mất nó là một dependency, không phải một tối ưu hoá — và cần được lập kế hoạch
  năng lực như một dependency.

## Checklist review

- [ ] Việc dựng lại hot key được bảo vệ bằng lock hoặc stale-while-revalidate, không để race tự do
- [ ] Mọi lock dùng để dựng lại đều có TTL và được lấy một cách atomic (`SET NX EX`)
- [ ] TTL có jitter, nhất là với các key được nạp hàng loạt
- [ ] Độ dài TTL được suy ra từ mức dữ liệu cũ chấp nhận được, ghi rõ cho từng loại dữ liệu
- [ ] Dữ liệu nguồn chuẩn (số dư, trạng thái thanh toán, tồn kho lúc checkout) không được phục vụ từ cache
- [ ] Cache quyền và role được invalidate tường minh khi thay đổi
- [ ] Invalidation diễn ra sau khi lệnh ghi đã commit, không phải trước
- [ ] Key không tồn tại được negative-cache hoặc lọc bỏ
- [ ] Hệ thống sống sót khi mất toàn bộ cache — việc xuống cấp đã được lên kế hoạch, không chỉ là giả thuyết
- [ ] Cache hit rate và tần suất dựng lại được giám sát; có alert khi hit rate sụt giảm
- [ ] Các entry trong cache được giới hạn bộ nhớ (xem [bộ nhớ & GC](memory-and-gc.md))

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P9–P10 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "sai lầm tệ nhất là để
mọi request cùng dựng lại một lúc", chuỗi domino từ miss tới sụp đổ, và hai lựa chọn cache lock so với
stale cache là của TechCraft; cấu trúc review và checklist là của repository này.*
