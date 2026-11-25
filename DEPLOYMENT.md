# Triển khai hệ thống báo giá U&I

Tài liệu này giữ nguyên hướng dẫn chạy nội bộ bên dưới và bổ sung phương án
triển khai Cloud. Môi trường nội bộ tiếp tục dùng SQLite/Ollama; Cloud dùng
Supabase PostgreSQL và AI tương thích OpenAI theo cấu hình Secrets. Không dùng
chung project Supabase với UNISmartPutaway.

## Mô hình sử dụng

- Chạy một bản Streamlit trên máy chủ nội bộ; Sales truy cập từ laptop của mình.
- Mỗi Sales đăng nhập bằng tài khoản do quản trị viên tạo. Không có đăng ký tự do.
- Báo giá được lưu trong SQLite trên máy chủ và mọi thao tác đọc, cập nhật, xóa đều giới hạn theo chủ sở hữu.
- Quản trị viên quản lý tài khoản nhưng không có giao diện xem báo giá của Sales.
- Dashboard và danh sách báo giá chỉ tổng hợp báo giá thuộc tài khoản Sales đang đăng nhập.
- Các trang Sales dùng chung sidebar xanh đậm, thanh công cụ và banner logistics. Thanh công cụ hỗ trợ thu gọn sidebar, tìm báo giá theo mã/khách hàng, xem thông báo, mở Tài khoản và đăng xuất.
- Khu vực quản trị dùng chung nhận diện logistics với Sales: sidebar xanh đậm, thanh công cụ với hamburger, tìm tài khoản, thông báo, cài đặt và hồ sơ; trang Quản lý tài khoản và Cài đặt hệ thống có banner logistics nổi bật.
- Dashboard Sales hiển thị thẻ chỉ số, biểu đồ hoạt động và báo giá gần đây; biểu đồ dùng cùng dữ liệu và phép tính trước đây.
- **Báo giá của tôi** hỗ trợ lọc theo thời gian/trạng thái, tìm kiếm, sắp xếp, phân trang, mở từng báo giá và xóa từng hoặc nhiều báo giá sau xác nhận. Trạng thái chỉ phản ánh độ đầy đủ dữ liệu, không đồng nghĩa đã gửi hoặc được khách hàng chấp nhận.
- **Tài khoản** cho phép cập nhật tên hiển thị và mật khẩu; tùy chọn thông báo và giao diện sáng/tối áp dụng trong phiên đăng nhập hiện tại.
- Trạng thái “Cần hoàn thiện/Đủ dữ liệu PDF” phản ánh kiểm tra dữ liệu, không đồng nghĩa đã gửi hoặc được khách hàng chấp nhận. Biểu đồ dùng lần cập nhật gần nhất vì hệ thống chưa lưu ngày gửi.
- Tên mô hình Ollama được cấu hình trong **Quản trị → Cài đặt hệ thống**; Sales không nhập cấu hình này.
- Sales tải file Pricing lên; AI đọc và tự tạo, lưu báo giá nháp từ thông tin nhận diện được.
- Trường không có trong file để trống và được cảnh báo; AI không tự đoán số lượng, giá, tuyến, khách hàng hoặc quy đổi ngoại tệ.
- Profit để trống cho Sales tự nhập theo USD/đơn vị. Các phí chưa phân loại hoặc có điều kiện không tự động đưa vào PDF.
- Sales có thể đặt markup (%) bằng thanh trượt; Profit mục tiêu bằng phần trăm nhân tổng giá mua, rồi được chia theo tỷ trọng giá mua × số lượng của từng dòng phí ngoài chi phí nội bộ. Ví dụ: giá mua 300 USD, markup 10% tạo Profit 30 USD và giá bán 330 USD. Profit trên mỗi đơn vị được làm tròn đến cent USD; Sales vẫn có thể chỉnh từng dòng sau đó.
- Thanh markup có dải 0–100%. Sales có thể xóa báo giá của mình trong **Báo giá của tôi** sau bước xác nhận; thao tác xóa không thể hoàn tác.
- Sales rà soát và lưu các chỉnh sửa, sau đó xác nhận nội dung trước khi tải PDF. Mọi thay đổi sau xác nhận đều yêu cầu xác nhận lại.
- Không đặt cơ sở dữ liệu trong thư mục dự án, OneDrive hoặc ổ đĩa mạng.

