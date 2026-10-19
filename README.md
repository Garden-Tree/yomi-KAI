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

1. [Discord Developer Portal](https://discord.com/developers/applications) からbotを作成し、 `Privileged Gateway Intents` の権限をすべて付与します（メッセージを読み上げるため `MESSAGE CONTENT INTENT` は必須です）。
1. 「OAuth2」→「URL Generator」で、SCOPES に **`bot` と `applications.commands`** の両方にチェックを入れて招待URLを作成し、botをサーバーに招待します（`applications.commands` が無いとスラッシュコマンドが表示されません。導入済みの場合も、この招待URLで招待し直せば追加されます）。
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

コンテナ内の Bot は一般ユーザー（`yomi`、uid 1000）で動作します。`dict/` `settings/` `log/` の所有者は起動時に自動で調整されるため、以前のバージョンから更新する場合も操作は不要です。`config.ini` は uid 1000 から読めるようにしておいてください（`chmod 644 config.ini`。読めない場合は警告を出して root のまま動作します）。

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

### スラッシュコマンド

すべてのコマンドは、同じ名前のスラッシュコマンドとしても使えます（例: `y.c` → `/c`、`y.s all` → `/s mode:all`、`y.dict add 単語 読み` → `/dict add`）。入力欄に `/` を打つと説明付きの一覧が表示されます。スラッシュコマンドで個人設定（`/voice`、`/speed`）を操作したときの返信は、本人にだけ表示されます。

- スラッシュコマンドは起動時に Discord へ登録されます。初回や変更直後は、Discord のアプリに表示されるまで時間がかかることがあります（表示されない場合は Discord アプリを再起動してください）。
- botの招待時に `applications.commands` のスコープが必要です（「事前準備」参照）。

### y.c

発言者と同じボイスチャンネルに接続します。表示されるドロップダウンから、サーバー全体の話者を選べます（選んだ話者は保存され、再起動後も引き継がれます）。

### y.dc

ボイスチャンネルから切断します。

### y.s

読み上げ中のメッセージを飛ばします。`y.s all` で、溜まっている未読み上げのメッセージもすべて飛ばします。

### y.voice

自分の発言を読み上げる話者を選ぶドロップダウンを表示します（ドロップダウンを操作した人の話者が変わります）。`y.voice reset` で個人設定を解除し、サーバーの話者に戻します。

- `y.voice 名前`: 名前を指定して自分の話者を設定します。完全に一致する名前がなければ、前方一致・部分一致で1つに決まる場合にその話者を使います（例: `y.voice 春日部` → 春日部つむぎ）。候補が複数ある場合は候補を表示します。
- `y.voice list`: 使える話者の一覧を表示します（ドロップダウンには Discord の制限により25件までしか表示できないため、それより多い場合はこちらを使ってください）。

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
- 長文は文ごとに分けて並行に合成し、最初の文からすぐに読み上げを開始
- コードブロックの省略（「コード省略」と読み上げ）
- 添付ファイル・スタンプの読み上げ（「画像」「画像2枚」「動画」「ファイル」「スタンプ 名前」など）
- プレフィックスの変更
- スラッシュコマンド（`/c` など）
- Discord から長時間切断されたときの通知（Webhook）と、Docker のヘルスチェック
- 自動切断
- 突然の死

## 設定

`config.ini` から設定を変更できます。現在設定可能な項目は以下の通りです。

- プレフィックス
- Google TTSの使用切り替え (`USE_GOOGLE_TTS`)
- 自動切断までの時間
- 読み上げる最大文字数 (`MAX_READ_LENGTH`、既定 100、0 で無制限)
- VOICEVOXの音声モデルをすべて読み込むか (`VOICEVOX_LOAD_ALL_MODELS`、既定 False)
- 同時に音声合成する数 (`VOICEVOX_WORKERS`、1〜8、既定 1)
- 切断通知の送信先 (`ALERT_WEBHOOK_URL`、既定は空 = 通知しない)
- 切断通知を送るまでの秒数 (`ALERT_AFTER`、既定 300、30 以上)

> **VOICEVOXのモデルについて:** 既定では `voicevox_core/models/vvms/0.vvm`（ずんだもん・春日部つむぎ・四国めたん・雨晴はう）だけを読み込みます。他の `.vvm` も配置して使いたい場合は `config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を追加してください。モデルを増やすほどメモリ使用量と起動時間が増えます。また、ドロップダウンに表示できる話者は Discord の制限により25件までです（`y.voice 名前` なら25件目以降の話者も指定できます）。

> **同時に合成する数（`VOICEVOX_WORKERS`）:** 複数のサーバーで同時に読み上げたり、長文を文ごとに分けて先読みしたりするときに、VOICEVOX の音声を並行に合成する数です。増やすと待ち時間が短くなりますが、その数だけ音声モデルを読み込むためメモリを使います。目安（Windows・`0.vvm` のみで計測、合成を続けたあとの使用量）: 1（既定）で約280MB、2 で約530MB、4 で約890MB。推奨スペックのメモリ 512MB では既定の 1 のままにしてください。1 でも、長文は最初の文の合成が終わった時点で再生が始まり、合成中に Bot の他の処理が止まることもありません。`VOICEVOX_LOAD_ALL_MODELS = True` の場合は、読み込むモデルの数に比例してさらに増えます。

### 切断通知（任意）

Bot が Discord から一定時間（`ALERT_AFTER` 秒）以上切断されたままになると、指定した Discord チャンネルに「yomi-KAI が 5分以上 Discord から切断されています」と通知し、復旧したら停止していた期間を通知します。ネットワークが落ちている間は、送れるようになるまで繰り返し送信を試みます。

1. 通知を受け取りたいチャンネルの「チャンネルの編集」（歯車）→「連携サービス」→「ウェブフック」を開きます。
1. 「新しいウェブフック」を作成し、「ウェブフックURLをコピー」を押します。
1. `config.ini` に `ALERT_WEBHOOK_URL = コピーしたURL` を追加し、Bot を再起動します。

Webhook の URL を知っていれば誰でもそのチャンネルに投稿できるため、他人に見せないでください（ログにも URL は出力しません）。

> **ヘルスチェック（Docker）:** Bot は Discord に接続している間、`log/heartbeat` を30秒ごとに更新します。Docker 版では `python -m yomi_kai.health` がこれを確認し、180秒以上更新がなければコンテナの状態が `unhealthy` になります（`docker ps` で確認できます）。

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
