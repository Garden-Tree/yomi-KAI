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
- メモリ: 512MB 以上（音声合成の同時実行数 `VOICEVOX_WORKERS` を増やす場合は、後述の「音声合成の同時実行数」の目安に合わせて増やしてください）
- ストレージ: 8GB 以上（Docker のイメージとビルドキャッシュを含めると 16GB あると安心です）

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

コンテナ内の Bot は一般ユーザー（`yomi`、uid 1000）で動作します。`dict/` `settings/` `log/` `usage/` の所有者は起動時に自動で調整されるため、以前のバージョンから更新する場合も操作は不要です。`config.ini` は uid 1000 から読めるようにしておいてください（`chmod 644 config.ini`。読めない場合は警告を出して root のまま動作します）。

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

すべてのコマンドは、同じ名前のスラッシュコマンドとしても使えます（例: `y.c` → `/c`、`y.s all` → `/s mode:all`、`y.dict add 単語 読み` → `/dict add`）。入力欄に `/` を打つと説明付きの一覧が表示されます。スラッシュコマンドで個人設定（`/voice`、`/speed`、`/pitch`、`/mydict`）を操作したときの返信は、本人にだけ表示されます。

- スラッシュコマンドは起動時に Discord へ登録されます。初回や変更直後は、Discord のアプリに表示されるまで時間がかかることがあります（表示されない場合は Discord アプリを再起動してください）。
- botの招待時に `applications.commands` のスコープが必要です（「事前準備」参照）。

### コマンド一覧

| コマンド | 内容 | 権限 |
| --- | --- | --- |
| `y.c` / `y.dc` | ボイスチャンネルに接続 / 切断 | |
| `y.s` / `y.s all` | 読み上げを飛ばす / 溜まっている分もすべて飛ばす | |
| `y.voice` / `y.speed` / `y.pitch` | 自分の話者 / 読み上げ速度 / 声の高さ | |
| `y.announce on\|off` | 入退室を読み上げる（既定オン） | |
| `y.readname on\|off` | 発言者の名前を読み上げる（既定オフ） | |
| `y.speedup on\|off` | 読み上げ待ちが溜まったら速く読む（既定オン） | |
| `y.dict add\|del\|list\|export` | 辞書の登録・削除・一覧・書き出し | |
| `y.dict import` | 添付した JSON の辞書を読み込む | サーバー管理 |
| `y.mydict add\|del\|list` | 自分の発言だけに使う個人辞書 | |
| `y.read url\|emoji\|code\|spoiler on\|off` / `y.read list` | URL・サーバー絵文字・コードブロック・ネタバレの読み方 | 変更はサーバー管理 |
| `y.ignore add\|del @ユーザー\|@ロール` / `y.ignore list` | 読み上げない人・ロール | 登録・解除はサーバー管理 |
| `y.autojoin add\|del` / `y.autojoin list` | VC に人が入ったら自動で接続 | 登録・解除はサーバー管理 |
| `y.readbots on\|off` | ほかの Bot・Webhook の発言も読み上げる（既定オフ） | サーバー管理 |
| `y.status` / `y.plan` | Bot の動作状況 / このサーバーの上限 | |
| `y.credit` / `y.sd` / `y.help` | クレジット / 突然の死 / ヘルプ | |

「サーバー管理」は Discord のロールの権限「サーバー管理」（Manage Server）です。権限がない場合は案内が表示されます（スラッシュコマンドでは本人にだけ表示）。

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

### y.pitch

自分の発言の声の高さを設定します。`y.pitch 3` のように -10〜+10 の整数で指定します（0 が標準、+10 で約2.6半音高く、-10 で約2.6半音低くなります）。`y.pitch` で現在の高さを表示し、`y.pitch reset` で解除します。Google TTS の話者でも同じくらい高さが変わります。

### y.announce

ボイスチャンネルへの入退室（「〇〇さんが入室しました」「〇〇さんが退室しました」）を読み上げるかをサーバー全体で設定します。`y.announce on` / `y.announce off` で切り替え、`y.announce` で現在の設定を表示します（既定はオン）。入退室はサーバーの話者・標準の速度で読み上げます。

### y.dict

辞書に関する操作です。詳しくは`y.dict help`を参照してください。

- `y.dict add 単語 読み`: 単語は 50 文字まで、読みは 100 文字までです。空（空白だけを含む）の単語・読みは登録できません。`y.dict import` で読み込むファイルにも同じ制限があります。

