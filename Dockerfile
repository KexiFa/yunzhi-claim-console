FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libjpeg62-turbo \
       zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY yz_core.py yz_cloudphone_claim.py web_app.py ./
COPY config.example.json ./
COPY static ./static
COPY templates ./templates

RUN cp config.example.json config.json

EXPOSE 8788

CMD ["python", "web_app.py"]
