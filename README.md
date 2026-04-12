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

発言者と同じボイスチャンネルに接続します。

### y.dc

ボイスチャンネルから切断します。

### y.dict

辞書に関する操作です。詳しくは`y.dict help`を参照してください。

### y.sd

「突然の死」に関する操作です。詳しくは`y.sd help`を参照してください。

### y.help

このヘルプを表示します。

## 機能

- 辞書
- プレフィックスの変更
- 自動切断
- 突然の死

## 設定

`config.ini` から設定を変更できます。現在設定可能な項目は以下の通りです。

- プレフィックス
- Google TTSの使用切り替え (`USE_GOOGLE_TTS`)
- 自動切断までの時間

## 補足

- **VOICEVOXの利用とクレジット制限:** 本プログラムは読み上げエンジンとして「VOICEVOX」を利用しています。生成された音声を利用・公開する際は、必ず各キャラクター（ずんだもん、春日部つむぎ等）が定める利用規約を遵守し、動画などに「VOICEVOX:ずんだもん」等のクレジット表記を行ってください。
- 本プログラムをアップデートする際には、辞書データを手動で引き継いでください。辞書データの場所は `./dict/` です。
- **discord.py v1.7.3では動作しません。**

## サポート

サポートサーバーは[こちら](https://discord.gg/DWEQ2cP3KZ)。要望や質問はこのDiscordサーバーで受け付けています。**試用もできます。**

## 開発者向け情報

yomi-KAIの構造や使用している内部システム、ビルドスクリプトの仕組みなどについては、以下のドキュメントを参照してください。
- [技術スタックとアーキテクチャ・動作フロー (docs/architecture.md)](./docs/architecture.md)

## 作者

GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
