---
source_sha256: 1b808427f536df277fcc11cb0dcd5de31b297cddbe0915b7c051d49c70688091
---

# Storage engine

**Quy tắc: database không đọc từng row — nó đọc các page có kích thước cố định.** Gần như mọi "query
chậm" thực chất là "phải lấy quá nhiều page từ đĩa", và cách bạn chọn key quyết định con số đó.

Thuộc nhóm storage: **storage engine** (trang này) ·
[index & query plan](indexes-and-query-plans.md) · [replication & sharding](replication-and-sharding.md) · [bố trí replica & quorum](replica-placement-and-quorums.md) ·
[session consistency](session-consistency.md) · [connection pool & độ trễ](connection-pools-and-latency.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Page, không phải row](#pages-not-rows)
- [UUID ngẫu nhiên phá hủy tính cục bộ khi ghi](#random-uuids-destroy-write-locality)
- [B-Tree và LSM Tree](#b-tree-vs-lsm-tree)
- [Ba loại khuếch đại](#the-three-amplifications)
- [Compaction và vacuum](#compaction-and-vacuum)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
CREATE TABLE events (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),   -- random v4
  payload    jsonb,
  created_at timestamptz DEFAULT now()
);
```

Gọn gàng, dễ mang sang database khác, không tranh chấp sequence — và xuống cấp nặng khi dữ liệu lớn
vì một lý do mà nhìn schema không thấy được: mỗi lần insert rơi vào một vị trí ngẫu nhiên trong index,
nên engine gần như lần nào cũng phải lấy, sửa rồi ghi lại một page khác nhau — và liên tục tách page.

Sai lầm đi kèm khi chẩn đoán: độ trễ tăng, thế là cả team đi viết lại câu SQL. Nội dung query chưa bao
giờ là vấn đề. Vấn đề là có bao nhiêu page phải di chuyển giữa đĩa và bộ nhớ.

## Vì sao môi trường dev che giấu lỗi này

Bảng ở môi trường dev nằm gọn trong buffer pool. Mọi lần đọc đều trúng bộ nhớ, nên số page chẳng tốn
gì và kiểu truy cập không quan trọng — key ngẫu nhiên hay key tuần tự cho hiệu năng như nhau.

Ở production, working set vượt quá RAM. Lúc này số page *chính là* chi phí, và quyết định schema bạn
đưa ra từ mấy tháng trước đã đóng đinh. Query không đổi gì cả; cái thay đổi là tỉ lệ giữa bộ nhớ và dữ
liệu.

## Page, không phải row

Đơn vị I/O là **page** — mặc định 8 KB ở PostgreSQL, 16 KB ở InnoDB. Row nằm bên trong page, và engine
chỉ có thể di chuyển nguyên cả page.

Đọc một row nghĩa là: tìm page đó trong **buffer pool**; nếu không có, đọc nguyên page từ đĩa; rồi đẩy
một page khác ra ngoài để lấy chỗ.

Vậy hai câu hỏi thực sự chi phối hiệu năng là:

1. **Query này cần ít nhất bao nhiêu page để trả lời?**
2. **Các page nóng nằm lại trong bộ nhớ được bao lâu?** (tỉ lệ hit của buffer pool)

Cách nhìn này giải thích lại nhiều quy tắc quen thuộc. `SELECT *` đắt không phải vì số cột mà vì row
rộng hơn thì mỗi page chứa ít row hơn, nên mỗi query cần nhiều page hơn. Covering index nhanh vì nó trả
lời chỉ bằng các page của index. Và quét tuần tự một bảng nhỏ thắng tra index vì đọc ngẫu nhiên từng
page đơn lẻ tốn hơn đọc liền một dải — đó là lý do planner bỏ qua index của bạn khi số row ít là hoàn
toàn đúng, như trong [indexes-and-query-plans](indexes-and-query-plans.md).

**Tỉ lệ hit của buffer pool là metric cần theo dõi.** Tỉ lệ hit giảm nghĩa là working set đã lớn hơn
bộ nhớ, và nó giải thích những đợt tăng độ trễ mà không thay đổi query nào sửa được.

## UUID ngẫu nhiên phá hủy tính cục bộ khi ghi

B-Tree giữ các entry theo thứ tự đã sắp xếp. Điều đó tương tác với việc chọn key theo cách quyết định
throughput ghi:

**Key tuần tự** (bigserial, ULID, UUIDv7, Snowflake) luôn được chèn vào mép phải. Một page nằm nóng
trong bộ nhớ và được lấp đầy rồi mới chuyển sang page kế — ít lần đọc page, ít lần tách, page dày đặc.

**Key ngẫu nhiên** (UUIDv4) được chèn khắp nơi. Mỗi lần ghi nhắm vào một page khác, nên engine phải đọc
một page chưa có trong cache, sửa nó, tách nó khi đầy, rồi ghi lại. Khi dữ liệu đủ lớn, index không còn
vừa bộ nhớ và việc này bị giới hạn bởi đĩa.

**Tách page** là chi phí cộng dồn: một page đầy bị chia đôi, để lại cả hai page trống khoảng một nửa.
Insert ngẫu nhiên tách page liên tục, nên index phình ra — thường gấp đôi kích thước cần thiết — và mật
độ page thấp nghĩa là cùng lượng dữ liệu giờ cần gấp đôi số page, tức là cache hiệu dụng của bạn bị cắt
một nửa.

Nếu cần ID không đoán được, hãy dùng UUID **có thứ tự theo thời gian** (v7) hoặc ULID: đủ ngẫu nhiên
nhìn từ bên ngoài, tăng đơn điệu ở bên trong. Như vậy giữ được tính chất quan trọng. Nếu đã có primary
key ngẫu nhiên với khối lượng lớn, clustering theo một cột tuần tự (InnoDB) hoặc `REINDEX` định kỳ
(PostgreSQL) sẽ lấy lại phần nào mật độ.

## B-Tree và LSM Tree

Hai lời giải cho cùng một bài toán, và thứ được đánh đổi là kiểu ghi với chi phí đọc.

**B-Tree** — cập nhật tại chỗ, có thứ tự, cân bằng.

- Đọc dễ dự đoán: vài bước nhảy page là tới bất kỳ row nào
- Range scan và đọc có thứ tự rất tốt
- Ghi cần **I/O ngẫu nhiên**, và đây là nút thắt khi dữ liệu lớn dần
- Được dùng bởi PostgreSQL, InnoDB, SQL Server, Oracle

**LSM Tree** — không bao giờ cập nhật tại chỗ; ghi nối thêm rồi gộp sau.

- Lần ghi đi vào một bảng trong bộ nhớ, rồi được flush ra các file có thứ tự, bất biến (**SSTable**)
- Mọi lần ghi trở thành **I/O tuần tự**, nhanh hơn rất nhiều
- Một lần đọc có thể phải xem memtable cộng thêm vài tầng SSTable rồi mới biết giá trị hiện tại
- Được dùng bởi RocksDB, Cassandra, ScyllaDB, LevelDB và nhiều store time-series

Sự đảo ngược: B-Tree trả giá lúc ghi để việc đọc rẻ; LSM trả giá lúc đọc để việc ghi rẻ. Không cái nào
tốt hơn — hãy chọn theo workload. Ingest nặng về ghi và time-series hợp với LSM; workload giao dịch với
nhiều point read, scan có thứ tự và update hợp với B-Tree.

Phần lớn ứng dụng nghiệp vụ nên dùng engine B-Tree. Lỗi cần bắt khi review không phải là chọn cái này
thay cái kia, mà là chọn khi không biết tỉ lệ đọc/ghi của workload.

## Ba loại khuếch đại

Gọi được tên chúng thì mới bàn được về các đánh đổi ở tầng storage:

- **Write amplification** — số byte thực sự được ghi trên mỗi byte logic. B-Tree ghi lại nguyên page
  (cả WAL và full-page image); LSM ghi lại dữ liệu nhiều lần trong quá trình compaction. Đây là thứ làm
  mòn SSD và làm đĩa bão hòa.
- **Read amplification** — số page đọc trên mỗi lần đọc logic. Đây là chi phí đặc trưng của LSM: mỗi
  lần tra phải xem nhiều tầng, được giảm nhẹ bằng Bloom filter, thứ loại trừ với chi phí thấp những file
  chắc chắn không chứa key.
- **Space amplification** — số byte lưu trữ trên mỗi byte logic. Dead tuple chờ vacuum, page trống một
  nửa sau khi tách, entry SSTable đã bị thay thế đang chờ compaction.

Bạn không thể giảm cả ba xuống mức thấp nhất. Mọi quyết định về storage cải thiện cái này và làm xấu đi
cái khác.

## Compaction và vacuum

Không engine nào xóa tại chỗ, nên cả hai đều tích tụ rác và cả hai đều cần một bộ dọn chạy nền.

**PostgreSQL — dead tuple và `VACUUM`.** MVCC nghĩa là một lệnh `UPDATE` ghi một phiên bản row mới và
đánh dấu bản cũ là chết; `DELETE` chỉ đánh dấu. `VACUUM` thu hồi phần chỗ đó để dùng lại.

Ràng buộc then chốt của nó: **vacuum không thể xóa một phiên bản mà bất kỳ transaction nào đang mở vẫn
còn nhìn thấy.** Chỉ một connection chạy lâu hoặc idle-in-transaction là chặn việc dọn dẹp **trên toàn
bộ database** — bảng phình ra, page thưa đi, scan chậm lại, và không có lock contention nào xuất hiện để
giải thích. Xem [lock-contention](lock-contention.md). Theo dõi tỉ lệ dead tuple và độ trễ của
autovacuum; khi autovacuum không theo kịp lượng ghi, bloat tăng mãi không giảm.

**LSM — compaction.** Gộp các SSTable để bỏ các giá trị đã bị thay thế và giữ read amplification trong
giới hạn. Việc này ngốn I/O và tranh tài nguyên với traffic chính, nên độ trễ tăng vọt trong lúc
compaction là bình thường và phải được lên kế hoạch từ trước, chứ không phải đi debug như một bất
thường.

Bài học chung: **chỗ trống được thu hồi bất đồng bộ, và bộ dọn có thể bị tụt lại.** Một tầng storage
không giám sát tiến trình dọn dẹp của nó sẽ xuống cấp âm thầm.

## Checklist review

- [ ] Primary key trên bảng lớn có thứ tự theo thời gian (bigserial, UUIDv7, ULID), không phải UUIDv4 ngẫu nhiên
- [ ] Bảng dùng key ngẫu nhiên với khối lượng lớn có biện pháp giảm thiểu (clustering, reindex định kỳ)
- [ ] Tỉ lệ hit của buffer pool được giám sát; khi giảm thì được hiểu là working set so với bộ nhớ
- [ ] `SELECT *` rộng trên đường nóng có lý do chính đáng — nó tốn page, không chỉ tốn cột
- [ ] Các luồng đọc nóng nhất dùng covering index
- [ ] Lựa chọn storage engine khớp với tỉ lệ đọc/ghi của workload
- [ ] Tỉ lệ dead tuple và độ trễ của autovacuum được giám sát (PostgreSQL)
- [ ] Không có session chạy lâu hoặc idle-in-transaction nào chặn vacuum
- [ ] Đã đặt `idle_in_transaction_session_timeout`
- [ ] Với store LSM, độ trễ tăng vọt lúc compaction là điều được lường trước và đã tính vào capacity
- [ ] Index bloat được đo, không mặc định là không có

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Database Internals của TechCraft, các
phần P1, P16 và P17 ([collection](https://www.patreon.com/collection/2115129)). Cách nhìn "page, không
phải row", ý "UUID là kẻ phá hủy các page của index", và sự đảo ngược ghi-so-với-đọc giữa B-Tree/LSM là
của TechCraft; cấu trúc review và checklist là của repository này.*
