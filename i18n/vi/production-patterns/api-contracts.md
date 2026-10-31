---
source_sha256: 76e013386366f3921fe4b66660f03589aa974e8f8d7306fd6acd8050a0bd3a98
---

# Hợp đồng API & Versioning

**Quy tắc: một API không phải là một endpoint, nó là một lời hứa bạn không thể rút lại.** Code nội
bộ có thể viết lại trong một đêm; một contract đã công bố thì không, vì hệ thống của người khác đang
phụ thuộc vào nó.

Thuộc nhóm thiết kế API: **hợp đồng & versioning** (trang này) ·
[mô hình hóa resource](api-resource-modeling.md) · [endpoint danh sách](api-list-endpoints.md) ·
[thao tác bất đồng bộ](api-async-operations.md) · [webhook provider](webhook-provider-design.md) ·
[rate limiting](rate-limiting.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Contract chính là sản phẩm](#the-contract-is-the-product)
- [Contract-first](#contract-first)
- [Thế nào là breaking change](#what-counts-as-breaking)
- [Ba kiểu phá vỡ lọt qua mọi lần kiểm tra schema](#three-breakages-that-pass-every-schema-check)
- [Versioning](#versioning)
- [Deprecation là một quy trình](#deprecation-is-a-process)
- [Governance, khi kỹ sư giỏi thôi là chưa đủ](#governance-when-good-engineers-are-not-enough)
- [Checklist review](#review-checklist)

## Anti-pattern

```diff
  {
    "id": "ord_123",
-   "user_id": "usr_9",
+   "customer_id": "usr_9",     // "rõ nghĩa hơn, tránh nhầm với các ID khác"
    "total": 1500
  }
```

Integration test nội bộ vẫn pass — chúng được sinh lại từ schema mới. Ba mươi phút sau khi deploy,
toàn bộ module thanh toán của một đối tác ngừng hoạt động, vì parser của họ đọc `user_id` và giờ
nhận về `null`.

*Lập luận* thì không có gì sai. `customer_id` đúng là rõ nghĩa hơn. Sai lầm nằm ở chỗ tin rằng tên
một field vẫn là chi tiết triển khai sau khi nó đã được công bố.

## Vì sao môi trường dev che giấu lỗi này

Ở môi trường dev bạn kiểm soát mọi consumer, nên đổi tên chỉ là một lần find-and-replace trên toàn
project, và các test bạn sở hữu là những test duy nhất tồn tại.

Ở production, consumer là **hệ thống của người khác**, chạy theo chu kỳ release của họ; có những hệ
thống bạn không liệt kê được, và không hệ thống nào bạn deploy được. Chính đặc tính khiến việc thay
đổi API nguy hiểm — những consumer bạn không biết và không thể cập nhật — lại hoàn toàn vắng mặt ở
local.

## Contract chính là sản phẩm

> Một API không phải là một endpoint. Một API là một lời hứa: *tôi sẽ cung cấp dữ liệu này, theo
> hình dạng này, và giữ nó ổn định, để hệ thống của bạn có thể dựa vào.*

Consumer không đọc sơ đồ kiến trúc của bạn, không biết bạn dùng framework gì, và không quan tâm
schema của bạn. Contract là toàn bộ bề mặt mà họ tiếp xúc, và là thứ duy nhất họ có thể xây dựng lên
trên.

Điều đó đảo ngược thứ tự ưu tiên quen thuộc. Chất lượng code nội bộ có thể cứu vãn — refactor rồi
đi tiếp. Một sai lầm trong contract thì tồn tại vĩnh viễn chừng nào còn consumer dùng nó, mà trên
thực tế nghĩa là nhiều năm.

Viết một API thì dễ. **Viết một API mà ba năm sau bạn vẫn còn hỗ trợ được** mới là kỹ năng thật sự,
và điều đó có nghĩa contract phải trả lời được những câu hỏi chỉ xuất hiện về sau: consumer phân
trang thế nào khi dữ liệu tăng, filter ra sao mà chi phí không vô hạn, retry an toàn bằng cách nào,
biết về lỗi qua đâu, và migrate thế nào khi bạn thay đổi.

## Contract-first

Failure mode ở đây là thiết kế API như phần đuôi của database:

```
tables  →  ORM models  →  serializers  →  endpoints
```

Kết quả là các quyết định về lưu trữ của bạn bị phơi ra thành cam kết công khai. Sau này chuẩn hóa
lại một bảng, hình dạng API cũng đổi theo — tức là một breaking change gây ra bởi một lần refactor
nội bộ, đúng kiểu coupling mà contract lẽ ra phải ngăn chặn.

Contract-first đảo ngược điều đó: **thống nhất contract trước, rồi mới xây phía sau nó.** Những hệ
quả thực tế đáng kiểm tra khi review:

- Schema (OpenAPI, protobuf, GraphQL SDL) được viết và review **trước** khi triển khai, và là thứ
  mà consumer và các team tranh luận với nhau.
- Có thể sinh client và dựng mock server ngay lập tức, nên frontend, mobile và đối tác làm việc song
  song thay vì phải chờ.
- Phần lưu trữ được tự do thay đổi, vì không có gì trong contract phản chiếu nó.
- Contract được kiểm tra trong CI: một diff phá vỡ nó sẽ làm build fail, thay vì để đối tác phát
  hiện ra.

Contract-first cũng là một cuộc thương lượng giữa những người có ưu tiên khác nhau — backend lead
muốn một model sạch, DBA muốn lưu trữ hiệu quả, product owner muốn có nó vào tuần sau. Có một văn
bản duy nhất để mọi người bất đồng xoay quanh chính là mục đích.

## Thế nào là breaking change

Sự phân biệt mà consumer thực sự cảm nhận được:

**An toàn (bổ sung thêm):**

- Thêm một field response mới, không bắt buộc
- Thêm một tham số request mới, không bắt buộc, có giá trị mặc định
- Thêm một endpoint mới
- Thêm một giá trị vào enum, **chỉ khi** tài liệu đã nêu rõ consumer phải chấp nhận giá trị lạ

**Breaking:**

- Đổi tên hoặc xóa bất kỳ field nào
- Đổi kiểu dữ liệu (`"1500"` → `1500`, ID kiểu integer → string)
- Biến một field request không bắt buộc thành bắt buộc
- Siết chặt validation đối với input trước đây được chấp nhận
- Thay đổi thứ tự sắp xếp mặc định, kích thước trang, hoặc filter mặc định
- Thay đổi mã lỗi, hoặc hình dạng của error response
- Thay đổi múi giờ, độ chính xác, hoặc đơn vị của một field đã có

Ba mục cuối là những thứ các team hay bỏ sót, vì không mục nào làm thay đổi schema. **Hành vi là
một phần của contract**, không chỉ cấu trúc. Một consumer dựa vào thứ tự mặc định — xem
[endpoint danh sách](api-list-endpoints.md) — sẽ vỡ khi bạn "cải tiến" nó, và không schema diff nào
bắt được điều đó.

## Ba kiểu phá vỡ lọt qua mọi lần kiểm tra schema

Cả ba đều ship với CI xanh: schema diff chỉ là bổ sung thêm, hoặc kiểu dữ liệu vẫn là "cùng một con
số", CI im lặng, vậy mà consumer vẫn vỡ.

**Enum mở.** Thêm một giá trị enum nằm trong danh sách an toàn ở trên kèm một điều kiện, và chính
điều kiện đó là toàn bộ lỗi. Một consumer so khớp đầy đủ các giá trị nó biết, không có nhánh
default, sẽ ném lỗi ngay lần đầu gặp giá trị mới. Nạn nhân thường là client kiểu tĩnh — một decoder
Swift hay Kotlin được sinh tự động sẽ từ chối case lạ ngay ở bước *parse*, nên crash xảy ra trước
cả phần business logic lẽ ra có thể xoay xở được. Enum dạng số còn tệ hơn: `3` không gợi ý gì về ý
nghĩa của nó, nên ngay cả một client muốn xuống cấp nhẹ nhàng cũng không có gì để bám vào. Hãy dùng
giá trị string, định nghĩa giá trị dự phòng (`"UNKNOWN"`) trong contract ngay từ ngày đầu, và coi
"consumer chấp nhận giá trị lạ" là điều bạn đã kiểm chứng trong code của họ, chứ không phải giả định
trong code của bạn.

**Một số nguyên vừa vặn ở mọi nơi trừ trình duyệt.** Mở rộng ID từ string sang integer 64-bit là
một lợi thế ở tầng lưu trữ nhưng không sống sót qua JSON. JSON không có kiểu integer — chỉ có
`number`, mà mọi trình duyệt parse thành số double IEEE-754 với mantissa 53 bit:

```
server sends    9007199254740993      int64, exact
JSON number     9.007199254740992e15  double, 53-bit mantissa — low bit gone
browser reads   9007199254740992      a different ID, no error raised
```

Vượt quá 2^53−1 (`9007199254740991`), độ chính xác bị mất một cách âm thầm. Không có gì ném lỗi;
giá trị đơn giản là sai, và sai ở một field dùng để tra cứu, nên thiệt hại là một lệnh ghi nhắm nhầm
đối tượng chứ không phải một lệnh ghi thất bại. Client mobile native có int64 thật thì không thấy gì
bất thường, đó là lý do lỗi này lên tới production với lý lẽ "lúc test vẫn chạy". Hãy serialize ID
lớn, tiền và bộ đếm dưới dạng string, và đừng bao giờ đổi kiểu JSON của một field đã công bố.

**Khóa auto-increment làm lộ tình hình kinh doanh.** Một surrogate key tăng dần được dùng làm định
danh công khai sẽ cho bất kỳ ai tạo hai tài khoản cách nhau một ngày biết chính xác bạn đã tạo bao
nhiêu tài khoản ở giữa — số đơn hàng, tốc độ tăng người dùng, vòng quay tồn kho, tất cả đọc được
bằng một phép trừ. Nó cũng khiến việc dò lần lượt trở nên dễ dàng, biến bất kỳ chỗ kiểm tra phân
quyền yếu nào thành một cửa trích xuất dữ liệu hàng loạt. Đây không phải vấn đề schema và không
contract test nào bắt được. Hãy công khai một định danh không đoán được (UUIDv7, hashid) và giữ khóa
tăng dần ở đúng chỗ của nó, bên trong database.

## Versioning

Versioning không phải là "thêm `/v2`". Đó là việc chọn ai sẽ gánh chi phí của thay đổi.

| Chiến lược                                                | Ghi chú                                                                                              |
|-----------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| **URL path** (`/v2/orders`)                               | Dễ thấy nhất và thân thiện với cache; dễ dẫn tới version cho cả API, vốn tốn kém để duy trì          |
| **Header** (`Accept: application/vnd.api+json;version=2`) | URL gọn gàng; client dễ quên gửi, và khó debug hơn                                                   |
| **Theo field / chỉ bổ sung**                              | Không có version nào — chỉ thay đổi bổ sung, mãi mãi. Chi phí cho consumer thấp nhất, đòi hỏi kỷ luật thiết kế cao nhất |

**Chỉ bổ sung là mục tiêu**; versioning là thứ bạn làm khi không đạt được mục tiêu đó. Mỗi version
còn chạy là một nhánh bạn phải hỗ trợ, test và bảo mật, và các version không tự nghỉ hưu — đó là lý
do câu "cứ nâng lên v3 là xong" thường là lời thừa nhận một chi phí vĩnh viễn.

Hai quy tắc ngăn được phần lớn rắc rối khi versioning: **đừng bao giờ version cả API khi chỉ một
resource thay đổi** (hãy version resource đó), và **đừng bao giờ để một model nội bộ dùng chung
serialize cho hai version** — đó là cách một bản sửa cho v1 âm thầm làm thay đổi v2.

## Deprecation là một quy trình

Thông báo sẽ gỡ bỏ không phải là deprecation. Một trình tự khả thi:

1. **Thông báo** kèm ngày cụ thể, trong changelog, tài liệu, và gửi trực tiếp tới các consumer đã
   xác định được.
2. **Đo lường** — log mức sử dụng field hoặc endpoint bị deprecate theo từng consumer. Bạn không thể
   khai tử thứ bạn không đo được, và dữ liệu sử dụng là thứ biến một cuộc tranh cãi thành một kế
   hoạch.
3. **Báo hiệu ngay trong response** — header `Deprecation` và `Sunset`, để client thấy được mà
   không cần đọc tài liệu.
4. **Liên hệ những người dùng còn lại** đã xác định ở bước 2.
5. **Brownout** — trả lỗi trong những khung giờ ngắn có lên lịch trước khi gỡ hẳn, để các phụ thuộc
   ngầm lộ ra trong lúc có người đang theo dõi.
6. **Gỡ bỏ**, với sẵn một đường rollback.

Không có bước 2 thì bạn đang đoán, và việc gỡ bỏ sẽ trở thành một sự cố.

## Governance, khi kỹ sư giỏi thôi là chưa đủ

> Người giỏi không cứu được bạn khi có tới 10.000 endpoint.

Năng lực cá nhân tạo ra những API xuất sắc ở từng chỗ nhưng không nhất quán với nhau: ba định dạng
ngày tháng, bốn kiểu phân trang, năm hình dạng lỗi, hai cơ chế auth. Mỗi chỗ không nhất quán là một
khoản thuế vĩnh viễn đánh lên mọi consumer.

Mục đích của governance không phải là gò bó thiết kế mà là đưa ra các quyết định nhàm chán **một
lần duy nhất**: hình dạng lỗi, kiểu phân trang, cách biểu diễn ngày tháng và tiền, quy ước đặt tên,
auth, chính sách versioning, và thế nào là breaking change.

Hãy làm cho nó rẻ và tự động — một linter trong CI, một thư viện schema dùng chung, client được sinh
tự động — vì governance dựa vào các buổi họp review sẽ mục ruỗng ngay khi áp lực giao hàng ập tới.
Và giữ một lối thoát có người chịu trách nhiệm rõ ràng: một tiêu chuẩn mà không ai được phép lệch
khỏi, kể cả khi có lý do chính đáng, sẽ bị lách thay vì được sửa đổi.

## Checklist review

- [ ] Contract được thiết kế và review trước khi triển khai, không suy ra từ các bảng
- [ ] Hình dạng response không phản chiếu schema database
- [ ] Schema được kiểm tra trong CI; diff mang tính breaking làm build fail
- [ ] Không đổi tên field, đổi kiểu, hay thêm field request bắt buộc trên một API đã công bố
- [ ] Thay đổi về hành vi (sắp xếp mặc định, kích thước trang, mã lỗi, đơn vị) được coi là breaking
- [ ] Thay đổi chỉ bổ sung là mặc định; một version mới phải có lý do, không phải chuyện thường lệ
- [ ] Version được giới hạn theo resource, không áp cho cả API
- [ ] Không có model nội bộ dùng chung nào serialize cho hai version API
- [ ] Field và endpoint bị deprecate có đo lường mức sử dụng theo từng consumer
- [ ] Deprecation có công bố ngày và báo hiệu ngay trong response (`Deprecation` / `Sunset`)
- [ ] Có quy ước liên team cho lỗi, phân trang, ngày tháng, tiền và auth
- [ ] Quy ước được ép buộc bằng công cụ, không dựa vào trí nhớ của người review
- [ ] Giá trị enum là string, và contract định nghĩa giá trị dự phòng cho trường hợp không xác định
- [ ] Việc consumer chấp nhận giá trị enum lạ đã được kiểm chứng, không phải giả định
- [ ] Không field integer nào có thể vượt quá 2^53−1; ID lớn, tiền và bộ đếm được serialize dạng string
- [ ] Định danh công khai không đoán được; khóa tăng dần của database không bao giờ bị lộ ra ngoài

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series API Design Patterns của TechCraft,
phần P1–P2 và P13–P15 ([collection](https://www.patreon.com/techcraft_official)). Cách nhìn "API là
một lời hứa, không phải một endpoint", lập luận contract-first chống lại thiết kế dẫn dắt bởi
database, và câu "người giỏi không cứu được bạn khi có tới 10.000 endpoint" là của TechCraft, cũng
như ba câu chuyện thực tế đằng sau "Ba kiểu phá vỡ lọt qua mọi lần kiểm tra schema" — enum mở, mất
độ chính xác 53 bit, và khóa auto-increment bị lộ. Phân loại breaking change, trình tự deprecation,
cơ chế và cách khắc phục viết cho từng trường hợp trong ba trường hợp đó, cùng checklist là của
repository này.*
