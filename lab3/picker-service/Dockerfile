FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    KAFKA_TOPIC=orders \
    KAFKA_GROUP_ID=pickers \
    INSTANCE_NAME=picker-1 \
    PROCESS_SECONDS=2 \
    AUTO_OFFSET_RESET=earliest \
    PORT=8001 \
    PYTHONUNBUFFERED=1

EXPOSE 8001
CMD ["python", "app.py"]
