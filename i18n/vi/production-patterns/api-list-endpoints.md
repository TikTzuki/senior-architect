---
source_sha256: 9160e4b7b2514d56b724854c37ec916b94e7b0014501f16a14abd0d934848552
---

# Endpoint danh sách: Phân trang, Filter & Sắp xếp

**Quy tắc: một endpoint danh sách trước hết là cơ chế kiểm soát chi phí, sau đó mới là tính năng.**
`LIMIT` không giới hạn, filter tùy ý và sắp xếp tùy ý — mỗi thứ đều trao cho client khả năng đánh
sập database của bạn.

Thuộc nhóm thiết kế API: [hợp đồng & versioning](api-contracts.md) ·
[mô hình hóa resource](api-resource-modeling.md) · **endpoint danh sách** (trang này) ·
[thao tác bất đồng bộ](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Phân trang là contract, không phải tiện ích](#pagination-is-a-contract-not-a-convenience)
- [Offset bị trôi và ngày càng chậm](#offset-drifts-and-gets-slower)
- [Keyset pagination](#keyset-pagination)
- [Sắp xếp là một lời hứa về database](#sorting-is-a-promise-about-the-database)
- [Filter và tìm kiếm](#filtering-and-search)
- [Contract phải nêu rõ những gì](#what-the-contract-must-state)
- [Checklist review](#review-checklist)

## Anti-pattern

```python
@app.get("/orders")
def list_orders(sort_by: str = "created_at", order: str = "desc",
                limit: int = None, **filters):
    q = f"SELECT * FROM orders WHERE 1=1"
    for k, v in filters.items():
        q += f" AND {k} = '{v}'"                    # cột tùy ý
    q += f" ORDER BY {sort_by} {order}"             # sắp xếp tùy ý
    if limit: q += f" LIMIT {limit}"                # limit không bắt buộc
    return db.query(q)
```

Tạm bỏ qua lỗi injection — vấn đề nằm ở *thiết kế*. Client có thể yêu cầu mọi đơn hàng từng được
tạo, sắp xếp theo một cột không có index, filter theo một field không có index. Một lời gọi biến
thành một lần full table scan cộng thêm một lần sắp xếp trên đĩa.

> Business logic không sai một dòng nào. Về mặt kiến trúc, hệ thống chết vì chính sự thành công của
> nó.

Với 500 dòng, endpoint này rất tuyệt. Với 50 triệu dòng, nó là một lỗ hổng từ chối dịch vụ do chính
bạn ship ra và ghi vào tài liệu như một tính năng.

## Vì sao môi trường dev che giấu lỗi này

Bảng ở dev chỉ có vài trăm dòng. Mọi lần scan đều tức thì, mọi phép sắp xếp đều nằm gọn trong bộ
nhớ, `SELECT *` không limit trả về một payload nhỏ, và mọi tổ hợp filter đều nhanh.

**Từng đặc tính đó đều phụ thuộc vào số dòng**, và không đặc tính nào được test. Endpoint không chỉ
đơn giản là chậm đi về sau — về sau nó là một endpoint khác hẳn, vì query plan thay đổi theo dữ liệu.
Xem [indexes-and-query-plans](indexes-and-query-plans.md).

## Phân trang là contract, không phải tiện ích

Phân trang làm ba việc, trong đó chỉ một việc là người dùng nhìn thấy:

1. **Giới hạn chi phí query** — database chỉ đọc và sắp xếp một tập có giới hạn.
2. **Giới hạn kích thước response** — bộ nhớ, serialization và truyền tải đều có giới hạn. Xem
   [memory-and-gc](memory-and-gc.md).
3. Cho phép consumer duyệt qua dữ liệu lớn một cách có thể đoán trước.

Vì vậy một endpoint danh sách không có kích thước trang **tối đa do server ép buộc** thì không giới
hạn ngay từ thiết kế:

```python
DEFAULT_LIMIT, MAX_LIMIT = 25, 100
limit = min(int(request.args.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
```

Hãy kẹp giá trị lại thay vì trả lỗi, và ghi việc kẹp đó vào tài liệu. Lưu ý rằng cả giá trị mặc định
lẫn giá trị tối đa đều là một phần của contract — đổi chúng về sau là một breaking change về hành vi,
theo [api-contracts](api-contracts.md).

## Offset bị trôi và ngày càng chậm

`LIMIT 20 OFFSET 100000` có hai khiếm khuyết độc lập.

**Càng sâu càng chậm.** Database phải sinh ra rồi bỏ đi 100.000 dòng để trả về 20 dòng. Chi phí tăng
tuyến tính theo số trang, nên trang cuối của một danh sách lớn là request đắt nhất trong hệ thống
của bạn.

**Bị trôi khi dữ liệu thay đổi.** Offset trỏ tới *một vị trí trong tập kết quả*, không phải một chỗ
trong dữ liệu:

```
page 1  →  rows 1–20 of newest-first
           a new row is inserted
page 2  →  OFFSET 20  →  the row previously #20 is now #21 → returned twice
```

Một client duyệt qua 10.000 bản ghi trong lúc có ghi đồng thời sẽ thấy bản ghi trùng và bỏ sót bản
ghi — một cách âm thầm. Với UI thì đó là chuyện khó chịu; với một job đối soát hay một lần export thì
đó là lỗi về tính đúng đắn, cho ra tổng số sai mà không báo lỗi gì. Xem
[payment-reconciliation](payment-reconciliation.md).

Offset chấp nhận được cho những danh sách nhỏ, có giới hạn, do người duyệt, nơi việc nhảy tới trang
7 có ý nghĩa. Nó sai cho bất cứ thứ gì do máy tiêu thụ.

## Keyset pagination

Phân trang theo *chỗ bạn đã đi tới*, không theo số dòng bạn đã bỏ qua:

```sql
SELECT * FROM orders
WHERE (created_at, id) < (:last_created_at, :last_id)   -- so sánh chặt, có tiebreaker
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

```json
{ "data": [...], "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNi0wMy0xNVQxMDozMDowMFoiLCJpZCI6MTIzfQ" }
```

Chi phí không đổi ở mọi độ sâu (nó seek trên index), và ổn định khi có ghi đồng thời — một dòng mới
chèn vào không làm dịch ranh giới.

Ba chi tiết triển khai, bỏ sót cái nào cũng thành bug:

- **Khóa sắp xếp phải là duy nhất**, hoặc dùng khóa ghép có tiebreaker (`created_at, id`). Một
  cursor không duy nhất sẽ hoặc bỏ sót, hoặc lặp lại dòng ở ranh giới trang.
- **Cursor không trong suốt.** Mã hóa vị trí; đừng ghi tài liệu về cấu trúc của nó. Một client tự
  parse cursor của bạn đã tự trói mình vào cách sắp xếp bạn đang cài đặt, và giờ bạn không thể thay
  đổi nó nữa.
- **Khớp với index.** `ORDER BY created_at DESC, id DESC` cần một index theo đúng thứ tự đó — nếu
  không, keyset pagination sẽ là một lần sắp xếp cả bảng cho mỗi trang.

## Sắp xếp là một lời hứa về database

Mỗi field cho phép sắp xếp là một cam kết sẽ sắp xếp hiệu quả một bảng không ngừng lớn lên. Không có
index phù hợp, database sắp xếp trong bộ nhớ cho tới khi vượt quá sort buffer, rồi **tràn xuống
đĩa** — và sắp xếp trên đĩa với một bảng lớn là kiểu alert kinh điển lúc 2 giờ sáng.

Vậy nên: **lập allowlist các field được sắp xếp, và đánh index cho từng field.**

```python
SORTABLE = {"created_at", "updated_at", "total"}     # mỗi field đều có index
if sort_by not in SORTABLE:
    raise BadRequest(f"sort_by must be one of {sorted(SORTABLE)}")
```

Thêm hai điểm hay khiến người ta vấp. **Luôn thêm một tiebreaker duy nhất vào cuối** — sắp xếp theo
một cột không duy nhất cho ra thứ tự không ổn định, nên các dòng bằng nhau có thể xuất hiện ở vị trí
khác nhau giữa các trang, và một dòng bị lặp hoặc bị bỏ sót kể cả khi dùng keyset pagination. Và
**thứ tự sắp xếp mặc định là một phần của contract**: consumer dựa vào nó dù bạn có ghi tài liệu hay
không, nên "cải tiến" nó là một breaking change.

## Filter và tìm kiếm

> API tìm kiếm là endpoint nguy hiểm thứ hai ở production.

Mối nguy đến từ tổ hợp. Mười filter không bắt buộc tạo ra hơn một nghìn hình dạng query, và bạn
không thể đánh index cho tất cả — nên sẽ có tổ hợp nào đó là table scan, và bạn sẽ không biết là tổ
hợp nào cho tới khi một client tìm ra nó.

Cần làm gì:

- **Lập allowlist các field được filter**, và nắm rõ plan cho từng field. Đừng bao giờ dựng điều kiện
  từ các key tùy ý của client.
- **Giới hạn không gian tổ hợp** — bắt buộc ít nhất một filter có tính chọn lọc (một tenant, một
  khoảng ngày, một chủ sở hữu) để không query nào là không giới hạn.
- **Giới hạn khoảng.** Một filter ngày không có biên là một lần full scan; bắt buộc có khoảng và giới
  hạn độ rộng của nó.
- **Đừng cung cấp tìm kiếm chuỗi con trên một bảng lớn trong database chính.** `LIKE '%x%'` không
  dùng được B-tree — xem [indexes-and-query-plans](indexes-and-query-plans.md). Hãy dùng trigram
  index, một search engine thực thụ, hoặc đừng cung cấp tính năng đó.
- **Đặt `statement_timeout`** cho các endpoint danh sách, để một tổ hợp tồi fail nhanh thay vì
  chiếm giữ một connection. Xem [connection-pools-and-latency](connection-pools-and-latency.md).

Lời hứa đáng để hướng tới khi thiết kế: *dù dữ liệu lớn tới đâu và filter kết hợp thế nào, endpoint
này vẫn trả lời trong ngân sách của nó.* Nếu bạn không hứa được điều đó với một dạng query, đừng phơi
dạng query đó ra — thay vào đó hãy cung cấp nó dưới dạng một
[thao tác bất đồng bộ](api-async-operations.md).

## Contract phải nêu rõ những gì

Consumer phụ thuộc vào tất cả những điều sau, dù bạn có viết ra hay không:

- Kích thước trang mặc định và tối đa
- Kiểu phân trang, và cursor có ổn định giữa các trang hay không
- Thứ tự sắp xếp mặc định, và chính xác tập field được sắp xếp
- Chính xác tập field được filter và ngữ nghĩa của chúng (khớp chính xác, khoảng, tiền tố)
- Tổng số là chính xác, xấp xỉ, hay không có — **một `total_count` chính xác trên một bảng lớn đã
  filter là một query đắt thứ hai**, và thường là phần tốn kém nhất của response
- Kết quả có thể bị dịch chuyển giữa các trang khi có ghi đồng thời hay không

## Checklist review

- [ ] Mọi endpoint danh sách đều có kích thước trang tối đa do server ép buộc
- [ ] Danh sách do máy tiêu thụ dùng keyset pagination, không dùng offset
- [ ] Cursor không trong suốt và có chứa tiebreaker duy nhất
- [ ] ORDER BY của phân trang khớp với một index đã có
- [ ] Field được sắp xếp nằm trong allowlist và mỗi field đều có index
- [ ] Phép sắp xếp có tiebreaker duy nhất để thứ tự ổn định
- [ ] Thứ tự sắp xếp mặc định được ghi tài liệu và coi là một phần của contract
- [ ] Field được filter nằm trong allowlist; không điều kiện nào dựng từ key tùy ý của client
- [ ] Bắt buộc ít nhất một filter có tính chọn lọc; khoảng không có biên bị giới hạn
- [ ] Không tìm kiếm chuỗi con trên một bảng lớn của database chính
- [ ] `statement_timeout` giới hạn các query danh sách
- [ ] `total_count` chỉ được trả về khi chi phí của nó xứng đáng, và độ chính xác được ghi tài liệu

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P4–P6 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn phân trang như một
cơ chế kiểm soát chi phí, câu "hệ thống chết vì chính sự thành công của nó", "tìm kiếm là endpoint
nguy hiểm thứ hai", và phần phân tích sort buffer / external merge sort là của TechCraft; các chi
tiết về keyset và checklist là của repository này.*
