# yomi-KAI
## 概要
yomi-KAIはDiscordのテキストチャンネルに送られた文章をボイスチャンネルで読み上げるbotです。
## デモ
## 特徴
## 必要なソフトウェア
- Python 3.9 以上  
- ffmpeg 4.4 以上
## 必要なライブラリ
- discord.py[voice]  
- python-voicetext
## インストール
`pip install -r requirements.txt`を実行してライブラリをインストールしてください。  
次に、tokens.py.exampleのファイル名から.exampleを削除して、中にトークンを入力して上書き保存してください。
## 使い方
`python3 yomi-KAI.py`で起動できます。  
ボイスチャンネルに接続した状態でテキストチャンネルに`yomi.c`と打つとyomi-KAIがボイスチャットに接続し、読み上げを開始します。  
切断したい場合は`yomi.dc`と打ってください。
## 注意
## 作者
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
## ライセンス

# yomi-KAI
## Outline
yomi-KAI is a bot that reads sentences sent to the Discord text channel on the voice channel.
## Demo
## Features
## Required software
- Python 3.9 and above  
- ffmpeg 4.4 and above
## Required library
- discord.py[voice]  
- python-voicetext
## Installation
Run `pip install -r requirements.txt` to install the library.  
Next, delete the .example from the file name of tokens.py.example, enter the token inside and save the file overwritten.
## Usage
You can start it with `python3 yomi-KAI.py`.  
If you are connected to the voice channel and type `yomi.c` in the text channel, yomi-KAI will connect to the voice chat and start reading out loud.  
If you want to disconnect it, type `yomi.dc`.
## Note
## Author
GardenTree [[Twitter]](https://twitter.com/Garden__Tree)
## Licence
