# yomi-KAI
![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/Garden-Tree/yomi-KAI?include_prereleases)
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
2. `tokens.py.example`を開き、中にDiscordのトークンとVoiceTextのAPIキーを入力。
3. `tokens.py`で名前をつけて保存。

## 使い方
`python3 yomi-KAI.py`

## コマンド
### yomi.c
発言者と同じボイスチャンネルに接続します。
### yomi.dc
ボイスチャンネルから切断します。

## 注意

## 作者
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)


# yomi-KAI

## Outline
yomi-KAI is a bot that reads sentences sent to the Discord text channel on the voice channel.

## Demo

## Features
Since you can run it on your own PC, it is more stable than other public readout bots.
## Required software
- Python 3.9 and above  
- ffmpeg 4.4 and above

## Required library
- discord.py[voice]  
- python-voicetext

## Installation
1. run `pip install -r requirements.txt` to install the library.
2. open `tokens.py.example` and enter Discord token and VoiceText API key in it.
3. name it `tokens.py` and save it.

## Usage
`python3 yomi-KAI.py`

## Commands
### yomi.c
Connect to the same voice channel as the speaker.
### yomi.dc
Disconnect from the voice channel.

## Note

## Author
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