- `y.dict export`: サーバーの辞書を `dict-サーバーID.json` として書き出します（辞書ファイルと同じ形式）。
- `y.dict import`: JSON ファイル（100KB まで、`{"単語": "読み", ...}`）を添付して実行すると、辞書に読み込みます。同じ単語はファイルの読みで上書きします。辞書の登録数に上限（`MAX_DICT_ENTRIES`）がある場合は入る分だけ追加し、追加できなかった件数を表示します。スラッシュコマンドでは `attachment` にファイルを指定します。「サーバー管理」の権限が必要です。

### y.mydict

自分の発言だけに使う個人辞書です（サーバー内で自分だけに効きます。サーバーの辞書より先に使います）。返信は本人にだけ表示されます（スラッシュコマンドの場合）。

- `y.mydict add 単語 読み`: 登録します（単語・読みの長さの制限は `y.dict add` と同じ）。登録数に上限（`MAX_USER_DICT_ENTRIES`）がある場合は `y.plan` で確認できます。
- `y.mydict del 単語`: 削除します。
- `y.mydict list`: 自分の個人辞書を表示します。

### y.read

URL・サーバー絵文字・コードブロック・ネタバレ（`||...||`）の読み方をサーバー全体で切り替えます。`y.read list`（または引数なしの `y.read`）で現在の設定を表示し、`y.read url` のように対象だけを指定するとその設定を表示します。変更には「サーバー管理」の権限が必要です。

| コマンド | on | off |
| --- | --- | --- |
| `y.read url on\|off` | 「URL」と読む（既定） | 読まない |
| `y.read emoji on\|off` | サーバー絵文字の名前を読む（既定） | 読まない |
| `y.read code on\|off` | コードブロックの中身を読む | 「コード省略」と読む（既定） |
| `y.read spoiler on\|off` | ネタバレの中身を読む | 「ネタバレ」と読む（既定） |

### y.readname

`y.readname on` で、発言の前に発言者の名前（「アリス、こんにちは」）を読み上げます（サーバー全体、既定はオフ）。同じ人が30秒以内に続けて発言した場合は名前を省きます。読み上げる最大文字数（`MAX_READ_LENGTH`）は本文だけに適用されます。

### y.speedup

読み上げ待ちのメッセージが3件以上溜まると1.25倍、6件以上で1.5倍の速さで読み上げます（最大2.0倍、サーバー全体、既定はオン）。溜まっていなければ通常の速さです。`y.speedup off` で無効にできます。

### y.ignore（サーバー管理者向け）

`y.ignore add @ユーザー` / `y.ignore add @ロール` で、そのユーザーの発言、またはそのロールを持つメンバーの発言を読み上げないようにします（それぞれ25件まで）。`y.ignore del` で解除、`y.ignore list` で一覧を表示します。

### y.autojoin（サーバー管理者向け）

ボイスチャンネルに入った状態で、読み上げたいテキストチャンネルで `y.autojoin add` を実行すると、以後そのボイスチャンネルに誰かが入ったとき Bot が自動で接続し、そのテキストチャンネルを読み上げます（5件まで）。`y.autojoin del` でいまいるボイスチャンネル（登録が1件だけならそれ）の自動接続を解除し、`y.autojoin list` で一覧を表示します。自動切断した直後の30秒間は自動接続しません。

### y.readbots（サーバー管理者向け）

`y.readbots on` で、ほかの Bot や Webhook の発言も読み上げます（既定はオフ。この Bot 自身の発言は読み上げません）。

### y.status

Discord の応答時間、接続中のボイスチャンネルの数、このサーバーの読み上げ待ちの件数、音声合成の状態、読み上げの開始までの時間（メッセージを受け取ってから再生を始めるまでの中央値・95パーセンタイル・件数。全サーバーの直近の読み上げから集計）、稼働時間、バージョンを表示します。

### y.plan

このサーバーの上限（1メッセージで読み上げる文字数、サーバー辞書と自分の個人辞書の登録数、使える話者の数）を表示します。上限は `config.ini` で決まり、既定ではすべて無制限です（読み上げる文字数のみ既定 100）。公開版でプレミアムプランを有効にしている場合は、このサーバーのプランも表示します。

### y.sd

「突然の死」に関する操作です。詳しくは`y.sd help`を参照してください。config.ini で `ENABLE_SUDDEN_DEATH = False` にすると、このコマンドは使えなくなります（docs/hosting.md 参照）。

### y.credit

読み上げに使っている音声のクレジット（`VOICEVOX:ずんだもん` など、使える話者のキャラクターごとに1行）と、VOICEVOX の利用規約へのリンクを表示します。Google TTS を有効にしている場合は、その旨も表示します。

### y.help

このヘルプを表示します。

## 機能

