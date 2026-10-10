#!/usr/bin/env bash
# 本番デプロイ: 指定コミットへ更新 → 再ビルド → 起動確認 → 失敗したら直前の版へロールバック。
#
#   使い方: scripts/deploy.sh <commit-sha>   （運用ディレクトリで実行）
#
# 環境変数:
#   HEALTH_TIMEOUT  起動確認の待ち時間（秒、既定 120）
#
# git reset でこのスクリプト自身が書き換わっても安全なよう、処理は main 関数にまとめ、
# ファイル末尾で呼び出す（bash はスクリプトを逐次読みするため）。

main() {
    set -euo pipefail

    local sha="${1:?usage: deploy.sh <commit-sha>}"
    local image="yomi-kai:latest"
    local previous_image="yomi-kai:previous"
    local container="yomi-kai"
    local ready_pattern="Launch complete!"
    local timeout="${HEALTH_TIMEOUT:-120}"

    log() { echo "[deploy] $*"; }

    # 起動完了のログが出るまで待つ。コンテナが止まった・時間切れなら失敗
    wait_healthy() {
        local since="$1" waited=0 state logs
        while (( waited < timeout )); do
            state="$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)"
            if [[ "$state" != "running" && "$state" != "restarting" ]]; then
                log "コンテナの状態が異常です: $state"
                return 1
            fi
            # パイプで grep -q すると、早期終了時の SIGPIPE が pipefail で失敗扱いになるので変数に受ける
            logs="$(docker logs --since "$since" "$container" 2>&1 || true)"
            if [[ "$logs" == *"$ready_pattern"* ]]; then
                log "起動を確認しました（${waited}秒）"
                return 0
            fi
            sleep 5
            waited=$(( waited + 5 ))
        done
        log "${timeout}秒以内に起動完了のログが出ませんでした"
        return 1
    }

    local prev_commit
    prev_commit="$(git rev-parse HEAD)"
    log "現在: ${prev_commit} → デプロイ対象: ${sha}"

    # CI が通ったコミットそのものに更新する（fast-forward のみ。ローカル変更があれば失敗して止まる）
    git fetch --quiet origin main
    git merge --ff-only --quiet "$sha"

    # ロールバック用に、今動いているコンテナのイメージを退避
    # （イメージ名ではなくコンテナから取る。compose の image 名を変えた初回でも退避できるように）
    local has_previous=0 current_image_id
    current_image_id="$(docker inspect -f '{{.Image}}' "$container" 2>/dev/null || true)"
    if [[ -n "$current_image_id" ]]; then
        docker tag "$current_image_id" "$previous_image"
        has_previous=1
    else
        log "動作中のコンテナがないため、ロールバック用のイメージは退避しません"
    fi

    # ビルドに失敗した場合は、コンテナは入れ替わらず旧版のまま動き続ける
    local started
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if ! docker compose up -d --build; then
        log "ビルドまたは起動に失敗しました。旧版のコンテナはそのまま動いています"
        git reset --hard --quiet "$prev_commit"
        return 1
    fi

    if wait_healthy "$started"; then
        docker image prune -f >/dev/null
        # 直近7日以内に使ったビルドキャッシュは残す（全部消すと毎回フルビルドになる）
        docker builder prune -f --filter until=168h >/dev/null
        log "デプロイ完了: ${sha}"
        return 0
    fi

    log "新しい版の起動に失敗しました。直近のログ:"
    docker logs --since "$started" --tail 50 "$container" 2>&1 || true

    if (( has_previous == 0 )); then
        log "退避したイメージがないためロールバックできません"
        return 1
    fi

    log "直前の版（${prev_commit}）にロールバックします"
    git reset --hard --quiet "$prev_commit"
    docker tag "$previous_image" "$image"
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker compose up -d --no-build --force-recreate
    if wait_healthy "$started"; then
        log "ロールバックしました。デプロイは失敗扱いにします"
    else
        log "ロールバック後も起動を確認できませんでした。手動で確認してください"
    fi
    return 1
}

main "$@"
