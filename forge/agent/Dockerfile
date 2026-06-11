FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ .

ENV FORGE_WORKSPACE=/data/workspace
ENV PORT=8080

RUN mkdir -p /data/workspace /data/db

VOLUME ["/data/workspace", "/data/db"]

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