- 辞書
- ユーザーごとの話者・読み上げ速度・声の高さの設定
- 個人辞書（自分の発言だけに使う辞書）
- URL・サーバー絵文字・コードブロック・ネタバレの読み方の切り替え（サーバーごと）
- 再起動後の自動再接続（再起動の前に接続していたボイスチャンネルに人がいれば接続し直す）
- 発言者の名前の読み上げ（連投では省略）・読み上げない人やロールの設定・Bot の発言の読み上げ（いずれもオン/オフ可能）
- ボイスチャンネルへの自動接続
- 読み上げ待ちが溜まったときの早読み
- 辞書の書き出し・読み込み（JSON）
- 動作状況の表示（`y.status`）
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
- 音声のクレジット表示（`y.credit`）
- 多くのサーバーに公開する場合の機能（[docs/hosting.md](./docs/hosting.md) を参照）

## 設定

`config.ini` から設定を変更できます。現在設定可能な項目は以下の通りです。

- プレフィックス
- Google TTSの使用切り替え (`USE_GOOGLE_TTS`)
- 自動切断までの時間
- 読み上げる最大文字数 (`MAX_READ_LENGTH`、既定 100、0 で無制限)
- VOICEVOXの音声モデルをすべて読み込むか (`VOICEVOX_LOAD_ALL_MODELS`、既定 False)
- 話者を増やすVOICEVOXの音声モデル (`VOICEVOX_EXTRA_MODELS`、既定は空 = 0.vvm のみ)。同時に読み込んでおく数 (`VOICEVOX_MAX_EXTRA_MODELS`、既定 1)、解放するまでの秒数 (`VOICEVOX_MODEL_IDLE`、既定 600)
- 同時に音声合成する数 (`VOICEVOX_WORKERS`、1〜8、既定 1)
- 切断通知の送信先 (`ALERT_WEBHOOK_URL`、既定は空 = 通知しない)
- 切断通知を送るまでの秒数 (`ALERT_AFTER`、既定 300、30 以上)
- 読み上げた本文をログに記録するか (`LOG_READ_TEXT`、既定 False = 記録しない)

多くのサーバーに公開する場合の設定（辞書・個人辞書の上限・使える話者の制限・プレミアムプラン・ブラックリスト・使用量の記録）は [公開版（ホスティング）の運用 (docs/hosting.md)](./docs/hosting.md) を参照してください。

### ログ

- メッセージの本文は、既定ではログに記録しません（文字数だけを記録します）。調査のために本文を見たい場合だけ `LOG_READ_TEXT = True` にしてください。
- 月ごとの使用量（読み上げた文字数とメッセージ数）の記録は、既定では行いません。必要な場合は `RECORD_USAGE = True` にしてください（[docs/hosting.md](./docs/hosting.md) の「使用量の確認」を参照）。

