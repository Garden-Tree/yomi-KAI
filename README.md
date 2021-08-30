# yomi-KAI
[![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/Garden-Tree/yomi-KAI?include_prereleases)](https://github.com/Garden-Tree/yomi-KAI/releases)
[![GitHub stars](https://img.shields.io/github/stars/Garden-Tree/yomi-KAI)](https://github.com/Garden-Tree/yomi-KAI/stargazers)
[![GitHub license](https://img.shields.io/github/license/Garden-Tree/yomi-KAI)](https://github.com/Garden-Tree/yomi-KAI/blob/main/LICENSE)

## 概要
yomi-KAIはDiscordのテキストチャンネルに送られた文章をボイスチャンネルで読み上げるbotです。

## デモ

## 特徴
自分のPC上で実行できるため、他の公開されている読み上げbotと比べて負荷が集中しにくく、安定性が高いです。

## 必要なソフトウェア
- Python 3.9 以上
- ffmpeg 4.4 以上

## 必要なライブラリ
- discord.py[voice]
- python-voicetext

## インストール
1. `pip3 install -r requirements.txt`を実行してライブラリをインストール。
2. `tokens.py.example`を開き、中にDiscordのトークンとVoiceTextのAPIキーを入力。
3. `tokens.py`で名前をつけて保存。

## 使い方
`python3 yomi-KAI.py`

## コマンド
### yomi.c
発言者と同じボイスチャンネルに接続します。
### yomi.dc
ボイスチャンネルから切断します。
### yomi.help
このヘルプを表示します。

## 注意
- `PyAudio`をインストールする際に`portaudio`が見つからないというエラーが出ることがあります。その場合、Windowsならビルド済みの`PyAudio`をダウンロードして、ローカルからpip3でインストールしてください。Linuxなら、`portaudio`をyumやaptでインストールしてください。
- DiscordのBot設定の`Privileged Gateway Intents`の権限が必要です。[Discord Developer Portal](https://discord.com/developers/applications)から権限を付与してください。

## 作者
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
