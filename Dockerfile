# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

# install required system packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Bot を実行する一般ユーザー（root では動かさない）。起動時の権限の切り替えは docker-entrypoint.sh が行う
RUN groupadd --gid 1000 yomi && useradd --uid 1000 --gid yomi --no-create-home --shell /usr/sbin/nologin yomi

WORKDIR /app

# install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Voicevox Coreのアセット（ONNXランタイム、辞書、モデル）をダウンロード
# VOICEVOX_MODELS: ダウンロードする vvm のファイル名（空白区切りのパターン。既定は 0.vvm のみ。0.vvm は必ず含める）
# 例: docker compose build --build-arg VOICEVOX_MODELS="0.vvm 1.vvm 2.vvm"（"*" ですべて）
# 追加したモデルを使うには config.ini の VOICEVOX_EXTRA_MODELS も設定する（docs/hosting.md 参照）
ARG VOICEVOX_MODELS="0.vvm"
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
    mkdir -p /app/voicevox_core/models/vvms && \
    set -f && \
    for pattern in $VOICEVOX_MODELS; do \
        set +f && \
        rm -rf /tmp/vvm && \
        yes "y" | /tmp/download-linux -o /tmp/vvm --only models --models-pattern "$pattern" && \
        cp /tmp/vvm/models/vvms/*.vvm /app/voicevox_core/models/vvms/ && \
        cp /tmp/vvm/models/*.txt /app/voicevox_core/models/ || exit 1; \
    done && \
    test -f /app/voicevox_core/models/vvms/0.vvm && \
    rm -rf /tmp/vvm /tmp/download-linux

# copy application code（コードだけの変更ならここから下だけが再ビルドされる）
COPY --chown=yomi:yomi . .
RUN chmod 755 /app/docker-entrypoint.sh && chown yomi:yomi /app

# timezone mapping default
ENV TZ=Asia/Tokyo
# y.status に表示するバージョン（scripts/deploy.sh が docker compose build --build-arg GIT_SHA=<短いハッシュ> で渡す。
# 渡さなければ「不明」と表示する）。コードのコピーより後に置き、値が変わっても下の層だけが再ビルドされるようにする
ARG GIT_SHA=
ENV YOMI_KAI_VERSION=$GIT_SHA
# HEALTHCHECK（root で実行される）が yomi 所有のディレクトリに root 所有の .pyc を作らないようにする
ENV PYTHONDONTWRITEBYTECODE=1

# Discord に接続している間、Bot が log/heartbeat を30秒ごとに更新する。180秒以上更新がなければ unhealthy
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 CMD python -m yomi_kai.health || exit 1

# root で起動し、ボリュームの所有者を直してから yomi ユーザーで実行する（docker-entrypoint.sh）
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "yomi-KAI.py"]
