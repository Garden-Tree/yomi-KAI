# yomi-KAI

<img src="https://user-images.githubusercontent.com/57281730/133915187-dca595e9-bbb5-4c6b-9ef0-88a3d3d20385.png" width="256">

[![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/Garden-Tree/yomi-KAI?include_prereleases)](https://github.com/Garden-Tree/yomi-KAI/releases)
[![GitHub stars](https://img.shields.io/github/stars/Garden-Tree/yomi-KAI)](https://github.com/Garden-Tree/yomi-KAI/stargazers)
[![GitHub license](https://img.shields.io/github/license/Garden-Tree/yomi-KAI)](https://github.com/Garden-Tree/yomi-KAI/blob/main/LICENSE)

## 概要

yomi-KAIはDiscordのテキストチャンネルに送られた文章をボイスチャンネルで読み上げるbotです。  
自分のPC上で実行できるため、他の公開されている読み上げbotと比べて**負荷が集中しにくく、安定して動作**します。

## デモ

準備中

## 環境・推奨スペック

- Python 3.12以上
- FFmpeg 8.1以上

### 推奨スペック（Docker）
- CPU: 最低4コア（音声合成処理をCPUで行うため、なるべく多く割り当てることを推奨）
- メモリ: 512MB 以上
- ストレージ: 8GB 以上

## 依存ライブラリ

- [discord.py **2.0以上**](https://github.com/Rapptz/discord.py)
- [google-cloud-texttospeech](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-cloud-texttospeech)
- [voicevox_core](https://github.com/VOICEVOX/voicevox_core)

## 導入方法

### 事前準備 (全環境共通)

1. [Discord Developer Portal](https://discord.com/developers/applications) からbotを作成し、 `Privileged Gateway Intents` の権限をすべて付与します。
1. **(省略可: Googleの音声を使う場合のみ)** [Google Cloud Platform](https://console.cloud.google.com/)(GCP)でプロジェクトを作成し、[Cloud Text-to-Speech API](https://cloud.google.com/text-to-speech?hl=ja)を有効化してAPIキーを取得します。
1. 後述する各環境の手順でリポジトリ等を用意したのち、同梱されている `config.ini.example` をコピーして `config.ini` を作成し、Discordのトークンを入力して保存します（※GCPの音声を使用する場合は合わせて `USE_GOOGLE_TTS = True` に変更し、取得したAPIキーも入力します）。

### Windows

1. [Releases](https://github.com/Garden-Tree/yomi-KAI/releases/latest)から、音声モデルやFFmpeg等が全て同梱された `yomi-KAI-v***.zip` をダウンロードして解凍します。
1. 上記「事前準備」の通りに `config.ini` を作成し、設定します。

### Linux

1. リポジトリをクローンします。
1. Python 3.12以上、FFmpeg、portaudio19-devをインストールします。
1. [voicevox_core](https://github.com/VOICEVOX/voicevox_core/releases)からモデル・辞書・DLL等をダウンロードし、`voicevox_core/`フォルダに配置します。
1. `pip install -r requirements.txt` を実行します。
1. 上記「事前準備」の通りに `config.ini` を作成し、設定します。

### Docker

1. リポジトリをクローンするか、ソースコードをダウンロードして解凍します。
1. 上記「事前準備」の通りに `config.ini` を作成し、設定します。
1. ターミナルで `docker compose build` を実行してイメージを構築します（初回時は音声モデルのダウンロードや環境構築などが自動で行われます）。

## 起動方法

### Windows

`yomi-KAI.bat` を実行。
起動している間、コンソール画面は閉じないでください。

### Linux

`yomi-KAI.py` を実行。
起動している間、ターミナル画面は閉じないでください。
sshなどで接続している場合は、`nohup`コマンドなどを使ってバックグラウンドで実行してください。
```bash
nohup python3 yomi-KAI.py &
```

### Docker

ターミナルで以下のコマンドを実行します（バックグラウンドで起動）。
```bash
docker compose up -d
```
- 新しくイメージを構築（ビルド）し直して起動する場合: `docker compose up -d --build`
- ログを確認する: `docker compose logs -f`
- 終了（停止）する: `docker compose down`

## コマンド

※以下のコマンド例は、デフォルトのプレフィックスが `y.` に設定されている場合を想定しています（プレフィックスは `config.ini` から自由に変更可能です）。

### y.c

発言者と同じボイスチャンネルに接続します。表示されるドロップダウンから、サーバー全体の話者を選べます（選んだ話者は保存され、再起動後も引き継がれます）。

### y.dc

ボイスチャンネルから切断します。

### y.s

読み上げ中のメッセージを飛ばします。`y.s all` で、溜まっている未読み上げのメッセージもすべて飛ばします。

### y.voice

自分の発言を読み上げる話者を選ぶドロップダウンを表示します（ドロップダウンを操作した人の話者が変わります）。`y.voice reset` で個人設定を解除し、サーバーの話者に戻します。

### y.speed

自分の発言の読み上げ速度を設定します。`y.speed 1.5` のように 0.5〜2.0 倍の範囲で指定します。`y.speed` で現在の速度を表示し、`y.speed reset` で解除（1.0倍）します。

### y.announce

ボイスチャンネルへの入退室（「〇〇さんが入室しました」「〇〇さんが退室しました」）を読み上げるかをサーバー全体で設定します。`y.announce on` / `y.announce off` で切り替え、`y.announce` で現在の設定を表示します（既定はオン）。入退室はサーバーの話者・標準の速度で読み上げます。

### y.dict

辞書に関する操作です。詳しくは`y.dict help`を参照してください。

### y.sd

「突然の死」に関する操作です。詳しくは`y.sd help`を参照してください。

### y.help

このヘルプを表示します。

## 機能

- 辞書
- ユーザーごとの話者・読み上げ速度の設定
- 設定の保存（サーバーの話者・ユーザーごとの設定は再起動後も引き継がれます）
- 読み上げのスキップ
- ボイスチャンネルへの入退室の読み上げ（オン/オフ可能）
- 長文の省略（設定文字数を超えた分は「以下略」）
- コードブロックの省略（「コード省略」と読み上げ）
- プレフィックスの変更
- 自動切断
- 突然の死

## 設定

`config.ini` から設定を変更できます。現在設定可能な項目は以下の通りです。

- プレフィックス
- Google TTSの使用切り替え (`USE_GOOGLE_TTS`)
- 自動切断までの時間
- 読み上げる最大文字数 (`MAX_READ_LENGTH`、既定 100、0 で無制限)
- VOICEVOXの音声モデルをすべて読み込むか (`VOICEVOX_LOAD_ALL_MODELS`、既定 False)
- 切断通知の送信先 (`ALERT_WEBHOOK_URL`、既定は空 = 通知しない)
- 切断通知を送るまでの秒数 (`ALERT_AFTER`、既定 300、30 以上)

### 切断通知（任意）

Bot が Discord から一定時間（`ALERT_AFTER` 秒）以上切断されたままになると、指定した Discord チャンネルに「yomi-KAI が 5分以上 Discord から切断されています」と通知し、復旧したら停止していた期間を通知します。ネットワークが落ちている間は、送れるようになるまで繰り返し送信を試みます。

1. 通知を受け取りたいチャンネルの「チャンネルの編集」（歯車）→「連携サービス」→「ウェブフック」を開きます。
1. 「新しいウェブフック」を作成し、「ウェブフックURLをコピー」を押します。
1. `config.ini` に `ALERT_WEBHOOK_URL = コピーしたURL` を追加し、Bot を再起動します。

Webhook の URL を知っていれば誰でもそのチャンネルに投稿できるため、他人に見せないでください（ログにも URL は出力しません）。

> **ヘルスチェック（Docker）:** Bot は Discord に接続している間、`log/heartbeat` を30秒ごとに更新します。Docker 版では `python -m yomi_kai.health` がこれを確認し、180秒以上更新がなければコンテナの状態が `unhealthy` になります（`docker ps` で確認できます）。

> **VOICEVOXのモデルについて:** 既定では `voicevox_core/models/vvms/0.vvm`（ずんだもん・春日部つむぎ・四国めたん・雨晴はう）だけを読み込みます。他の `.vvm` も配置して使いたい場合は `config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を追加してください。モデルを増やすほどメモリ使用量と起動時間が増えます。また、ドロップダウンに表示できる話者は Discord の制限により25件までです。

## 補足

- **VOICEVOXの利用とクレジット制限:** 本プログラムは読み上げエンジンとして「VOICEVOX」を利用しています。生成された音声を利用・公開する際は、必ず各キャラクター（ずんだもん、春日部つむぎ等）が定める利用規約を遵守し、動画などに「VOICEVOX:ずんだもん」等のクレジット表記を行ってください。
- 本プログラムをアップデートする際には、辞書データと設定データを手動で引き継いでください。辞書データの場所は `./dict/`、話者・速度などの設定データの場所は `./settings/` です。
- **discord.py v1.7.3では動作しません。**

## サポート

サポートサーバーは[こちら](https://discord.gg/DWEQ2cP3KZ)。要望や質問はこのDiscordサーバーで受け付けています。**試用もできます。**

## 開発者向け情報

yomi-KAIの構造や使用している内部システム、ビルドスクリプトの仕組みなどについては、以下のドキュメントを参照してください。
- [技術スタックとアーキテクチャ・動作フロー (docs/architecture.md)](./docs/architecture.md)
- [機能追加案 (docs/improvement-proposals.md)](./docs/improvement-proposals.md)

### テスト・Lint

テストは Discord への接続や `voicevox_core` / `google-cloud-texttospeech` を必要としません。

```bash
pip install "discord.py[voice]" pytest ruff
pytest
ruff check .
```

GitHub Actions でも `main` / `develop` への push とプルリクエストごとに同じチェックが実行されます。

### リリース

Actions タブの「Release」からバージョンを入力して実行すると、Windows 版の ZIP を添付した下書きリリースが作成されます。リリースノートを清書・確認してから公開してください。詳しくは [docs/architecture.md の「リリース」](./docs/architecture.md) を参照してください。

## 作者

GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
