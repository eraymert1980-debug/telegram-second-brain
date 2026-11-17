FROM python:3.11-slim

WORKDIR /app

# Gerekli sistem kütüphaneleri
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Veritabanı ve geçici dizinler için
RUN mkdir -p /app/temp

CMD ["python", "bot.py"]

