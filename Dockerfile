# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

# install required system packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Voicevox Coreのアセット（ONNXランタイム、辞書、モデル）をダウンロード
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
        DOWNLOADER_URL="https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.4/download-linux-x64"; \
    elif [ "$ARCH" = "arm64" ]; then \
        DOWNLOADER_URL="https://github.com/VOICEVOX/voicevox_core/releases/download/0.16.4/download-linux-arm64"; \
    else \
        echo "Unsupported architecture: $ARCH" && exit 1; \
    fi && \
    curl -sSL "$DOWNLOADER_URL" -o /tmp/download-linux && \
    chmod +x /tmp/download-linux && \
    yes "y" | /tmp/download-linux -o /app/voicevox_core --exclude c-api --exclude models && \
    yes "y" | /tmp/download-linux -o /app/voicevox_core --only models --models-pattern 0.vvm && \
    rm /tmp/download-linux

# copy application code
COPY . .

# timezone mapping default
ENV TZ=Asia/Tokyo

CMD ["python", "yomi-KAI.py"]
