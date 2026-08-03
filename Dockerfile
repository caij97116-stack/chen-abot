FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fly.io 需要监听 8080 端口
ENV PORT=8080

CMD ["python", "main.py"]