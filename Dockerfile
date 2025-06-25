# Dùng Python 3.11.6 chính thức
FROM python:3.11.6-slim

# Đặt thư mục làm việc trong container
WORKDIR /app

# Sao chép toàn bộ project từ máy bạn vào container
COPY . /app

# Cập nhật pip và cài đặt thư viện từ requirements.txt
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Khởi động bot
CMD ["python", "Bot.py"]
