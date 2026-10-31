---
source_sha256: 9426b5de9e7e600ca78689d4819ed7411794910f6a83b4ce4d52d7579a8ef140
---

# Định danh container & secret

**Quy tắc: process chạy code của bạn không phải là process mà orchestrator đã cấu hình — nó có user
khác, group khác, và một môi trường nhỏ hơn.** Mọi thứ bạn trao cho container lúc khởi động đều bị lọc
trước khi tới được process thực sự quan trọng.

Thuộc nhóm backend runtime: [mô hình concurrency](server-concurrency-model.md) ·
[bộ nhớ & GC](memory-and-gc.md) · [caching](caching.md) ·
[distributed lock](distributed-locks.md) ·
[health check & load balancing](health-checks-and-load-balancing.md) ·
[deploy & cấu hình](deployment-and-config.md) ·
**định danh container & secret** (trang này)

## Mục lục

- [Anti-pattern](#the-anti-pattern)
- [Vì sao môi trường dev che giấu lỗi này](#why-development-hides-it)
- [Ba bộ lọc giữa manifest và process](#three-filters-between-the-manifest-and-the-process)
- [Biến môi trường không sống sót qua việc hạ quyền](#the-environment-does-not-survive-a-privilege-drop)
- [fsGroup là một group, không phải một quyền](#fsgroup-is-a-group-not-a-permission)
- [Giao secret dưới dạng file](#deliver-secrets-as-files)
- [Mặc định của image lấn át ý định của bạn](#image-defaults-outrank-your-intent)
- [Cách kiểm chứng](#how-to-verify)
- [Checklist review](#review-checklist)

## Anti-pattern

Một StatefulSet trông hoàn toàn đúng:

```yaml
securityContext:
  fsGroup: 1000                 # để pod ghi được vào volume của nó
containers:
  - env:
      - name: APP_PASSWORD      # được một bootstrap hook sử dụng
        valueFrom: { secretKeyRef: { name: db-secret, key: app-password } }
      - name: DATA_DIR
        value: /var/lib/app/data
```

Giá trị nào cũng đúng. Image khởi động dưới quyền root, làm phần thiết lập, rồi hạ xuống user ứng dụng
không có đặc quyền — đó là hành vi image ghi trong tài liệu và là lý do nó được coi là được build tốt.
Thế rồi: ứng dụng không đọc được token đã mount, `APP_PASSWORD` không được set, và database khởi tạo lên
filesystem của container thay vì lên volume.

Không có gì trong manifest đó sai cả. Chỉ là manifest không phải thứ mà process nhận được.

## Vì sao môi trường dev che giấu lỗi này

`docker run` ở local dùng cùng một user từ đầu đến cuối, thường là root, với đầy đủ biến môi trường và
một bind mount thuộc sở hữu của bạn. Mọi bộ lọc mô tả dưới đây đều không hoạt động. Container khởi động,
ứng dụng đọc config, và thiết lập được tuyên bố là đúng.

Các bộ lọc chỉ vào cuộc khi một thứ *khác* chạy process: một orchestrator inject `fsGroup`, một
supervisor làm sạch biến môi trường, một entrypoint của image chuyển user. Cả ba đều là đường đi bình
thường trên production và không cái nào tồn tại trên laptop.

## Ba bộ lọc giữa manifest và process

```
manifest  ──▶  kubelet        ──▶  entrypoint (root)  ──▶  supervisor  ──▶  your process
               fsGroup rewrites     image ENV defaults      environment      (uid != 0,
               volume ownership     already set             allowlist         fewer groups,
                                                                             fewer vars)
```

Mỗi mũi tên đều bỏ đi hoặc viết lại một thứ gì đó. Một lần review chỉ đọc manifest thì chỉ thấy mép
bên trái.

## Biến môi trường không sống sót qua việc hạ quyền

Một supervisor hạ quyền thường làm sạch biến môi trường trên đường đi, một cách có chủ đích, để
credential không thể rò vào cây process hay crash dump. Ví dụ thật — một định nghĩa service runit khởi
động supervisor của database:

```sh
# Chỉ một tập nhỏ biến môi trường được phép giữ lại.
for E in $(printenv -0 | ... | grep -vE '^(KUBERNETES_(SERVICE|PORT)[_=]|(POD_(IP|NAMESPACE)|HOSTNAME|PATH|HOME)=)'); do
    unset $E
done
exec chpst -u app myservice /etc/app.yml
```

Mọi thứ không có trong allowlist đó đều biến mất trước `exec`. Điều tương tự xảy ra, kín đáo hơn, với
`su app -c …`, vốn reset môi trường về môi trường của một login shell, và với `sudo` khi không có `-E`.
Những hệ quả đáng biết:

- **Config truyền qua env âm thầm quay về giá trị mặc định trong file.** Process đọc file config của nó
  và không bao giờ thấy override bạn đã đặt, nên nó chạy với những giá trị không ai chọn.
- **Secret truyền qua env đơn giản là không có.** Một hook chạy như *process con* của process đó thừa
  hưởng môi trường đã bị lột sạch, không phải môi trường của pod — nên một bootstrap script thất bại ngay
  dòng đầu tiên sau khi toàn bộ phần thiết lập tốn kém xung quanh đã thành công.
- **Biến đó vẫn hiển thị với `kubectl exec` và với probe**, vì đó là những process mới được sinh ra từ
  config của container chứ không phải con của process được supervisor quản lý. Đây là chi tiết khiến
  người ta khó tin: `kubectl exec -- env` in ra đúng cái biến mà process đang chạy không nhìn thấy.

## fsGroup là một group, không phải một quyền

`fsGroup` khiến kubelet chown các volume được mount sang GID đó và nới rộng bit quyền của group. Nó
thường được thêm vào để sửa quyền ghi trên một volume, và nó âm thầm thay đổi **mọi** volume trong pod
— kể cả token service account dạng projected:

```
-rw-r----- 1 root 1000  /var/run/secrets/kubernetes.io/serviceaccount/token
```

Owner là `root`, group là `1000`, và **không có bit đọc cho mọi người**. Một process chạy dưới quyền
`root` đọc được nó. Một process đã hạ xuống user ứng dụng chỉ đọc được nếu user đó thuộc group 1000 — và
có hai điều cùng chống lại chuyện đó:

- GID của user ứng dụng do image đặt, không phải do bạn. Nó thường không phải 1000.
- Group bổ sung mà kubelet inject vào **không sống sót qua `su` hay `chpst -u`**, vốn reset các group bổ
  sung về những group ghi trong `/etc/group` cho user đích.

Vì vậy lỗi hiện ra là một thư viện client không đọc nổi credential của chính nó:

```
PermissionError: [Errno 13] Permission denied:
  '/var/run/secrets/kubernetes.io/serviceaccount/token'
```

trông như một vấn đề RBAC nhưng không phải — nó thất bại trước khi có bất kỳ lời gọi API nào. Hãy đặt
`fsGroup` bằng GID mà *user ứng dụng thực sự có*, và đối chiếu với image thay vì giả định con số quen
thuộc 1000.

Sự lệch pha tương tự cũng đánh vào volume dữ liệu. Một volume được chown sang group mà process không
thuộc về thì với nó, tốt nhất cũng chỉ là chỉ-đọc, và một database được giao một thư mục sai mode sẽ từ
chối khởi động luôn.

## Giao secret dưới dạng file

Mount secret và đọc file:

```yaml
volumes:
  - name: secrets
    secret:
      secretName: app-secret
      defaultMode: 0440        # group đọc được; fsGroup cung cấp group
```

```bash
APP_PASSWORD="$(cat /etc/app/secrets/app-password)"
```

Một file sống sót qua mọi bộ lọc ở trên: nó không phụ thuộc vào biến môi trường, process con ở bất kỳ
độ sâu nào cũng đọc được, và nó không bao giờ xuất hiện trong `ps`, trong crash dump, hay trong môi
trường process của một container mà sau này ai đó `exec` vào. Mode là quan trọng — `0440` cộng với
`fsGroup` đúng thì user ứng dụng đọc được và không ai khác đọc được; mặc định `0644` thì ai trong
container cũng đọc được.

Đây cũng là lý do supervisor lột sạch biến môi trường ngay từ đầu. Hãy coi việc xoá sạch đó là nền tảng
đang cho bạn biết nó tin tưởng cơ chế giao nhận nào.

## Mặc định của image lấn át ý định của bạn

`ENV` của chính image đã được set khi entrypoint của bạn chạy, và các entrypoint script thường tính các
đường dẫn dẫn xuất bằng `${VAR:-default}` — vốn chẳng làm gì khi biến đó đã được image điền sẵn. Đặt
thư mục *cha* của một đường dẫn không làm đường dẫn đó dịch chuyển:

```
image:    ENV PGDATA=/home/app/data/main
manifest: APP_ROOT=/var/lib/app        # the volume
result:   PGDATA is still /home/app/data/main — on the container filesystem
```

Database khởi tạo thành công lên storage tạm thời, volume vẫn trống, và dữ liệu biến mất ở lần khởi
động lại tiếp theo. Hãy đặt đúng biến lá mà process thực sự đọc, và kiểm chứng nó trong config *được
sinh ra* thay vì trong manifest.

## Cách kiểm chứng

Kiểm tra process, đừng bao giờ kiểm tra manifest — manifest là đầu vào của các bộ lọc, không phải đầu
ra:

```bash
# Ứng dụng thực sự chạy dưới định danh nào?
kubectl exec POD -- id app

# Định danh đó đọc được gì? (không phải root đọc được gì)
kubectl exec POD -- su app -s /bin/sh -c \
  'id; cat /var/run/secrets/kubernetes.io/serviceaccount/token > /dev/null && echo READABLE'

# Process thực sự nhận được gì, so với những gì container được trao?
kubectl exec POD -- cat /proc/1/environ | tr '\0' '\n'   # hoặc file config được sinh ra
```

Lệnh thứ hai mới là lệnh quan trọng: `kubectl exec -- cat` thành công dưới quyền root trong khi user
ứng dụng bị từ chối, và chỉ dạng `su` mới tái hiện đúng những gì service nhìn thấy.

## Checklist review

- [ ] Đã biết UID và GID mà process ứng dụng chạy dưới, và lấy chúng từ image
- [ ] `fsGroup` khớp với GID đó — không giả định là 1000
- [ ] Mọi `su`, `gosu`, `chpst -u` hay supervisor trên đường khởi động đều đã được xác định
- [ ] Không có config nào daemon cần mà chỉ được giao qua biến môi trường khi tồn tại một bước hạ
      quyền như vậy
- [ ] Secret tới process dưới dạng file được mount, không phải biến môi trường
- [ ] Volume chứa secret đặt `defaultMode` tường minh, không dùng mặc định ai cũng đọc được
- [ ] Các biến image đã định nghĩa được override ở biến lá, không qua đường dẫn cha
- [ ] Thư mục dữ liệu được xác nhận là nằm trên volume đã mount sau lần khởi động đầu tiên, không giả định
- [ ] Việc kiểm chứng được làm dưới user ứng dụng, không phải root

---

*Bản dịch tiếng Việt của bài học cùng tên. Được viết từ một sự cố trực tiếp trải qua trên một cluster
PostgreSQL Patroni/Spilo chạy trên Kubernetes: một token service account không đọc được sau khi
`fsGroup` được đặt sang một GID mà user `postgres` không thuộc về, một định nghĩa service runit xoá sạch
mọi biến `PATRONI_*` trước khi khởi động daemon, một bootstrap hook thất bại vì mật khẩu của nó được
truyền qua biến môi trường, và một database khởi tạo lên filesystem của container vì `PGDATA` của chính
image lấn át override `PGROOT`. Phần khái quát hoá sang supervisor, việc hạ quyền và cách giao secret là
của repository này.*
