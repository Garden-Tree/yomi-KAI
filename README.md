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
1. `pip install -r requirements.txt`を実行してライブラリをインストール。
2. `settings.json.example`を開き、中にDiscordのトークンとVoiceTextのAPIキーを入力。
3. `settings.json`で名前をつけて保存。

## 使い方

`python3 yomi-KAI.py`

## コマンド
### yomi.c
発言者と同じボイスチャンネルに接続します。
### yomi.dc
ボイスチャンネルから切断します。
### yomi.dict
辞書に関する操作です。詳しくは`yomi.dict help`を参照してください。
### yomi.help
このヘルプを表示します。

## 機能
- 辞書
- プレフィックスの変更
- 自動切断

## 設定
`setting.json`が設定ファイルです。プレフィックスを変更することができます。

## 注意
- `PyAudio`をインストールする際に`portaudio`が見つからないというエラーが出ることがあります。その場合、Windowsならビルド済みの`PyAudio`をダウンロードして、ローカルからpip3でインストールしてください。Linuxなら、`portaudio19-dev`をyumやaptでインストールしてください。
- DiscordのBot設定の`Privileged Gateway Intents`の権限が必要です。[Discord Developer Portal](https://discord.com/developers/applications)から権限を付与してください。
- 本ブログラムで使用しているライブラリである`Discord.py`の開発が終了しています。そのため今後、本ブログラムも使えなくなる可能性があります。(使えなくなった場合は、`Discord.js`などでサポートは続けるつもりです。)
- 本プログラムをアップデートする際には、辞書データを手動で引き継いでください。辞書データの場所は`./dict/`です。

## 支援
サーバー運営費等が毎月発生しています。皆様のご支援をお待ちしております。開発のモチベーションにも繋がります！  
[[Fantia]](https://fantia.jp/fanclubs/254049)

## 作者
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
