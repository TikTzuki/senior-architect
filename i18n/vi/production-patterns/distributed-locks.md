---
source_sha256: 1fd1e2cd5d7fa617fe57025f84719912c02791121d9f0861d10854629e7f5265
---

# Distributed lock

**Quy tắc: một lock nằm trong một process không bảo vệ được gì khi bạn chạy hai instance.** Và một
distributed lock không có TTL sẽ làm deadlock hệ thống, trong khi một lock có TTL lại không thực sự
đảm bảo được loại trừ lẫn nhau — chỉ fencing token mới làm được.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
[bộ nhớ & GC](memory-and-gc.md) · [caching](caching.md) ·
**distributed lock** (trang này) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
[deploy & cấu hình](deployment-and-config.md) ·
[định danh container & secret](container-identity-and-secrets.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Mỗi process có bộ nhớ riêng](#each-process-has-its-own-memory)
- [Lease: lock phải hết hạn](#the-lease-locks-must-expire)
- [Vì sao TTL phá vỡ loại trừ lẫn nhau](#why-a-ttl-breaks-mutual-exclusion)
- [Fencing token](#fencing-tokens)
- [Split brain](#split-brain)
- [Tốt nhất là không cần đến lock](#prefer-not-to-need-one)
- [Checklist review](#review-checklist)

## Anti-pattern

```java
// Chạy hoàn hảo trên một instance. Không bảo vệ được gì trên hai mươi instance.
public synchronized void reserveLastUnit(String sku) {
    int stock = inventory.get(sku);
    if (stock > 0) inventory.set(sku, stock - 1);
}
```

`synchronized` là một đảm bảo trong phạm vi **một JVM**. Đứng sau load balancer với hai mươi pod, hai
mươi thread giữ hai mươi monitor khác nhau và cùng lúc đi vào. Code trông như đã được bảo vệ nhưng
thực ra không — tổ hợp nguy hiểm nhất, và đây cũng chính là kiểu "sửa mà không sửa" đã nêu trong
[transaction-isolation](transaction-isolation.md).

Phiên bản phân tán ngây thơ thì gặp vấn đề ngược lại:

```python
if redis.setnx("lock:order", "1"):     # không có TTL
    process_order()
    redis.delete("lock:order")         # không bao giờ chạy nếu process chết
```

Pod bị OOM-kill giữa lúc đang xử lý và key đó giờ bị giữ vĩnh viễn. Mọi request sau này đều bị chặn
bởi một lock mà chủ nhân của nó không còn tồn tại.

## Vì sao môi trường dev che giấu lỗi này

Bạn chạy **một instance**. Một lock cục bộ trong process thực sự là đủ, và mọi test đều qua.

Lỗi xuất hiện vào đúng lúc ai đó scale lên hai replica — một thay đổi hạ tầng, không phải thay đổi
code, do một người không hề đọc đoạn code này thực hiện. Không có gì trong ứng dụng báo hiệu rằng tính
đúng đắn của nó phụ thuộc vào việc chỉ có đúng một replica.

## Mỗi process có bộ nhớ riêng

> Bộ nhớ không được chia sẻ giữa các server. Tin rằng lock trên một server là đủ chính là cái bẫy lớn
> nhất.

Mọi primitive nằm trong process — `synchronized`, `ReentrantLock`, `threading.Lock` của Python, một cờ
trong module Node — chỉ có phạm vi trong không gian địa chỉ của một process. Loại trừ lẫn nhau giữa
các instance đòi hỏi một **bên có thẩm quyền chung nằm bên ngoài**: Redis, ZooKeeper, etcd, Consul,
hoặc chính database.

Tín hiệu có thể review được là một sự lệch pha: một lock trong process đang canh giữ một tài nguyên
*không* nằm trong process (một dòng database, một API bên ngoài, một file dùng chung, một message trong
queue).

## Lease: lock phải hết hạn

Bên giữ một distributed lock có thể biến mất — crash, bị OOM-kill, phân vùng mạng, pod bị evict. Nó
không thể nhả thứ nó đang giữ, nên **mọi distributed lock phải là một lease có TTL**:

```python
token = str(uuid.uuid4())
if redis.set("lock:order:42", token, nx=True, ex=30):    # lấy lock + hết hạn, atomic
    try:
        process_order()
    finally:
        # Chỉ nhả nếu ta vẫn còn sở hữu — so sánh-rồi-xoá, một cách atomic.
        redis.eval("""
          if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
          end
          return 0
        """, 1, "lock:order:42", token)
```

Ba chi tiết, thiếu cái nào cũng là bug:

1. **Lấy lock và đặt hạn phải là một thao tác atomic** (`SET NX EX`). `SETNX` rồi mới `EXPIRE` có thể
   crash ở giữa và để lại một lock vĩnh viễn.
2. **Giá trị phải là một token duy nhất**, không phải `"1"`.
3. **Nhả lock phải là so sánh-rồi-xoá.** Một lệnh `DEL` trần có thể xoá lock *của người khác* nếu lock
   của bạn đã hết hạn — đúng kịch bản dưới đây.

## Vì sao TTL phá vỡ loại trừ lẫn nhau

Hệ quả khó chịu, và là phần mà hầu hết các implementation không bao giờ đối mặt:

```
t=0    A acquires the lock, TTL 30s
t=5    A pauses — GC pause, CPU starvation, network stall
t=30   lock expires. A does not know.
t=31   B acquires the lock. Legitimately.
t=32   A resumes, still believing it holds the lock
              →  A and B are both in the critical section
```

Không có bug implementation nào. Lock service hoạt động đúng. Khiếm khuyết nằm ở chỗ **một lease có
thể hết hạn mà bên giữ không hề hay biết**, và một process không thể tự phát hiện mình vừa bị đứng.

Vì vậy một lock dựa trên TTL cho bạn *hiệu quả* — thường chỉ một worker làm việc đó — nhưng **không cho
tính đúng đắn**. Nếu hai lượt thực thi đồng thời sẽ làm hỏng dữ liệu hoặc tiêu tiền hai lần, thì chỉ
một Redis lock không phải là sự bảo vệ đủ, dù được viết cẩn thận đến đâu.

Đây cũng là lý do gia hạn TTL bằng một heartbeat chạy nền có giúp nhưng không bịt được lỗ hổng: thread
heartbeat có thể bị đứng bởi chính lần GC pause đã làm đứng công việc.

## Fencing token

Cách sửa thật sự, khi tính đúng đắn là quan trọng. Lock service phát ra một **token tăng đơn điệu** với
mỗi lần cấp lock, và tài nguyên được bảo vệ **từ chối mọi lệnh ghi mang token cũ**:

```
A acquires  → token 41 → paused
B acquires  → token 42 → writes with token 42   → accepted, resource records 42
A resumes   → writes with token 41              → REJECTED (41 < 42)
```

Sự đảm bảo được chuyển từ lock sang tài nguyên được bảo vệ — đó mới là nơi có thể thực thi nó, vì tài
nguyên nhìn thấy các lệnh ghi.

Trên thực tế, phần lớn hệ thống đã có sẵn một cơ chế fencing và nên dùng nó thay cho lock:

- **Một cột version** với update có điều kiện — xem
  [locking](optimistic-vs-pessimistic-locking.md)
- **Một số thứ tự tăng đơn điệu** được kiểm tra khi ghi
- **Row lock của database**, nơi lock và dữ liệu nằm chung một transaction và không thể lệch nhau

Redlock của Redis đáng được nhắc tên: nó điều phối giữa các node Redis độc lập, và nó vẫn còn gây tranh
cãi chính vì không giải quyết được vấn đề pause ở trên. Đừng coi nó là sự đảm bảo tính đúng đắn cho
chuyện tiền bạc.

## Split brain

Một lần phân vùng mạng có thể khiến hai node cùng tin rằng mình đang giữ lock hoặc vai trò leader, và
cả hai cùng hành động. Với một lock service, điều này nghĩa là phía thiểu số trong phân vùng vẫn có thể
cấp hoặc tôn trọng lock.

Những gì quan trọng khi review:

- Dùng lock service có yêu cầu đa số **dựa trên consensus** (ZooKeeper, etcd) khi tính đúng đắn phụ
  thuộc vào nó, không dùng một Redis instance đơn lẻ mà failover có thể làm mất trạng thái lock.
- Hiểu rằng một lock được giữ trên primary bị failover **có thể không tồn tại** trên primary mới nếu
  replication là bất đồng bộ — cùng khoảng mất dữ liệu như trong
  [replication-and-sharding](replication-and-sharding.md).
- Ghép vai trò leader với fencing, để lệnh ghi của một leader đã lỗi thời bị từ chối chứ không chỉ bị
  khuyến cáo.

## Tốt nhất là không cần đến lock

Phần lớn distributed lock trong hệ thống thực tế đều có thể tránh được, và gỡ bỏ một lock tốt hơn là
hoàn thiện nó:

| Thay vì dùng lock                              | Hãy dùng                                                                          |
|------------------------------------------------|-----------------------------------------------------------------------------------|
| Canh giữ việc cập nhật counter hoặc số dư      | `UPDATE ... WHERE` có điều kiện, atomic                                           |
| Ngăn tạo bản ghi trùng                         | Ràng buộc `UNIQUE`                                                                |
| Đảm bảo chỉ một worker xử lý một job           | `SELECT ... FOR UPDATE SKIP LOCKED` trên một bảng queue                           |
| Đảm bảo chỉ một instance chạy tác vụ định kỳ   | Leader election, hoặc một dòng job được nhận một cách atomic                      |
| Ngăn side effect bị lặp                        | Idempotency key — xem [message-delivery-semantics](message-delivery-semantics.md) |
| Dựng lại một entry cache nóng đúng một lần     | Cache lock, khi "thường chỉ một lần" thực sự là đủ — xem [caching](caching.md)    |

Điểm phân biệt quyết định thiết kế: **lock này phục vụ hiệu quả hay tính đúng đắn?** Dựng lại cache và
khử trùng lặp job định kỳ là chuyện hiệu quả — thỉnh thoảng chạy hai lần chỉ lãng phí công sức và
chẳng hại gì. Dịch chuyển tiền và phân bổ tồn kho là chuyện đúng đắn, và chúng cần một ràng buộc hoặc
một fencing token, không phải một lease.

## Checklist review

- [ ] Không có lock trong process (`synchronized`, mutex) nào canh giữ tài nguyên dùng chung giữa các instance
- [ ] Tính đúng đắn không phụ thuộc vào việc chạy đúng một replica
- [ ] Mọi distributed lock đều có TTL, được đặt atomic cùng lúc lấy lock (`SET NX EX`)
- [ ] Giá trị lock là token duy nhất; nhả lock là so sánh-rồi-xoá, không phải xoá trần
- [ ] TTL của lock vượt thời lượng tệ nhất của critical section, có dư địa
- [ ] Mỗi lock đều ghi rõ nó phục vụ hiệu quả hay tính đúng đắn
- [ ] Các đường xử lý đòi hỏi tính đúng đắn dùng ràng buộc, update có điều kiện hoặc fencing token — không dùng lease
- [ ] Fencing token được kiểm tra bởi tài nguyên được bảo vệ, không tin vào token từ client
- [ ] Leader election và lock service dùng consensus khi tính đúng đắn phụ thuộc vào chúng
- [ ] Đã hiểu hành vi failover của nơi lưu lock (một lock có thể không sống sót qua failover)
- [ ] Thời gian chờ lock và tỉ lệ lấy lock thất bại được giám sát

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Backend Internals của TechCraft, phần
P11–P12 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "mỗi server có bộ nhớ
riêng", mối nguy lock vĩnh viễn cùng giải pháp lease, và phần xử lý split brain là của TechCraft; phân
tích fencing token, sự phân biệt giữa hiệu quả và tính đúng đắn, cùng checklist là của repository
này.*