## Khởi tạo lần đầu trên máy chủ Windows

Mở PowerShell tại thư mục dự án và tạo quản trị viên đầu tiên:

```powershell
.\.venv\Scripts\python.exe manage_users.py create-admin admin "U&I Admin"
```

Nhập mật khẩu tối thiểu 12 ký tự khi Terminal yêu cầu. Mật khẩu không được ghi vào mã nguồn hoặc hiển thị trên màn hình.

Cơ sở dữ liệu mặc định nằm tại:

```text
%LOCALAPPDATA%\UIExportQuotation\quotes.sqlite3
```

Để chỉ định vị trí khác trên máy chủ, đặt biến môi trường `UI_QUOTE_DB_PATH` giống nhau cho lệnh khởi tạo và tiến trình Streamlit. Dùng ổ đĩa cục bộ có quyền truy cập giới hạn cho tài khoản chạy dịch vụ.

## Chạy phía máy chủ

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_cu.py --server.address 127.0.0.1 --server.port 8501
```

Khi truy cập từ laptop khác, phải đặt reverse proxy nội bộ có HTTPS trước Streamlit, chuyển tiếp `X-Forwarded-Proto: https`, và không mở trực tiếp cổng 8501 ra mạng. Ứng dụng chặn đăng nhập từ HTTP không bảo mật trên mạng; truy cập `localhost` vẫn dùng được để phát triển và kiểm thử.

## Cấp và quản lý tài khoản

Đăng nhập bằng tài khoản quản trị, tạo tài khoản Sales trong trang **Quản lý tài khoản**, rồi chuyển mật khẩu ban đầu cho đúng nhân viên. Mật khẩu tối thiểu 12 ký tự; Sales phải đổi mật khẩu ở lần đăng nhập đầu. Sau 5 lần đăng nhập sai liên tiếp, tài khoản bị khóa tạm thời 15 phút. Phiên đăng nhập hết hạn sau 30 phút không hoạt động. Khi nhân viên nghỉ hoặc đổi quyền truy cập, quản trị viên khóa tài khoản.

Mỗi Sales chỉ thấy báo giá có `owner_id` của chính tài khoản đó. Quản trị viên không thể mở báo giá Sales qua giao diện. Mật khẩu được lưu dưới dạng PBKDF2 hash; nội dung báo giá trong SQLite chưa được mã hóa ở tầng ứng dụng. Vì vậy, quyền truy cập trực tiếp vào máy chủ và tệp cơ sở dữ liệu phải được giới hạn ở cấp hệ điều hành, ổ đĩa máy chủ phải được bảo vệ/mã hóa, và bản sao lưu phải được mã hóa.

## Lưu ý vận hành

- Sao lưu SQLite định kỳ vào nơi lưu trữ được bảo vệ; kiểm tra khả năng khôi phục.
- Không sao chép cơ sở dữ liệu vào laptop Sales hoặc thư mục đồng bộ công khai.
- Ollama và mô hình cần chạy trên máy chủ nếu dùng chức năng AI; tài liệu bảng giá được gửi tới Ollama cục bộ.
- Quản trị viên cấu hình tên mô hình trong **Cài đặt hệ thống**. Mặc định là `qwen2.5vl:3b`; cần bảo đảm mô hình đó đã được tải trong Ollama trên máy chủ.
- Logo chính thức được lưu tại `assets/ui_logo.png` và dùng trong trang đăng nhập, thanh điều hướng, biểu tượng tab trình duyệt và PDF báo giá.
- Với ảnh hoặc PDF scan, hệ thống tự cắt khoảng trắng/phóng to bảng và yêu cầu AI đọc riêng từng dòng theo cột giá mua; dòng chưa rõ giá hoặc tiền tệ được kiểm tra thêm theo từng hàng cùng tiêu đề cột. Dấu `$` nhìn thấy trong ô giá được tính là USD. Giá không có ký hiệu tiền tệ được giữ trong ghi chú để Sales xác nhận. Giá ghi “at cost” giữ trống, không lấy nhầm số từ dòng kế tiếp. Nếu AI không trả đủ số dòng, Sales cần đối chiếu file gốc.
- Luồng Sales: mở tab **Tạo từ bảng Pricing**, tải file và chọn **ĐỌC FILE VÀ TẠO BÁO GIÁ NHÁP**. Sau đó kiểm tra tab **Hoàn thiện báo giá**, bổ sung trường còn thiếu, phân loại phí cần thiết, dùng thanh trượt markup (%) hoặc chỉnh Profit riêng từng dòng. Để thay báo giá bằng file Pricing mới, xóa bản nháp cũ trong **Báo giá của tôi**, xác nhận xóa, rồi tải file mới.
- Báo giá nháp được tự lưu sau khi AI đọc thành công. Nếu hướng vận chuyển chưa rõ, bản nháp được lưu kèm cảnh báo để Sales kiểm tra; tệp được nhận diện rõ là hàng nhập vẫn bị chặn. Nếu thiếu giá mua, số lượng hoặc đơn vị, giữ nguyên cảnh báo và không xuất PDF cho đến khi Sales bổ sung/xác nhận.
- Bản hiện tại dùng SQLite trên một máy chủ nội bộ, phù hợp triển khai nội bộ quy mô nhỏ với một tiến trình ứng dụng. Không dùng chung tệp SQLite qua SMB/NAS hay chạy nhiều máy chủ ứng dụng trên cùng tệp cơ sở dữ liệu.

## Triển khai Streamlit Community Cloud

### Lưu ý trước khi đưa dữ liệu thật lên Cloud

- Tạo **Supabase project riêng** cho UNISmartQuote, không chạy SQL hay cấu hình
  credentials trên project UNISmartPutaway. Chọn Free và không bật dịch vụ/add-on
  trả phí. Free hiện có giới hạn dung lượng và có thể pause sau thời gian không
  hoạt động; kiểm tra lại giới hạn/bảng giá trước khi sử dụng vì có thể thay đổi.
- Streamlit Community Cloud và Supabase là dịch vụ bên ngoài tổ chức. Chỉ đưa
  thông tin doanh nghiệp lên sau khi được công ty phê duyệt; dùng tài khoản, repo
  riêng tư và giới hạn người có quyền truy cập.
- Không commit `secrets.toml`, API key, mật khẩu DB hoặc database. Lưu secrets
  trong Streamlit Cloud; lưu database password/API key trong password manager.
- Không có bản sao lưu dữ liệu tự động trên Supabase Free theo cam kết lưu trữ
  dài hạn. Thiết lập và kiểm tra quy trình backup riêng trước khi dùng production.

### 1. Chuẩn bị repository và Python

1. Tạo một repository GitHub riêng cho UNISmartQuote; không chứa database, bản
   sao lưu, file Secrets hay dữ liệu khách hàng.
2. Đẩy nhánh triển khai lên repository chỉ sau khi rà soát diff và xác nhận
   `.gitignore` loại trừ `secrets.toml`, `.env`, SQLite và môi trường ảo.
3. Chọn Python **3.14** trong Advanced settings của Community Cloud nếu có trong
   danh sách. Ứng dụng và các phiên bản thư viện trong `requirements.txt` được
   chuẩn bị cho Python 3.14.10; Cloud chọn phiên bản 3.14 do Streamlit quản lý,
   không chọn được số patch cụ thể. `packages.txt` cài font DejaVu cần cho PDF
   tiếng Việt trên Linux.
4. File khởi chạy vẫn là `app_cu.py`. Không chuyển dữ liệu trong bước deploy.

### 2. Tạo database Supabase riêng

1. Tạo project mới tên UNISmartQuote trong Supabase Free. Lưu project ở tổ chức
   được phê duyệt và không chọn nâng cấp trả phí.
2. Tạo role database riêng tên `unisq_app`, có quyền LOGIN nhưng không có
   SUPERUSER, CREATEDB, CREATEROLE hay BYPASSRLS. Đặt một mật khẩu mạnh, duy nhất,
   không dùng mật khẩu project `postgres`. Trong SQL Editor tạo role không có
   password trước:

   ```sql
   CREATE ROLE unisq_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
       NOINHERIT NOREPLICATION NOBYPASSRLS;
   ```

   Sau đó kết nối bằng `psql` với role quản trị của project và chạy lệnh tương tác
   `\password unisq_app` để đặt password mà không ghi nó vào lệnh SQL. Không dùng
   chung project hoặc credentials với UNISmartPutaway.
3. Trong SQL Editor của **đúng project mới**, chạy nội dung
   [`supabase_setup.sql`](./supabase_setup.sql). Script tạo schema `unismartquote`,
   các bảng, khóa ngoại, chỉ mục, policy RLS cho bảng báo giá và cấp quyền cho role
   `unisq_app`.
4. Connection URL trong Secrets
   phải đăng nhập bằng `unisq_app`, dùng SSL và trỏ đến database của project
   UNISmartQuote. Nếu dùng Supavisor pooler, lấy đúng URI/username/host từ nút
   **Connect** của project; percent-encode ký tự đặc biệt trong password.
5. Tài khoản `unisq_app` là credential dùng bởi backend Streamlit. RLS và các
   truy vấn có điều kiện `owner_id` giúp giới hạn thao tác báo giá theo tài khoản
   đang đăng nhập; không cấp credential database này cho người dùng cuối. Vì ứng
   dụng dùng một role database chung và `app.current_user_id` do backend đặt,
   RLS không bảo vệ khỏi kẻ có được credential database và tự kết nối SQL; bảo vệ
   chặt chẽ Secrets và giới hạn quyền truy cập vào project.

### 3. Tạo Secrets an toàn

1. Mở [`.streamlit/secrets.toml.example`](./.streamlit/secrets.toml.example) để
   xem cấu trúc. Dán **cấu trúc TOML** vào ô Secrets của Streamlit Community Cloud,
   không commit bản thật vào repository.
2. Thay giá trị rỗng trong giao diện Secrets. Các khóa cần cấu hình:

   - `database.url`: PostgreSQL connection URI của role `unisq_app`.
   - `ai.provider`: `openai_compatible` khi dùng AI Cloud; `ollama` cho Ollama
     nội bộ. Community Cloud không tự chạy Ollama cục bộ.
   - `ai.base_url`: URL tương thích OpenAI, ví dụ endpoint OpenAI-compatible của
     Gemini API.
   - `ai.model`: tên model được nhà cung cấp cấp quyền sử dụng.
   - `ai.api_key`: API key do nhà cung cấp AI tạo.
   - `bootstrap_admin.username`, `bootstrap_admin.display_name`,
     `bootstrap_admin.password`: thông tin khởi tạo Admin đầu tiên.

3. Tạo mật khẩu Admin ngẫu nhiên, tối thiểu 12 ký tự, khác password DB và không
   gửi mật khẩu/API key qua chat, issue hoặc repository. Secrets bootstrap chỉ
   được dùng khi database chưa có Admin đang hoạt động. Sau khi đăng nhập lần
   đầu, đổi mật khẩu Admin; sau đó xóa ba khóa `bootstrap_admin.*` khỏi Secrets
   và lưu thay đổi/reboot app. Nếu xóa cấu hình trước khi bootstrap thành công,
   chỉ cần thêm lại Secrets và khởi động lại.
4. App hiển thị danh sách tên khóa còn thiếu, không hiển thị giá trị secrets.
   Không chụp/chia sẻ màn hình hoặc log có Secrets.

### 4. Cấu hình AI Cloud và quyền riêng tư

1. Tạo API key trong trang chính thức của nhà cung cấp OpenAI-compatible. Gemini
   API có endpoint tương thích tại
   `https://generativelanguage.googleapis.com/v1beta/openai/`; chọn một model
   vision được tài khoản cho phép, kiểm tra quota/free tier và điều khoản sử dụng
   tại thời điểm cấu hình. Không bật billing hoặc dịch vụ trả phí nếu yêu cầu là
   không phát sinh thanh toán.
