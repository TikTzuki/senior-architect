---
source_sha256: 435ad1c8ffad9ab06300856d96df2889fb32786c3ef424321d8befa9093a6c61
---

# Upload file lớn

**Quy tắc: file của người dùng không bao giờ được stream qua application server.** Client upload
thẳng lên object storage; backend chỉ cấp quyền upload và phản ứng khi upload hoàn tất.

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Nó hỏng thế nào trên production](#how-it-fails-in-production)
- [Pattern: upload trực tiếp](#the-pattern-direct-upload)
- [Multipart, cho những file thực sự lớn](#multipart-for-genuinely-large-files)
- [Làm cho nó chịu được retry](#making-it-survive-retries)
- [Điều kiện bảo mật trên quyền upload](#security-conditions-on-the-grant)
- [Download cũng gặp đúng vấn đề này](#downloads-have-the-same-problem)
- [Checklist review](#review-checklist)

## Anti-pattern

```
Client  ──upload 2GB video──▶  App server  ──write──▶  Object storage
                               (holds the whole
                                request in RAM/disk)
```

File đi xuyên qua process của ứng dụng. Từng byte chiếm một request handler, một connection, và
hoặc bộ nhớ hoặc disk tạm trên một máy mà nhiệm vụ thật sự là phục vụ request.

Dấu hiệu trong code: một handler multipart form gắn toàn bộ file vào bộ nhớ hoặc một đường dẫn tạm,
rồi upload lại lên storage. `MultipartFile` được bind vào tham số của controller, `multer` lưu
xuống disk rồi gọi `putObject` của SDK, `request.files[...]` được đọc vào buffer, một handler làm
`read()` rồi `upload()`.

## Vì sao môi trường dev che giấu lỗi này

Một developer, mỗi lần một upload, mạng loopback, disk local nhanh, và không có proxy nào đứng
trước. Trong những điều kiện đó luồng xử lý là đúng — chỉ là nó chưa từng phải chịu tải thật. Không
có gì trong môi trường local tạo ra áp lực đủ để làm nó vỡ.

## Nó hỏng thế nào trên production

- **Cạn worker.** Mỗi upload đang chạy giữ chặt một worker hoặc connection trong suốt thời gian
  truyền. Upload chậm vì đường uplink của client chậm; chỉ vài người dùng có kết nối kém là đủ làm
  đầy pool và làm treo cả những request không liên quan. Đây thường là triệu chứng đầu tiên, và nó
  trông như một đợt tăng latency bí ẩn trên các endpoint chẳng dính dáng gì tới upload.
- **Cạn bộ nhớ và disk.** Những chiến lược buffer ổn với một avatar 2 MB sẽ sụp với một video
  2 GB, nhất là khi nhiều file đến cùng lúc. Container bị OOM-kill; disk tạm đầy và cứ đầy mãi khi
  bước dọn dẹp bị bỏ qua trên nhánh lỗi.
- **Timeout ở proxy và gateway.** Load balancer và reverse proxy giới hạn thời lượng request và
  kích thước body — mặc định thường là 60 s và 1–100 MB. Một upload chậm bị cắt giữa chừng và client
  nhận 504, không giữ lại chút tiến độ nào.
- **Retry nhân tải lên.** Một client retry một upload 2 GB thất bại sẽ gửi lại 2 GB nữa. Những cơn
  bão retry trong sự cố biến một service đang xuống cấp thành một service bị bão hoà.
- **Đến sai thứ tự và đến đồng thời.** Những cơ chế chia chunk giả định dữ liệu đến tuần tự sẽ làm
  hỏng file khi các phần đến lộn xộn, và hai upload đồng thời vào cùng một key sẽ đan xen thành một
  file chẳng phải của bên nào.
- **Không resume được.** Lỗi ở 95% là bắt đầu lại từ 0%, mà trên mạng di động điều đó có nghĩa là
  upload có thể không bao giờ xong.

## Pattern: upload trực tiếp

```
1. Client ──"I want to upload"──▶ Backend        authorize, record intent (status: pending)
2. Backend ──presigned URL──────▶ Client          short-lived, tightly scoped grant
3. Client ──────PUT bytes───────▶ Object storage  never touches the app server
4. Storage ──event──▶ Queue ──▶ Worker            validate, then mark ready
```

**Bước 1 — cấp quyền và ghi nhận ý định.** Backend kiểm tra phía gọi có được phép upload hay không,
tự sinh object key (không bao giờ nhận key từ client), và ghi một dòng ở trạng thái `pending`. Dòng
đó là thứ mà phần còn lại của hệ thống tham chiếu tới; lúc này file vẫn chưa tồn tại.

**Bước 2 — cấp một quyền có phạm vi hẹp.** Trả về một presigned URL có hiệu lực tính bằng phút,
không phải bằng giờ, và bị ràng buộc như mô tả ở phần
[điều kiện bảo mật](#security-conditions-on-the-grant).

**Bước 3 — client upload trực tiếp.** Byte đi thẳng tới object storage. App server không nằm trên
đường đi và worker pool của nó không bị đụng tới.

**Bước 4 — phản ứng khi hoàn tất.** Có hai lựa chọn, và chọn cái nào là quan trọng:

- *Storage event → queue → worker* (`s3:ObjectCreated:*` → SQS/SNS, hoặc bucket notification).
  Đáng tin: nó chỉ bắn ra khi object thực sự tồn tại. Nên chọn cách này.
- *Client gọi lại* (`POST /uploads/{id}/complete`). Đơn giản hơn, nhưng một client bị crash hoặc mất
  mạng sau khi upload sẽ để bản ghi nằm ở `pending` mãi mãi. Nếu dựa vào cách này, hãy thêm một job
  đối soát liệt kê storage và sửa lại các bản ghi mồ côi.

Worker kiểm tra những gì client có thể khai man — kích thước thật, content type thật được nhận diện
từ magic bytes thay vì header khai báo, quét virus, giới hạn kích thước ảnh hoặc thời lượng — rồi
mới chuyển bản ghi sang `ready`. Chỉ khi đó file mới hiển thị với phần còn lại của sản phẩm.

## Multipart, cho những file thực sự lớn

Trên khoảng 100 MB, một lệnh PUT duy nhất là một đơn vị công việc tồi. Multipart upload chia object
thành các phần được upload song song và retry riêng lẻ.

- Kích thước phần 5–100 MB. Nhỏ hơn thì nhiều request và nhiều overhead hơn; lớn hơn thì mỗi lần
  retry tốn hơn. 8–16 MB là một giá trị mặc định hợp lý.
- Các phần được upload đồng thời và không theo thứ tự — đó là thiết kế có chủ đích: storage ghép
  chúng lại theo số thứ tự phần, nên việc đảo thứ tự không ảnh hưởng tính đúng đắn như trong một cơ
  chế chia chunk tự viết.
- Phần nào lỗi thì chỉ retry phần đó. Đây là điều giúp upload lớn sống sót qua mạng di động chập
  chờn.
- **Đặt lifecycle rule để huỷ các multipart upload chưa hoàn tất** (thường là 7 ngày). Thiếu nó,
  các phần bị bỏ dở cứ tích tụ thành dung lượng bạn phải trả tiền mà không nhìn thấy trong danh
  sách object thông thường. Đây là một nguồn chi phí vô hình và rất phổ biến.

Presign URL cho từng phần, hoặc cấp credential có phạm vi hẹp và để SDK phía client điều khiển việc
truyền.

## Làm cho nó chịu được retry

Retry là bình thường, không phải ngoại lệ. Hãy thiết kế để một bản trùng là vô hại:

- **Key tất định.** Suy ra object key từ ID của bản ghi upload, để retry ghi vào đúng key cũ thay
  vì tạo ra object thứ hai.
- **Idempotency key trên lời gọi hoàn tất.** Một lần gọi hoàn tất lặp lại phải là no-op trả về cùng
  kết quả, không phải một lượt xử lý thứ hai — nếu không, một upload sinh ra hai job transcode và
  hai lần tính tiền.
- **Bảo vệ chuyển trạng thái.** Chuyển `pending → ready` bằng một update có điều kiện, để hai lần
  hoàn tất đồng thời không thể cùng đi tiếp.
- **Đừng bao giờ chỉ tin vào việc client báo thành công.** Xác nhận object tồn tại và kích thước
  khớp trước khi đánh dấu bất cứ thứ gì là ready.

## Điều kiện bảo mật trên quyền upload

Presigned URL là một bearer credential. Ai cầm nó thì có đúng quyền mà nó mã hoá, nên hãy mã hoá
càng ít càng tốt:

- **TTL ngắn** — tính bằng phút. URL sống lâu sẽ rò rỉ qua log, header referrer và ảnh chụp màn
  hình.
- **Cố định content length** bằng điều kiện khoảng kích thước. Thiếu nó, một quyền upload dành cho
  avatar 5 MB sẽ nhận cả file 5 GB, và hoá đơn storage của bạn chính là lỗ hổng.
- **Cố định content type**, và sau đó vẫn nhận diện type thật ở phía server. Type khai báo chỉ là
  lời khẳng định của client.
- **Key do server sinh, có namespace theo người dùng** (`uploads/{user_id}/{uuid}`). Chấp nhận key
  do client cung cấp là mở ra công cụ path traversal và ghi đè — một client đặt key là
  `uploads/other-user/avatar.png` vừa ghi đè file của người khác.
- **Không bao giờ đưa nguyên tên file của client vào key.** Làm sạch rồi lưu vào metadata nếu bạn
  cần nó làm tên file khi download.
- **Chặn truy cập public vào bucket.** Phục vụ việc đọc qua presigned GET hoặc CDN với signed URL.

## Download cũng gặp đúng vấn đề này

Stream file ngược về qua application server có cùng kiểu thất bại theo chiều ngược lại — một worker
bị giữ chặt suốt một lượt download chậm. Hãy redirect tới một presigned GET, hoặc đặt CDN phía trước
với signed URL. App server quyết định phía gọi *có được* đọc file hay không; nó không nên là thứ
truyền file đi.

## Checklist review

- [ ] Byte của file không bao giờ đi qua request handler của ứng dụng
- [ ] Quyền upload có thời hạn ngắn và phạm vi hẹp: khoảng kích thước, content type, key do server sinh
- [ ] Object key có namespace theo người dùng và không bao giờ suy ra từ input của client
- [ ] Dùng multipart khi trên ~100 MB, có cấu hình lifecycle rule huỷ upload chưa hoàn tất
- [ ] Việc hoàn tất được kích hoạt bởi storage event, hoặc bởi client callback kèm một job đối soát
- [ ] Xử lý hoàn tất là idempotent và chuyển trạng thái được bảo vệ khỏi race
- [ ] Content type thật được nhận diện ở phía server; không tin type khai báo
- [ ] Download đi qua presigned URL hoặc CDN, không qua app server
- [ ] Giới hạn body size và timeout của proxy/gateway nhất quán với nhu cầu thực tế của luồng
- [ ] File tạm được dọn trên nhánh lỗi, không chỉ khi thành công

---

*Bản dịch tiếng Việt của bài học cùng tên. Chủ đề được gợi ý từ một
[bài đăng của TechCraft](https://www.facebook.com/techcraft.official) về upload file lớn trên
production. Bài đăng nêu vấn đề và chỉ ra upload thẳng lên object storage là lời giải; phần chi tiết
ở đây được viết từ thực tiễn đã được kiểm chứng, không chép lại từ bài đăng. Các series Database
Internals và Data Modeling Patterns có được sau đó không đề cập kiến trúc upload, nên bài học này vẫn
là công trình độc lập.*
