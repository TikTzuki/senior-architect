---
source_sha256: 116e18b95960154c8ea269a3c195d108575d9bc86073f2239c9794fa245f306a
---

# Event Sourcing, Snapshot & CQRS

**Quy tắc: lưu trạng thái trả lời câu "bây giờ nó là gì"; lưu event trả lời câu "nó đã đi đến đây
bằng cách nào".** Chọn event là nhận về một nghĩa vụ vĩnh viễn — snapshot, projection, và schema có
version — nên chỉ chọn khi có lý do.

Thuộc nhóm data modeling: [lưu giữ & lịch sử](data-retention-and-history.md) ·
[dữ liệu thời gian](temporal-data.md) · **event sourcing & CQRS** (trang này) ·
[replay event & rebuild](event-replay-and-projection-rebuild.md) ·
[tiến hóa schema event](event-schema-evolution.md) ·
[ngữ nghĩa phân phối message](message-delivery-semantics.md)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Tư duy trạng thái đánh mất lý do](#state-thinking-loses-the-reason)
- [Event sourcing](#event-sourcing)
- [Khoản nợ ẩn: chi phí replay](#the-hidden-debt-replay-cost)
- [Snapshot](#snapshots)
- [CQRS](#cqrs)
- [Materialized view không phải cache](#materialized-views-are-not-caches)
- [Chuyển đổi từ CRUD](#migrating-from-crud)
- [Khi nào không nên làm](#when-not-to-do-this)
- [Checklist review](#review-checklist)

## Anti-pattern

```sql
UPDATE accounts SET balance = 500 WHERE id = :id;
```

Dòng dữ liệu đúng, nhưng lý do thì đã mất. Đó là nạp tiền, hoàn tiền, nhân viên hỗ trợ sửa tay,
đảo phí, hay thu hồi do gian lận? Là một lần biến động hay bốn lần? Con số còn đó; câu chuyện thì
không.

Anti-pattern ngược lại cũng phổ biến không kém, ở những team vừa đọc xong một bài viết về event
sourcing:

```python
# Mỗi lần đọc đều dựng lại từ thuở ban đầu.
def get_balance(account_id):
    events = event_store.load_all(account_id)      # 400.000 event sau hai năm
    return reduce(apply_event, events, Account())  # 3 giây, cho mỗi request
```

Đúng, audit được, và không dùng nổi. Đây chính là failure mode khiến các team bỏ event sourcing và
kết luận rằng nó không chạy được trên production.

## Vì sao môi trường dev che giấu lỗi này

Với tư duy trạng thái, việc thiếu "vì sao" là vô hình cho tới khi có người hỏi — thường là bộ phận
rủi ro, hỗ trợ, hoặc kiểm toán viên vài tháng sau, và lúc đó lý do chưa từng được ghi lại.

Với event sourcing, chi phí replay là một hàm của **số lượng event**, mà ở môi trường dev chỉ có vài
chục. Một projection dựng lại từ 50 event thì tức thì; cùng đoạn code đó chạy trên hai năm lịch sử
production thì timeout. Thiết kế trông như đã được kiểm chứng, trong khi thứ duy nhất được test là
một tập dữ liệu nhỏ.

## Tư duy trạng thái đánh mất lý do

Một dòng CRUD là bức ảnh chụp hiện tại. Các câu hỏi nghiệp vụ hiếm khi nói về hiện tại:

- Tại sao số dư này lại là con số này?
- Chuỗi hành động nào đã tạo ra trạng thái này?
- Replay tháng vừa rồi với quy tắc tính phí đã sửa — lẽ ra số dư phải là bao nhiêu?
- Nhân viên hỗ trợ nói họ chưa bao giờ cộng khoản credit đó. Vậy ai đã làm?

Một [audit log](data-retention-and-history.md) trả lời được *ai và khi nào*. Chỉ có event mới làm
cho trạng thái **suy ra được** — trạng thái thôi là sự thật, và trở thành hệ quả được tính ra từ sự
thật.

## Event sourcing

Lưu chuỗi bất biến những gì đã xảy ra; suy ra trạng thái bằng cách fold qua chuỗi đó.

```
AccountOpened{id, owner}
MoneyDeposited{amount: 300, ref: "txn_1"}
FeeCharged{amount: 5, rule: "monthly_v2"}
MoneyWithdrawn{amount: 100, ref: "txn_2"}
                    ↓ fold
              balance = 195
```

Những thuộc tính làm cho một event store đáng tin:

- **Event là sự việc ở thì quá khứ.** `MoneyDeposited`, không phải `DepositMoney`. Một command có
  thể bị từ chối; một event thì đã xảy ra rồi.
- **Append-only, không bao giờ bị sửa.** Thu hồi `UPDATE`/`DELETE` ở mức database. Một sai sót được
  sửa bằng một event bù trừ mới, đúng như trong [payment-ledger](payment-ledger.md). Dữ liệu cá nhân
  cần có phương án trước khi event đầu tiên được ghi, vì sau này không thể xóa — xem
  [xóa dữ liệu khi lịch sử là append-only](data-retention-and-history.md#erasure-when-history-is-append-only).
- **Có thứ tự trong từng aggregate**, với một số thứ tự tăng đơn điệu. Thứ tự giữa các aggregate
  thường không được đảm bảo và không nên giả định.
- **Optimistic concurrency khi append** — ghi với kỳ vọng đang ở version N, thất bại nếu một writer
  khác đã ghi trước. Xem [locking](optimistic-vs-pessimistic-locking.md).
- **Schema có version.** Event tồn tại mãi mãi, nên event `V1` từ năm năm trước vẫn phải đọc được
  bằng code hôm nay. Nghĩa vụ này không bao giờ mất đi và là chi phí bị đánh giá thấp nhất — xem
  [tiến hóa schema event](event-schema-evolution.md).

## Khoản nợ ẩn: chi phí replay

Event sourcing thường được quảng cáo là lịch sử miễn phí. Hóa đơn đến dưới dạng replay.

Chi phí rebuild tăng tuyến tính và không có giới hạn theo số event. Một aggregate nóng — một ví
tiền, tồn kho của một sản phẩm bán chạy — tích lũy event mãi mãi, và độ trễ đọc tăng theo tuổi của
nó. Không có dấu hiệu gì cho tới khi có lịch sử thật.

Đây không phải lý do để tránh event sourcing. Đây là lý do để coi snapshot là **hạ tầng bắt buộc,
không phải một tối ưu để thêm sau.**

## Snapshot

Định kỳ lưu trạng thái đã fold cùng version mà nó phản ánh:

```
snapshot(account_id, version=1000, state={balance: 195})
+ events 1001..1004
= current state
```

Nạp snapshot mới nhất, rồi chỉ apply các event sau nó.

- **Kích hoạt theo số event** (mỗi N event) thay vì theo thời gian — cách này chặn được trường hợp
  replay tệ nhất.
- **Snapshot là cache, không bao giờ là nguồn sự thật.** Chúng phải xóa được và dựng lại được hoàn
  toàn từ event. Nếu snapshot là nơi duy nhất một giá trị tồn tại, bạn không còn làm event sourcing
  nữa.
- **Version chúng theo schema trạng thái.** Khi hình dạng trạng thái thay đổi, snapshot cũ mất hiệu
  lực; chúng phải bỏ đi và sinh lại được, và điều đó an toàn chính vì event vẫn là nguồn có thẩm
  quyền.

Nghịch lý đáng gọi tên: bạn lưu toàn bộ lịch sử rồi lại sắp xếp để khỏi phải đọc phần lớn nó. Đó
không phải mâu thuẫn — lịch sử dành cho *tính đúng đắn và audit*, snapshot dành cho *độ trễ*.

## CQRS

Đọc và ghi muốn những thứ trái ngược nhau:

|              | Write model                       | Read model                          |
|--------------|-----------------------------------|-------------------------------------|
| Tối ưu cho   | Tính đúng đắn, bất biến           | Tốc độ query                        |
| Hình dạng    | Chuẩn hóa, giới hạn theo aggregate | Phi chuẩn hóa, theo hình của query |
| Khối lượng   | Thấp hơn                          | Thường cao hơn nhiều bậc độ lớn     |

Một schema phục vụ cả hai là một sự thỏa hiệp, xuống cấp dần khi một trong hai phía lớn lên. CQRS
tách chúng ra: command thay đổi write model, và **projection** dựng các read model có hình dạng phù
hợp với từng query cụ thể.

Hai điều cần làm rõ, giúp tránh phần lớn sai lầm với CQRS:

- **CQRS không phải event sourcing.** Chúng kết hợp tốt nhưng độc lập. Bạn có thể dùng CQRS trên các
  bảng CRUD thông thường, và dùng event sourcing với một model duy nhất. Áp dụng cả hai cùng lúc chỉ
  vì chúng xuất hiện trong cùng một bài viết là cách các team rước về hai bài toán khó thay vì một.
- **Read model là eventually consistent.** Người dùng vừa ghi xong rồi đọc ngay có thể không thấy
  thay đổi của chính mình — xem [consistency-boundaries](consistency-boundaries.md). Hãy thiết kế cho
  điều đó: đọc từ write model ở các luồng cần read-your-writes, hoặc để UI phản ánh command vừa gửi
  theo kiểu lạc quan. Đừng giả vờ độ trễ bằng không.
- **Command ra quyết định dựa trên write model, không bao giờ dựa trên read model.** Một phép kiểm
  tra số dư đọc từ projection sẽ duyệt lệnh rút tiền thứ hai trong khi lệnh thứ nhất vẫn đang xử lý,
  vì read model chưa bắt kịp. Phần validation bảo vệ tiền hay tồn kho phải nạp aggregate (hoặc lock
  dòng); projection dùng để hiển thị, không dùng để quyết định.

### Mỗi câu hỏi một read model, và cái giá của nó

Định hình read model theo từng use case giữ cho mỗi cái đều đơn giản, nhưng có hai cái giá chỉ lộ
ra ở quy mô dữ liệu production:

- **Khuếch đại cập nhật.** Một event có thể chạm tới nhiều dòng của read model — một thay đổi giá
  hay NAV cập nhật bản tóm tắt danh mục của mọi người nắm giữ. Độ trễ khi chạy thường ngày vẫn ổn,
  cho tới khi event đó bắn vào hàng chục nghìn dòng. Hãy đo fan-out của từng loại event và thời gian
  hội tụ sau event có fan-out lớn nhất, so với mục tiêu độ tươi của view.
- **Lệch giữa các bản sao.** Phi chuẩn hóa tên khách hàng vào ba view là có chủ đích, nhưng mỗi bản
  sao cần một owner rõ ràng, event cập nhật nó, và một phép kiểm tra định kỳ so với nguồn. Chỉ cần
  một projector bỏ sót một lần chỉnh sửa là các bản sao lệch nhau vô thời hạn.

## Materialized view không phải cache

Sự khác biệt nằm ở việc **dịch chuyển chi phí**, không phải một mẹo tăng tốc:

|               | Cache                                     | Materialized view                               |
|---------------|-------------------------------------------|-------------------------------------------------|
| Nạp dữ liệu   | Lười, khi miss                            | Có chủ đích, khi ghi hoặc theo lịch             |
| Khi vắng mặt  | Tính lại ngay tại chỗ (có đường chậm)     | Đơn giản là dữ liệu chưa có ở đó               |
| Vô hiệu hóa   | TTL / eviction, cố gắng hết mức          | Chiến lược refresh do bạn sở hữu                |
| Thẩm quyền    | Không bao giờ có thẩm quyền               | Không có thẩm quyền, nhưng là bản duy nhất được phục vụ |

Cache dời chi phí sang người đọc *đầu tiên* sau khi bị evict. Materialized view dời nó sang **lúc
ghi hoặc lúc refresh**, nên không người đọc nào phải trả. Điều này quan trọng vì lưu lượng đọc
thường tăng nhanh hơn lưu lượng ghi rất nhiều — tính trước một lần mỗi lần ghi để phục vụ một triệu
lượt đọc chính là toàn bộ lập luận.

Cái bẫy là đối xử với materialized view như cache và release mà không có chiến lược refresh. Khi đó
nó âm thầm trả về dữ liệu cũ, không có TTL nào để đổ lỗi và không có đường miss nào để tính lại. Mọi
materialized view đều cần câu trả lời tường minh cho: *cái gì refresh nó, bao lâu một lần, và độ cũ
tối đa mà bất kỳ ai có thể quan sát là bao nhiêu?* Refresh đồng thời
(`REFRESH MATERIALIZED VIEW CONCURRENTLY`) cũng quan trọng, nếu không lần refresh sẽ khóa chính
những người đọc mà nó tồn tại để phục vụ.

## Chuyển đổi từ CRUD

Một bảng CRUD giữ điểm đến cuối cùng của mỗi entity, không giữ con đường nó đã đi. Một dòng ở trạng
thái `REFUNDED` có thể là một lần hoàn toàn bộ, hoặc ba lần hoàn một phần sau một lần capture thất
bại. Chuyển nó thành một chuỗi event gọn gàng được suy ra từ trạng thái cuối là **bịa ra lịch sử**:
audit trail giờ cho thấy một con đường sạch sẽ chưa từng xảy ra, còn các lần retry, hủy và giải tỏa
mà một vụ tranh chấp cần thì đã biến mất.

Hãy bắt đầu mỗi stream được chuyển đổi bằng **một event mở đầu** —
`PaymentStateImported{legacy_version, …}` — nêu rõ snapshot legacy biết những gì tại thời điểm
chuyển giao. Các query trước mốc đó trả lời "chỉ có snapshot". Sau đó chuyển dần thẩm quyền, chứ
không phải chuyển dòng, theo từng giai đoạn:

1. **Lập baseline cho dữ liệu legacy** — số lượng, tổng theo trạng thái và loại tiền, các bản trùng
   và bản mồ côi đã biết — để khuyết tật sẵn có của nó không bị đổ cho luồng mới về sau.
2. **Phát ra các sự việc trong khi legacy vẫn giữ thẩm quyền**, thông qua một
   [outbox](message-delivery-semantics.md#outbox-do-not-lose-the-event).
3. **Dựng các shadow projection không ai đọc**, và phân loại mọi khác biệt thành bug legacy, bug
   mới, khác biệt ngữ nghĩa, hoặc chưa rõ. Mục chưa rõ phải về không trước khi đi tiếp.
4. **Shadow các command**: chạy aggregate mới song song với logic cũ và so sánh các quyết định của
   nó — luồng shadow không được gây side effect nào.
5. **Cut over theo từng entity hoặc tenant** thông qua một control marker mà router đọc. Sau mốc đó,
   bảng legacy trở thành một projection được nuôi từ stream.

Không bao giờ ghi vào cả hai store trong cùng một request mà không có ranh giới nguyên tử: một bên
thành công, bên kia timeout, và lần đọc tiếp theo tin vào bên nào đang cũ. Tại mọi thời điểm, mỗi
entity có đúng một store có thẩm quyền, và một metric **writes-to-legacy-after-cutover** chứng minh
không còn gì ghi vào store cũ.

## Khi nào không nên làm

Mỗi pattern ở đây đều thêm độ phức tạp vĩnh viễn. Hãy bỏ qua chúng khi:

- **Entity không có lịch sử nào đáng quan tâm.** Tùy chọn người dùng, feature flag, mục trong danh
  mục sản phẩm. "Nó đã đổi và giờ khác rồi" là đủ để giải thích — dùng CRUD.
- **Không ai sẽ hỏi "chuyện này xảy ra thế nào?"** Nếu không có yêu cầu audit, tranh chấp hay replay,
  một [audit log](data-retention-and-history.md) rẻ hơn event sourcing rất nhiều và trả lời được
  những câu hỏi bạn thực sự nhận được.
- **Team không gánh nổi tải vận hành.** Versioning schema event, rebuild projection, quản lý
  snapshot, và debug eventual consistency là công việc liên tục, không phải xây một lần là xong.

Mặc định thành thật: **CRUD cộng audit log** bao phủ hầu hết hệ thống. Chỉ dùng event sourcing ở nơi
trạng thái *được suy ra từ các biến động tiền hoặc có ý nghĩa pháp lý* — sổ cái, ví, vị thế giao
dịch — đúng là nơi [payment-ledger](payment-ledger.md) áp dụng.

## Checklist review

- [ ] Lựa chọn giữa CRUD, audit log và event sourcing được biện minh bằng một câu hỏi có thật
- [ ] Event được đặt tên ở thì quá khứ và lưu append-only, với `UPDATE`/`DELETE` bị thu hồi
- [ ] Event có thứ tự trong từng aggregate kèm version, và append dùng optimistic concurrency
- [ ] Versioning schema event đã được lên kế hoạch; các version event cũ vẫn đọc được — xem
      [tiến hóa schema event](event-schema-evolution.md)
- [ ] Chỉnh sửa là event bù trừ, không bao giờ sửa event đã lưu
- [ ] Snapshot có mặt trước khi lịch sử production tích lũy, kích hoạt theo số event
- [ ] Snapshot dựng lại được và không bao giờ là nguồn duy nhất của một giá trị
- [ ] Read model của CQRS được thừa nhận là eventually consistent, và read-your-writes được xử lý
- [ ] Command bảo vệ tiền hay tồn kho validate trên write model, không bao giờ trên projection
- [ ] Biết fan-out lên read model của từng loại event, và đã test thời gian hội tụ sau event lớn nhất
- [ ] Các bản sao phi chuẩn hóa có owner, event cập nhật và phép kiểm tra độ lệch
- [ ] Stream được chuyển đổi bắt đầu bằng một event mở đầu tường minh; không suy ra lịch sử từ trạng thái cuối
- [ ] Việc chuyển đổi có một store có thẩm quyền cho mỗi entity, shadow command, và metric writes-after-cutover
- [ ] Mọi materialized view có chiến lược refresh tường minh và độ cũ tối đa được nêu rõ
- [ ] Refresh materialized view không khóa người đọc
- [ ] Rebuild projection đã được diễn tập thực tế, không chỉ giả định là chạy được — xem
      [replay event & rebuild](event-replay-and-projection-rebuild.md)

---

*Bản dịch tiếng Việt của bài học cùng tên. Tổng hợp từ series Data Modeling Patterns phần P7–P10
của TechCraft, đối chiếu với Distributed Systems phần P9
([collection](https://www.patreon.com/techcraft_official)). Cách đặt vấn đề "khoản nợ ẩn" của event
sourcing, lập luận snapshot là bắt buộc, và sự phân biệt materialized view là một phép dịch chuyển
chi phí là của TechCraft; cấu trúc review và checklist là của repository này. Phần kiểm tra command
so với projection, khuếch đại cập nhật và độ lệch, cùng phần chuyển đổi từ CRUD (event mở đầu,
shadow projection và shadow command, cut over thẩm quyền theo entity, metric writes-after-cutover)
lấy từ Event Sourcing & CQRS phần P4, P11, P27 và P28 của TechCraft.*
