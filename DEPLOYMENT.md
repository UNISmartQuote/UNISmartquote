# Triển khai hệ thống báo giá U&I

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
