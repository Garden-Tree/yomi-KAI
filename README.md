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

## 環境

- Python 3.9以上
- FFmpeg 4.4以上

## 依存ライブラリ

- discord.py **2.0以上**
- python-voicetext

## 導入方法

### Windows

1. [Discord Developer Portal](https://discord.com/developers/applications)からbotを作成し、 `Privileged Gateway Intents` の権限をすべて付与。
1. [VoiceText Web API](https://cloud.voicetext.jp/webapi)に登録し、APIキーを受け取る。
1. [Google Cloud Platform](https://console.cloud.google.com/)(GCP)でプロジェクトを作成し、[Cloud Text-to-Speech API](https://cloud.google.com/text-to-speech?hl=ja)を有効化してサービスアカウントのキー(JSONファイル)を受け取る。
1. [Releases](https://github.com/Garden-Tree/yomi-KAI/releases/latest)から `yomi-KAI-v***.zip` をダウンロードして解凍。
1. `config.ini.example` を開き、DiscordのトークンVoiceTextのAPIキー、GCPのキーのディレクトリ(ファイル名も含む)を入力。
1. `config.ini` で名前をつけて保存。
1. `windows_setup.bat`を実行

### Linux

1. Windowsの6. まで同じ
1. Python, FFmpegをインストール
1. `pip install git+https://github.com/Rapptz/discord.py`
1. `pip install python-voicetext`
1. `pip install google-cloud-texttospeech`

## 起動方法

### Windows

`yomi-KAI.exe` を実行。

### Linux

`yomi-KAI.py` を実行。

## コマンド

### y.c

発言者と同じボイスチャンネルに接続します。

### y.dc

ボイスチャンネルから切断します。

### y.dict

辞書に関する操作です。詳しくは`y.dict help`を参照してください。

### y.help

このヘルプを表示します。

## 機能

- 辞書
- プレフィックスの変更
- 自動切断

## 設定

`config.ini` から設定を変更できます。現在設定可能な項目は以下の通りです。

- プレフィックス
- 話者（声質）
- ピッチ
- スピード

## 補足

- 本プログラムをアップデートする際には、辞書データを手動で引き継いでください。辞書データの場所は `./dict/` です。
- **discord.py v1.7.3では動作しません。**
- exeで起動した場合、pythonでの起動と比較して、若干の遅延が発生します。

## サポート

サポートサーバーは[こちら](https://discord.gg/DWEQ2cP3KZ)。要望や質問はこのDiscordサーバーで受け付けています。**試用もできます。**

## 支援

サーバー運営費等が毎月発生しています。皆様のご支援をお待ちしております。開発のモチベーションにも繋がります！  
[[Fantia]](https://fantia.jp/fanclubs/254049)

## 作者

GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