> **VOICEVOXのモデルについて:** 既定では `voicevox_core/models/vvms/0.vvm`（ずんだもん・春日部つむぎ・四国めたん・雨晴はう）だけを読み込みます。他の `.vvm` も配置して使いたい場合は、次のどちらかを `config.ini` に追加してください。また、ドロップダウンに表示できる話者は Discord の制限により25件までです（`y.voice 名前` なら25件目以降の話者も指定できます）。
>
> - `VOICEVOX_EXTRA_MODELS = 1.vvm, 2.vvm`（`*` で 0.vvm 以外のすべて）: 話者の一覧には起動時から出ますが、モデルはその話者で初めて読み上げるときに読み込みます。メモリを節約できる代わりに、最初の1文だけ1〜2秒遅れます。同時に読み込んでおくのは `VOICEVOX_MAX_EXTRA_MODELS` 個（既定 1）までで、`VOICEVOX_MODEL_IDLE` 秒（既定 600）使われなければ解放します。
> - `VOICEVOX_LOAD_ALL_MODELS = True`: すべての `.vvm` を起動時に読み込みます（従来どおり）。モデルを増やすほどメモリ使用量と起動時間が増えます。
>
> 配布版（Windows の ZIP）と Docker イメージには `0.vvm` だけが入っています。他の `.vvm` は [voicevox_vvm](https://github.com/VOICEVOX/voicevox_vvm) などから入手し、配布版は `lib/voicevox_core/models/vvms/`、ソースから動かす場合は `voicevox_core/models/vvms/` に置いてください（Docker は [docs/hosting.md](./docs/hosting.md) 参照）。キャラクターごとに利用規約が違うため、使う前に必ず確認してください。

### 音声合成の同時実行数（`VOICEVOX_WORKERS`）

VOICEVOX の音声を同時に何件まで合成するかを決める設定です。この数だけ音声合成エンジンを用意するため、増やすほど速くなる代わりにメモリを多く使います。

| `VOICEVOX_WORKERS` | メモリ使用量の目安 | 向いている使い方 |
| --- | --- | --- |
| 1（既定） | 約 280MB | 1〜2 サーバーで使う。メモリ 512MB の環境 |
| 2 | 約 530MB | 複数のサーバーで同時に読み上げることが多い |
| 4 | 約 890MB | 多くのサーバーで常に読み上げている |

- メモリの値は、Windows で `0.vvm` のみを読み込み、しばらく合成を続けたあとの使用量です。Linux / Docker では多少変わります。`VOICEVOX_LOAD_ALL_MODELS = True` の場合は、読み込むモデルの数に比例してさらに増えます（1つあたり約150〜200MB）。`VOICEVOX_EXTRA_MODELS` のモデルは、読み込んでいる間だけ同じだけ増えます。
- 1 のままでも、長文は文ごとに区切って合成するため、最初の文ができた時点で読み上げが始まります。合成中に Bot のほかの処理が止まることもありません。増やして効果が大きいのは、複数のサーバーで同時に読み上げる場合です。
- CPU のスレッドは、合成エンジンの数で等分して割り当てます（例: 8 スレッドの CPU で 2 にすると、1 エンジンあたり 4 スレッド）。CPU のコア数より大きくしても速くはなりません。
- 起動時のログに、エンジンの数と 1 エンジンあたりのスレッド数が表示されます。

長文の区切り方: 改行と「。」「！」「？」で区切り、「、」は一文が長いときだけ区切ります（「、」で毎回区切ると、区切りごとに抑揚が付け直されて不自然になるため）。「すごい！」と言った。のように、かぎかっこの直後に文が続く場合は区切りません。

### 切断通知（任意）

Bot が Discord から一定時間（`ALERT_AFTER` 秒）以上切断されたままになると、指定した Discord チャンネルに「yomi-KAI が 5分以上 Discord から切断されています」と通知し、復旧したら停止していた期間を通知します。ネットワークが落ちている間は、送れるようになるまで繰り返し送信を試みます。

1. 通知を受け取りたいチャンネルの「チャンネルの編集」（歯車）→「連携サービス」→「ウェブフック」を開きます。
1. 「新しいウェブフック」を作成し、「ウェブフックURLをコピー」を押します。
1. `config.ini` に `ALERT_WEBHOOK_URL = コピーしたURL` を追加し、Bot を再起動します。

Webhook の URL を知っていれば誰でもそのチャンネルに投稿できるため、他人に見せないでください（ログにも URL は出力しません）。

> **ヘルスチェック（Docker）:** Bot は Discord に接続している間、`log/heartbeat` を30秒ごとに更新します。Docker 版では `python -m yomi_kai.health` がこれを確認し、180秒以上更新がなければコンテナの状態が `unhealthy` になります（`docker ps` で確認できます）。

## 補足

- **VOICEVOXの利用とクレジット制限:** 本プログラムは読み上げエンジンとして「VOICEVOX」を利用しています。生成された音声を利用・公開する際は、必ず各キャラクター（ずんだもん、春日部つむぎ等）が定める利用規約を遵守し、動画などに「VOICEVOX:ずんだもん」等のクレジット表記を行ってください。
- 本プログラムをアップデートする際には、辞書データと設定データを手動で引き継いでください。辞書データの場所は `./dict/`、話者・速度などの設定データの場所は `./settings/`、使用量の記録（`RECORD_USAGE = True` の場合のみ）の場所は `./usage/` です。
- **保存するデータについて:** Bot が保存するのは、サーバー・ユーザーの ID、ユーザーごとの話者・速度の設定、サーバーの辞書です（`RECORD_USAGE = True` の場合は、月ごとの読み上げ文字数・メッセージ数も保存します）。メッセージの本文は保存しません（`LOG_READ_TEXT = True` にした場合のみログファイルに記録されます）。詳しくは [docs/hosting.md](./docs/hosting.md) の「保存するデータ」を参照してください。
- **discord.py v1.7.3では動作しません。**

## サポート

サポートサーバーは[こちら](https://discord.gg/DWEQ2cP3KZ)。要望や質問はこのDiscordサーバーで受け付けています。**試用もできます。**

## 開発者向け情報

yomi-KAIの構造や使用している内部システム、ビルドスクリプトの仕組みなどについては、以下のドキュメントを参照してください。
- [技術スタックとアーキテクチャ・動作フロー (docs/architecture.md)](./docs/architecture.md)
- [公開版（ホスティング）の運用 (docs/hosting.md)](./docs/hosting.md)
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
