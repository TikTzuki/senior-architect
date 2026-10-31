---
source_sha256: dafb6ab1f028a3b90fdb4a880364f8765854ff9f10f2e8351eb85827a86ab194
---

# Replication & sharding

**Quy tắc: một replica là một thời điểm khác, và shard key là một quyết định bạn không thể hoàn
tác.** Đọc từ replica nghĩa là chấp nhận dữ liệu cũ; chọn sai shard key nghĩa là phải viết lại tầng dữ
liệu.

Thuộc nhóm storage: [storage engine](storage-engines.md) ·
[index & query plan](indexes-and-query-plans.md) · **replication & sharding** (trang này) · [bố trí replica & quorum](replica-placement-and-quorums.md) ·
[session consistency](session-consistency.md) · [connection pool & độ trễ](connection-pools-and-latency.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Replication lag không phải là trạng thái lỗi](#replication-lag-is-not-an-error-state)
- [Read-your-writes](#read-your-writes)
- [Thêm replica có thể làm lag tăng](#more-replicas-can-mean-more-lag)
- [Replication không phải là backup](#replication-is-not-a-backup)
- [Đồng bộ và bất đồng bộ](#synchronous-vs-asynchronous)
- [Failover làm mất dữ liệu, theo thiết kế](#failover-loses-data-by-design)
- [Sharding: key chính là kiến trúc](#sharding-the-key-is-the-architecture)
- [Shard nóng](#hot-shards)
- [Consistent hashing](#consistent-hashing)
- [Những gì bạn phải từ bỏ](#what-you-give-up)
- [Rebalancing là cuộc đua phải bắt đầu sớm](#rebalancing-is-a-race-you-start-early)
- [CAP, nói cụ thể](#cap-concretely)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
# "Reads go to replicas, that's the standard scaling pattern."
user_service.create(user)                    # writes to primary
profile = read_replica.get_user(user.id)     # ← may not exist yet
return render(profile)                       # 404 on a user just created
```

Và phiên bản tương ứng bên sharding, được quyết trong một buổi chiều:

```python
shard = hash(order.id) % 16      # even distribution, looks ideal
```

Phân bố đều không phải là mục tiêu — **khớp với query** mới là mục tiêu. Giờ đây mọi query "liệt kê
đơn hàng của khách này" đều chạm cả 16 shard, vì đơn hàng của một khách bị rải khắp tất cả các shard.

## Vì sao môi trường dev che giấu lỗi này

Chỉ có một database. Không có replica, nên lag đúng bằng không và đọc-sau-khi-ghi lúc nào cũng đúng.
Không có shard, nên mọi join và transaction đều cục bộ và miễn phí.

Cả hai lỗi chỉ xuất hiện sau một thay đổi hạ tầng được giới thiệu bằng những câu nghe rất yên tâm như
"chỉ thêm vài replica thôi" hay "chỉ shard thôi". Không cái nào là "chỉ" cả: cả hai đều thay đổi mô hình
consistency mà code ứng dụng đã được viết dựa trên.

## Replication lag không phải là trạng thái lỗi

Replica áp dụng các thay đổi từ log của primary sau khi primary đã commit chúng. Khoảng thời gian đó là
**hoạt động bình thường**, không phải sự cố. Thường chỉ vài mili giây, và kéo dài thành vài giây hoặc
vài phút khi có ghi hàng loạt, build index, transaction dài trên primary, hoặc mạng chịu áp lực.

> Nếu bạn đọc từ replica, bạn có thể thấy dữ liệu cũ trong khi primary đã có giá trị mới.

Vậy nên gửi lượt đọc sang replica là một quyết định về việc dữ liệu nào được phép cũ, và nó thuộc về
[ranh giới consistency](consistency-boundaries.md) — chứ không phải một cú tăng hiệu năng trong suốt,
không có giá.

**Định tuyến theo mức chịu đựng, không theo loại thao tác.** "Đọc thì sang replica" là một quy tắc quá
thô:

| Dữ liệu                                                                | Định tuyến               |
|---------------------------------------------------------------------|---------------------|
| Số dư, tồn kho, trạng thái thanh toán, bất cứ thứ gì sắp được dùng để ra quyết định | **Primary**         |
| Analytics, báo cáo, dashboard, tìm kiếm, gợi ý             | Replica             |
| Người dùng xem lại thứ họ vừa thay đổi                        | Primary (xem bên dưới) |

**Giám sát lag như một tín hiệu hạng nhất** và chủ động hạ cấp: nếu lag vượt ngưỡng, chuyển lượt đọc
về primary thay vì phục vụ dữ liệu cũ không rõ đến mức nào.

## Read-your-writes

Bug replica phổ biến nhất, và cũng dễ bị người dùng nhìn thấy nhất: người dùng cập nhật profile, được
redirect, đọc từ một replica chưa bắt kịp, và thấy giá trị cũ. Họ kết luận là lưu thất bại và làm lại
lần nữa.

Các lựa chọn, theo độ chắc chắn giảm dần:

1. **Định tuyến lượt đọc của người dùng đó về primary** trong một khoảng ngắn sau khi họ ghi (session
   stickiness). Đơn giản và hiệu quả.
2. **Theo dõi vị trí ghi** (LSN/GTID) và yêu cầu một replica ít nhất đã tới vị trí đó, nếu không thì
   quay về primary. Chính xác, nhưng cần nhiều cơ chế hơn.
3. **Hiển thị lạc quan từ giá trị vừa gửi lên** thay vì đọc lại. Thường là lời giải UX tốt nhất, và nó
   né được luôn vấn đề.

Read-your-writes chỉ là cái đầu tiên trong ba đảm bảo theo từng client, và sửa nó không sửa được hai
cái còn lại — người dùng có thể thấy lần ghi của chính mình mà vẫn thấy số dư lùi lại ở lần refresh tiếp
theo, hoặc thấy một câu trả lời trước khi thấy tin nhắn mà nó trả lời. Khi một luồng đọc có ý nghĩa với
người dùng, hãy đi qua [session consistency](session-consistency.md).

## Thêm replica có thể làm lag tăng

Phản xạ khi primary quá tải là thêm read replica. Mỗi replica thêm vào là thêm một đích mà primary phải
gửi luồng thay đổi tới, nên quá một mức nào đó, chính việc fan-out trở thành giới hạn: lag tăng trên
*tất cả* replica, và thay đổi nhằm mua thêm throughput lại nới rộng cửa sổ dữ liệu cũ.

Hai hệ quả khi review. Một kế hoạch thêm replica nên nói rõ nó kỳ vọng lag sẽ thay đổi thế nào, chứ không
chỉ nói về năng lực đọc. Và một đảm bảo consistency vẫn đứng vững ở ba replica không tự động còn đứng
vững ở mười replica.

## Replication không phải là backup

Đó là hai cơ chế khác nhau trả lời hai câu hỏi khác nhau, và nhập nhằng giữa chúng chính là cách người ta
phát hiện ra mình không có backup.

Replication sao chép trạng thái hiện tại, kể cả một lệnh `DROP TABLE`, một migration hỏng, hay một lần
ghi làm hỏng dữ liệu — nó sẽ tái tạo trung thành sai lầm của bạn lên mọi replica trong vài mili giây.
Backup tồn tại để khôi phục một trạng thái *trước đó*, đúng trường hợp mà replication theo thiết kế không
thể đáp ứng.

Nếu câu trả lời cho "lỡ ai đó xóa nhầm row thì sao" là "chúng ta có replica", thì đó là một lỗ hổng. Xem
[schema migration](schema-migrations.md) cho nửa phía migration của cùng rủi ro này.

## Đồng bộ và bất đồng bộ

- **Bất đồng bộ** — primary commit và xác nhận ngay; replica theo sau. Nhanh, nhưng khi primary hỏng có
  thể mất những transaction đã xác nhận.
- **Đồng bộ** — primary chờ replica xác nhận rồi mới báo commit. Không mất gì khi failover, nhưng mọi lần
  ghi giờ phải trả thêm độ trễ một vòng tới replica, và một replica không khỏe có thể làm việc ghi đứng
  hẳn.

Điểm cân bằng mà phần lớn hệ thống production muốn: **quorum** — chờ *một số* replica, không phải tất cả.
Trong PostgreSQL là `synchronous_standby_names` với `ANY 1 (...)`; trong MySQL là semi-synchronous
replication.

Nối lại với độ bền: một thiết lập async mà báo commit là bền vững đang đưa ra một lời hứa nó không giữ
được qua một lần failover. Xem
[transaction-atomicity-and-durability](transaction-atomicity-and-durability.md).

## Failover làm mất dữ liệu, theo thiết kế

Khi primary chết, một replica được promote — thường qua một cuộc bầu chọn giữa các ứng viên. Với
replication bất đồng bộ, những transaction primary đã xác nhận nhưng chưa kịp gửi đi sẽ **mất**, trong
khi ứng dụng đã được báo là chúng đã commit.

Đó là một đánh đổi có chủ đích, và nó phải là một con số đã biết:

- Biết **RPO** của bạn (được phép mất bao nhiêu dữ liệu) và cấu hình replication cho khớp — không phải
  ngược lại.
- **Test failover.** Một replica bạn chưa từng promote chỉ là một giả định, y hệt một bản backup bạn
  chưa từng restore.
- Lên kế hoạch cho trường hợp **split-brain**: hai node cùng tin mình là primary còn tệ hơn downtime, và
  fencing là thứ ngăn chuyện đó.

## Sharding: key chính là kiến trúc

Replication scale việc đọc và cho bạn tính sẵn sàng. Nó không scale **việc ghi** hay **khối lượng dữ
liệu** — mọi replica đều giữ toàn bộ dataset và áp dụng mọi lần ghi. Khi một máy không còn chứa nổi dữ
liệu hoặc không hấp thụ nổi tốc độ ghi, bạn shard.

**Chọn shard key từ kiểu truy cập, không phải từ độ phân bố.** Câu hỏi không phải "nó có rải đều không?"
mà là *"query chủ đạo lọc theo cái gì?"*

Với bảng đơn hàng mà gần như mọi query đều giới hạn trong một khách hàng, hãy shard theo `customer_id`:
đơn hàng của một khách nằm cùng nhau, và query thường gặp chỉ chạm một shard. Shard theo `order_id` thì
mọi query theo khách hàng trở thành scatter-gather qua tất cả shard — shard chậm nhất quyết định độ trễ
của bạn, và thứ bạn mua được là fan-out chứ không phải khả năng scale.

**Key gần như là vĩnh viễn.** Đổi nó nghĩa là di chuyển toàn bộ dữ liệu trong lúc vẫn phục vụ traffic.
Hãy đối xử với lựa chọn này nghiêm túc như với một schema bạn không thể migrate.

## Shard nóng

Phân bố đều *key* không đem lại phân bố đều *tải*. Một khách hàng doanh nghiệp có khối lượng gấp nghìn
lần những người khác sẽ dồn gấp nghìn lần traffic lên một shard, và shard đó giờ là giới hạn năng lực
của bạn trong khi các shard còn lại ngồi không.

Chuyện tương tự xảy ra mà không cần tenant lớn nào khi bạn **range-shard theo một key chỉ tăng** — một
timestamp, một ID auto-increment, một ngày. Mỗi row mới đều xếp sau mọi row đã có, nên mọi lần insert
đều rơi vào shard mới nhất, còn các shard giữ dữ liệu lịch sử chỉ thỉnh thoảng phục vụ vài lượt đọc.
Thêm shard không giúp được gì: shard mới chỉ đơn giản trở thành shard kế tiếp gánh mọi lần ghi. Nó qua
mọi bài test, vì dataset test đủ nhỏ để "shard mới nhất" cũng là shard duy nhất. Range sharding là lựa
chọn đúng khi range scan chiếm ưu thế *và* key được rải đều trên cả dải; với một key tăng mãi, hãy hash
nó, hoặc thêm vào trước nó một thứ giúp rải các lần ghi (tenant hoặc số bucket) và chấp nhận rằng một
query theo khoảng thời gian giờ sẽ chạm mọi shard.

Phân vùng theo một thuộc tính ngoài đời thực — quốc gia, khu vực, dòng sản phẩm — thất bại theo cùng
cách, chỉ chậm hơn: các phân vùng phản chiếu các thị trường thật, và các thị trường thật chênh nhau về
quy mô hàng bậc độ lớn.

Cách giảm thiểu: composite key để chia tenant lớn ra nhiều shard, shard riêng dành cho tenant quá khổ,
hoặc một mapping dựa trên directory để có thể dời từng key riêng lẻ. Tất cả đều dễ áp dụng hơn nếu ngay
từ đầu bạn đã giả định phân bố bị lệch — phân bố key ngoài đời thực gần như không bao giờ đồng đều.

## Consistent hashing

`hash(key) % N` là cái bẫy. Đổi `N` từ 3 thành 4 và **gần như mọi key bị ánh xạ lại** — vậy nên "chỉ
cần thêm một server" kéo theo việc di chuyển gần như toàn bộ dữ liệu, và nếu store là cache thì là một
cơn bão miss gần như toàn bộ, dội vào database cùng một lúc (một [cache avalanche](caching.md)).

> Lắp thêm server hiếm khi giải quyết được sự cố cũ — thường nó mở màn cho một sự cố mới.

Consistent hashing đặt node và key lên một vòng hash; một key thuộc về node kế tiếp theo chiều kim đồng
hồ. Thêm một node chỉ gán lại các key trong cung của nó — khoảng `1/N` số key — và bỏ một node thì chuyển
cung của nó sang đúng một node lân cận.

Hai chi tiết quan trọng trong thực tế. **Virtual node** là bắt buộc, không phải tùy chọn: với một điểm
cho mỗi node vật lý, vòng sẽ không đều và tải bị lệch, nên mỗi node được đặt ở 100–200 điểm để làm mượt
phân bố. Và **consistent hashing giới hạn lượng di chuyển, không giới hạn độ lệch** — một hot key vẫn
rơi vào một node, đó vẫn nguyên là vấn đề [shard nóng](#hot-shards).

Nó xuất hiện ở đâu: cache cluster, store đã shard, và load balancer định tuyến theo key để tận dụng
tính cục bộ của cache (xem
[health-checks-and-load-balancing](health-checks-and-load-balancing.md)).

## Những gì bạn phải từ bỏ

Sharding khiến những thứ trước đây miễn phí giờ có giá, và câu hỏi khi review là thiết kế đã đối mặt với
chúng hay chưa:

- **Join xuyên shard** — không còn là một thao tác của database. Hoặc denormalize, hoặc join ở tầng ứng
  dụng.
- **Transaction xuyên shard** — không còn nữa. Giờ đây đây là bài toán distributed transaction; xem
  [distributed-transactions](distributed-transactions.md).
- **Tính duy nhất toàn cục** — `AUTO_INCREMENT` theo từng shard sẽ trùng nhau. Dùng ULID/UUIDv7 hoặc một
  service cấp ID tập trung; lưu ý hệ quả về tính cục bộ khi lưu trữ trong
  [storage-engines](storage-engines.md).
- **Thứ tự và phép tổng hợp toàn cục** — `COUNT(*)`, `ORDER BY` và phân trang xuyên shard đòi hỏi
  scatter-gather rồi gộp kết quả.
- **Rebalancing** — thêm shard là di chuyển dữ liệu. Consistent hashing hoặc một tầng directory giúp
  việc này sống sót được; `hash % N` biến việc thêm một shard thành cuộc migration gần như mọi thứ.

## Rebalancing là cuộc đua phải bắt đầu sớm

Di chuyển dữ liệu của một shard là sao chép hàng terabyte qua chính những đĩa và đường mạng đang phục vụ
production. Điều đó tạo ra một cuộc đua có hai cách để thua:

- **Sao chép nhanh** thì các lượt đọc và ghi của migration tranh tài nguyên với query thật — độ trễ trên
  shard nguồn tăng đúng lúc nó vốn đã bận nhất, vì đó chính là lý do nó đang bị tách.
- **Sao chép chậm** thì shard nguồn tiếp tục lớn lên trong lúc bạn rút dữ liệu khỏi nó. Bắt đầu khi đã
  đầy 90%, một lần di chuyển bị giới hạn tốc độ có thể thua tốc độ tăng trưởng và shard hết đĩa giữa
  chừng migration.

Vậy nên điểm kích hoạt là một **ngưỡng dung lượng còn cách xa mức đầy** — thường là 60–70% đĩa hoặc dư
địa ghi, mức điển hình chứ không phải phổ quát — đi kèm một tốc độ sao chép đã được đo, để thời gian di
chuyển được biết trước khi cần đến. Một lần rebalance lên kế hoạch ở mức 90% là một sự cố với thêm vài
bước.

Bản thân việc di chuyển phải online và kiểm chứng được, theo thứ tự này:

1. **Bắt đầu ghi nhận thay đổi** cho dải key đang di chuyển (dual-write, hoặc tail luồng thay đổi của
   nguồn) *trước khi* sao chép, để không bỏ sót thứ gì được ghi trong lúc sao chép.
2. **Backfill** các row đang có, giới hạn tốc độ theo độ trễ của nguồn, không theo một tốc độ cố định.
3. **Kiểm chứng** — số row và checksum theo từng dải key — trước khi chuyển bất kỳ lượt đọc nào.
4. **Cut over** định tuyến cho dải đó, giữ nguồn ở chế độ chỉ đọc trong một cửa sổ rollback.
5. **Xóa** khỏi nguồn chỉ sau khi hết cửa sổ, như một bước riêng.

Mỗi bước đều có thể tiếp tục lại và idempotent; một migration phải chạy lại từ đầu sau khi hỏng ở 80% sẽ
không bao giờ xong khi đang chịu tải. Các hệ thống có tầng directory hoặc nhiều shard logic nhỏ trên mỗi
node (Vitess, Citus, vnode của Cassandra) biến việc này thành thường lệ; với vài shard vật lý lớn thì lần
nào cũng là một dự án.

## CAP, nói cụ thể

Khi có **network partition**, bạn phải chọn:

- **CP** — từ chối phục vụ thay vì liều trả dữ liệu sai. Đúng cho số dư, tồn kho, đặt chỗ.
- **AP** — tiếp tục phục vụ, hội tụ sau. Đúng cho feed, catalogue, gợi ý.

"CA" không phải là một lựa chọn trong hệ phân tán, vì partition không phải thứ bạn được chọn có hay
không. Và lựa chọn này không áp cho toàn hệ thống: nó được đưa ra **theo từng loại dữ liệu**, cũng chính
là quyết định về [ranh giới consistency](consistency-boundaries.md). Một hệ thống chưa đưa ra quyết định
này một cách tường minh thì đã đưa ra nó một cách vô tình, và thường là không nhất quán.

## Checklist review

- [ ] Thay đổi này có thêm replica không? Nếu có, ảnh hưởng dự kiến lên replication lag là gì, chứ không chỉ lên năng lực đọc?
- [ ] Có đang dựa vào replication cho thứ lẽ ra backup phải lo không? Một câu lệnh phá hủy cũng được replicate
  trung thành y như một câu lệnh đúng.

- [ ] Việc đọc từ replica được chọn theo từng loại dữ liệu dựa trên mức chịu dữ liệu cũ, không theo kiểu "đọc thì sang replica"
- [ ] Dữ liệu sắp dùng để ra quyết định (số dư, tồn kho, trạng thái thanh toán) được đọc từ primary
- [ ] Read-your-writes được xử lý cho các luồng người dùng nhìn thấy
- [ ] Replication lag được giám sát, có hạ cấp chủ động khi vượt ngưỡng
- [ ] Chế độ replication (async / quorum / sync) khớp với RPO đã tuyên bố
- [ ] Các cam kết về độ bền đã tính đến những gì failover làm mất
- [ ] Failover đã được test bằng cách thực sự promote một replica
- [ ] Có fencing chống split-brain
- [ ] Shard key được rút ra từ điều kiện lọc của query chủ đạo, không phải từ độ đều của hash
- [ ] Đã giả định phân bố key bị lệch; có biện pháp giảm thiểu shard nóng
- [ ] Không range sharding theo key tăng mãi (timestamp, auto-increment) — mọi lần ghi sẽ dồn vào shard mới nhất
- [ ] Join, transaction, tính duy nhất và phân trang xuyên shard đều có câu trả lời
- [ ] Chiến lược rebalancing tránh được `hash % N` vốn đòi hỏi migration toàn bộ
- [ ] Một ngưỡng dung lượng còn xa mức đầy sẽ kích hoạt rebalancing, và thời gian sao chép ở kích thước đó đã được biết
- [ ] Việc di chuyển shard ghi nhận thay đổi trước khi backfill, kiểm chứng bằng checksum, và giữ nguồn cho đến khi hết cửa sổ rollback
- [ ] CP hay AP được quyết định theo từng loại dữ liệu và được ghi lại
- [ ] Phân bố key dùng consistent hashing với virtual node, không dùng `hash % N`
- [ ] Thêm hoặc bớt một node chỉ di chuyển khoảng 1/N số key, không phải gần như tất cả

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Database Internals của TechCraft, các
phần P11–P12 và P14 ([collection](https://www.patreon.com/collection/2115129)), Distributed Systems các
phần P15, P17–P18, và Replication & Scaling P12 và P19
([collection](https://www.patreon.com/collection/2351128)). Cách nhìn về replication lag, quy tắc shard
theo kiểu truy cập, cái bẫy shard nóng, và lập luận "CA chỉ tồn tại trên lý thuyết" là của TechCraft;
cấu trúc review và checklist là của repository này.*
