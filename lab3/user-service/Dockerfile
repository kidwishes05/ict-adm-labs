FROM python:3.12-slim

WORKDIR /app

# Сначала зависимости — этот слой кэшируется и не пересобирается при правке кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Значения по умолчанию; в compose/Helm переопределяются через env
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    KAFKA_TOPIC=orders \
    INSTANCE_NAME=user-1 \
    PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "app.py"]
