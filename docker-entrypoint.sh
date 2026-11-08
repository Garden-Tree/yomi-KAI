#!/bin/sh
# コンテナの起動処理: root で起動し、データ用ディレクトリの所有者を直してから一般ユーザー（yomi）で Bot を実行する。
#
# ボリューム（./dict ./settings ./log）は Docker がホスト側に root 所有で作るため、
# 一般ユーザーのままでは書き込めない。既存の環境でも手作業なしで動くよう、起動のたびにここで所有者を直す。
set -eu

APP_USER=yomi
APP_UID="$(id -u "$APP_USER")"
APP_GID="$(id -g "$APP_USER")"

if [ "$(id -u)" != "0" ]; then
    # 既に一般ユーザーで起動されている（docker run --user など）
    exec "$@"
fi

for dir in /app/dict /app/settings /app/log; do
    mkdir -p "$dir"
    # 所有者が違うものだけ直す（毎回すべてを書き換えない）
    find "$dir" \( ! -user "$APP_UID" -o ! -group "$APP_GID" \) -exec chown "$APP_UID:$APP_GID" {} +
done

drop() {
    setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups -- "$@"
}

# config.ini はホストから読み取り専用でマウントされる。一般ユーザーで読めない権限（600 など）なら、
# 動かなくなるのを避けるため従来どおり root のまま起動する
if [ -f /app/config.ini ] && ! drop test -r /app/config.ini; then
    echo "[entrypoint] config.ini を ${APP_USER} ユーザーで読めないため root のまま起動します（chmod 644 config.ini を推奨）" >&2
    exec "$@"
fi

exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --init-groups -- "$@"