2. Điền `ai.provider`, `ai.base_url`, `ai.model`, `ai.api_key` trong Streamlit
   Secrets. Không ghi các giá trị vào file mã nguồn. Chọn `ollama` để giữ cấu hình
   nội bộ khi chạy local; các giá trị AI Cloud không làm đổi logic trích xuất hay
   tính giá.
3. Trước khi gửi từng file Pricing lên AI Cloud, người dùng phải chọn checkbox
   xác nhận. Nhà cung cấp bên ngoài có thể xử lý/lưu nội dung theo điều khoản và
   gói dịch vụ của họ. Không gửi dữ liệu nội bộ nếu chưa được doanh nghiệp cho
   phép; bỏ chọn checkbox để không gửi.
4. Nếu AI Cloud lỗi, người dùng vẫn có thể nhập dữ liệu thủ công, tính giá, lưu
   báo giá và xuất PDF. Không tự chuyển dữ liệu sang provider khác.

### 5. Tạo ứng dụng Community Cloud

1. Tại [share.streamlit.io](https://share.streamlit.io), chọn **Create app**,
   repository, nhánh triển khai và entrypoint `app_cu.py`.
2. Mở **Advanced settings**, chọn Python 3.14 (nếu có) và dán TOML thật vào ô
   **Secrets**. Không tải `secrets.toml` lên GitHub.
3. Deploy và đợi logs báo ứng dụng khởi động thành công. Nếu khởi động lỗi, kiểm
   tra Python/dependencies, SSL và tên role/schema; không đưa giá trị Secrets vào
   log hoặc chia sẻ log công khai.
4. Tạo/đăng nhập Admin đầu tiên, đổi password, xóa secrets bootstrap. Admin tạo
   các tài khoản Sales trong trang quản lý tài khoản. Không có đăng ký tự do.
5. Kiểm thử với dữ liệu giả: đăng nhập Sales, tạo/chỉnh sửa báo giá, kiểm tra mỗi
   Sales chỉ thấy báo giá của mình, xuất PDF tiếng Việt, và kiểm tra lỗi AI Cloud
   vẫn để lại luồng nhập tay. Chỉ chuyển sang dữ liệu thật sau khi được phê duyệt.

### 6. Chuyển dữ liệu SQLite (chỉ khi có xác nhận riêng)

Migration **không chạy tự động** trong deploy. Lệnh mặc định chỉ đọc schema và
đếm số dòng, không in nội dung:

```powershell
.\.venv\Scripts\python.exe migrate_sqlite_to_postgres.py
```

Chỉ sau khi có phê duyệt bằng văn bản, backup SQLite được xác minh, ứng dụng local
đã dừng để không có ghi đồng thời, project UNISmartQuote đúng được xác nhận và
database đích trống, mới chạy:

```powershell
.\.venv\Scripts\python.exe migrate_sqlite_to_postgres.py --execute
```

URL nhập vào được ẩn; chương trình yêu cầu gõ `MIGRATE`, dùng transaction và
kiểm tra số dòng sau khi chép. Không chạy thử với CSDL sản xuất trong giai đoạn
chuẩn bị này. Công cụ từ chối target không trống hoặc thiếu schema và không xóa
hay sửa SQLite nguồn.

### File triển khai Cloud

- Tạo mới: `app_config.py`, `requirements.txt`, `packages.txt`,
  `.streamlit/secrets.toml.example`, `supabase_setup.sql`,
  `migrate_sqlite_to_postgres.py`, `.gitignore`.
- Chỉnh sửa: `app_cu.py` (chọn AI Cloud, consent từng file và bootstrap Admin),
  `ai_reader.py` (adapter OpenAI-compatible, giữ Ollama/local và logic hiện có),
  `security_store.py` (PostgreSQL và RLS, giữ SQLite/local),
  `manage_users.py` (ngăn dùng CLI local trên database Cloud),
  `test_system.py` (kiểm thử adapter/consent/migration bằng mocks và dữ liệu giả),
  `DEPLOYMENT.md` (hướng dẫn này).
- Không chỉnh sửa `pricing_logic.py`; công thức báo giá và module xuất PDF không
  bị thay đổi. Không có migration dữ liệu thật, push, merge hay deploy trong
  giai đoạn chuẩn bị.
