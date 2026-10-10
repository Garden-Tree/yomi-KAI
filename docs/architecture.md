# yomi-KAI 開発者向けドキュメント：技術スタックとアーキテクチャ

このドキュメントでは、yomi-KAIの内部で利用されている技術や、処理の流れ、ポータブルな配布へのアプローチについて解説します。

## 1. コア技術・ライブラリ
| 分野 | 技術・ライブラリ | 説明 |
| --- | --- | --- |
| **言語** | Python 3.12 | アプリケーションのベース言語。モダンな文法と豊富なパッケージエコシステムを利用。 |
| **Discord Botフレームワーク** | discord.py (v2.0+) | Discord APIのラッパー。Voice機能の制御（`discord.FFmpegPCMAudio`）を利用し、VCへの音声ストリーミングを実行。また `discord.ui.Select` による話者変更のドロップダウンUI等にも利用。 |
| **合成エンジン (Local)** | voicevox_core | ローカルで完結する高品質な音声合成エンジン。ONNX RuntimeとOpen JTalk辞書を利用して高速に音声を生成。`.vvm` モデルファイルをロードし、ずんだもんや春日部つむぎ等の音声を使用。 |
| **合成エンジン (Cloud)** | google-cloud-texttospeech | GCPのAPIを利用した音声合成。`config.ini` の `GOOGLE_API_KEY`（APIキー）で認証し、Wavenet音声（`ja-JP-Wavenet-A` / `D`）を LINEAR16 (WAV) で生成（`USE_GOOGLE_TTS = True` のときのみ利用）。 |
| **メディア処理** | FFmpeg | 合成した WAV のバイト列を標準入力（`pipe=True`）で受け取り、Discordが要求するPCM形式に変換しながらストリーミングするために利用。一時ファイルは作らない。 |

## 2. コード構成

本体は `yomi_kai/` パッケージにあり、`yomi-KAI.py` は起動用の薄いスクリプトです。

```
yomi-KAI.py            # 起動スクリプト。lib/ 配下から起動された場合は親ディレクトリへ chdir し、
                       # lib/packages を sys.path に追加してから yomi_kai.main.main() を呼ぶ
yomi_kai/
  main.py              # Config・TTSエンジン・Bot を組み立てて起動（import 時の副作用なし）
  config.py            # config.ini を Config データクラスに読み込む。不備はキー名付きで報告し exit(1)
  logging_setup.py     # コンソール INFO / log/ 配下のファイル DEBUG。ログは最新50個を保持
  text.py              # 読み上げテキスト整形（純粋関数のみ。I/Oなし）
  dictionary.py        # DictionaryStore: dict/{guild_id}.json の読み込み・キャッシュ・アトミック書き込み
  session.py           # GuildSession: サーバーごとの接続・キュー・ワーカー・AFKタイマー
  ui.py                # 話者選択ドロップダウン（discord.ui.Select / View）
  tts/
    __init__.py        # TTSEngine プロトコル・Speaker・CompositeEngine（重いライブラリは import しない）
    voicevox.py        # VoicevoxEngine
    google.py          # GoogleEngine
  cogs/
    voice.py           # help / c / dc コマンド、on_message（読み上げ）、on_voice_state_update（自動切断）
    dictionary.py      # dict add / del / list / help
    misc.py            # sd（突然の死）
tests/                 # pytest（text / dictionary / session）
```

### 2.1 TTSエンジン
`TTSEngine` は `synthesize(text, speaker) -> bytes`（WAV）、`speakers() -> list[Speaker]`、`default_speaker` を持つプロトコルです。

* **VoicevoxEngine**: ONNX Runtime と Open JTalk 辞書を起動時に一度だけ読み込み、既定では `models/vvms/0.vvm` のみをロードします（`config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を書くと `*.vvm` をすべてロード）。話者一覧はモデルのメタ情報（`VoiceModelFile.metas`）から構築し、ノーマルスタイルはキャラクター名（例: `ずんだもん`、`春日部つむぎ`）、その他のスタイルは `名前（スタイル名）` のラベルになります。ずんだもん・春日部つむぎは常に先頭に並び、Discord の制限によりドロップダウンには最大25件まで表示されます。
* **GoogleEngine**: `USE_GOOGLE_TTS = True` の場合のみ構築され、`CompositeEngine` で VOICEVOX と束ねられます（初期話者は `ja-JP-Wavenet-A`）。
* `voicevox_core` / `google-cloud-texttospeech` は `main.build_engine()` の中で遅延 import されるため、テストや CI はこれらをインストールせずに実行できます。

### 2.2 サーバーごとのワーカーモデル
サーバーごとの状態はすべて `GuildSession` が持ち、`VoiceCog.sessions: dict[int, GuildSession]`（キーは `guild.id`）で管理します。モジュールレベルの可変状態はありません。

* **接続 (`c`)**: ボイスクライアントと読み上げ対象のテキストチャンネルを `session.start()` に渡し、ワーカータスクを起動、AFKタイマーを開始します（発言がなくても `AFK_TIME` 秒後に自動切断）。
* **読み上げ**: `on_message` は整形済みテキストを `session.enqueue()` で `asyncio.Queue` に積むだけです（このとき選択中の話者も一緒に記録し、AFKタイマーを張り直す）。
* **ワーカー**: 1サーバーにつき1本。キューから1件取り出し `asyncio.to_thread` で合成 → 再生、を繰り返します。再生中に次の1件だけ先行して合成しますが、再生順は必ずキュー投入順です。FFmpeg のソースは `voice_client.play()` の直前に生成し、再生完了の `after` コールバックは `loop.call_soon_threadsafe` でイベントループ側の `asyncio.Event` をセットしてワーカーに制御を戻します。
* **切断**: `dc` コマンド・Botのキック・AFKタイムアウト・VCが無人になった場合のいずれも `session.close()` を通り、ワーカー停止 → キュー破棄 → 再生停止 → 切断を行います。話者の選択はセッションに残るため、再接続しても引き継がれます（再起動では初期化されます）。

## 3. アプリケーションの動作フロー
1. **テキスト受信**: `VoiceCog.on_message` でテキストをフック（コマンド用のプレフィックスから始まるもの、Bot・DM・読み上げ対象外チャンネルの発言は除外）。
2. **辞書置換**: `dict/{guild.id}.json` の内容を `DictionaryStore` が初回のみスレッドで読み込み、コンパイル済み正規表現と一緒にメモリへキャッシュ（`dict add` / `dict del` で破棄）。長い単語を優先して読み仮名に置換。
3. **前処理**: コードブロック（「コード省略」に置換、インラインコードは中身だけ残す）、URL、ネタバレタグ(`\|\|...\|\|`)、メンション(`<@...>` / `<@&...>` / `<#...>`)、カスタム絵文字、簡易なマークダウンを置換（`text.build_read_text`）。`*強調*` は記号の内側が空白でない場合のみ、`_強調_` は英数字に挟まれていない場合のみ除去するため、`snake_case_name` や `2 * 3 * 4` はそのまま読まれます。最後に `MAX_READ_LENGTH` 文字を超えた分を切り詰めて「以下略」を付けます。
4. **音声合成・再生**: 上記のワーカーが、選択された話者に応じたエンジンでメモリ上に WAV を生成し、FFmpeg 経由で順番にVCへ送信。
5. **AFK監視**: 接続時および読み上げのたびにタイマーを張り直し、`AFK_TIME` 秒間読み上げがない場合や、VCにBot以外誰もいなくなった場合は自動でボイスチャンネルから切断。

ログはコンソールに INFO 以上、`log/` 配下のファイルに DEBUG 以上を出力します（読み上げた本文はファイルにのみ DEBUG で記録）。

## 4. デプロイと配布アーキテクチャ

多様な環境で「面倒な環境構築なしに、誰でもすぐに動かせる」ことに重きを置いて設計されています。

### 4.1 完全ポータブルなWindows版 (`build.py`)
Python環境の構築やコマンド操作をユーザーに強いることなく配布するため、標準のPyInstallerなどに頼らず **独自のマルチビルドスクリプト** を使用しています。
* `Python Embeddable` をダウンロードし、必要な `site-packages` と `pip` を強制展開。
* バックグラウンドで `ffmpeg-essentials` をダウンロードし解凍。
* `voicevox-downloader.exe` を叩き、エンジン、OpenJTalk辞書、および基本的なキャラクターが含まれるモデルファイル (`0.vvm` 等) を抽出。
* 起動スクリプト `yomi-KAI.py` と `yomi_kai/` パッケージを `lib/` にコピー（`__pycache__` は除外）。
* 全てをローカルの一つのフォルダにまとめ、ダブルクリックで動く相対パス起動の `yomi-KAI.bat` を同封してZIP化。

これにより、エンドユーザーはZIPを解凍して `bat` を実行するだけで、一切の環境制約を受けずに利用できます。

### 4.2 自己ホスト向けのDocker版
Linuxサーバー、NAS、クラウドインスタンス等向けに、`docker-compose` での一発構築を提供しています。
軽量な `python:3.12-slim-bookworm` をベースに設計しており、コンテナビルド時にLinux（x86_64, arm64等）向けの `voicevox-downloader` を自動で叩き、ONNX Runtimeとモデルを準備します。ユーザー設定(`config.ini`)や辞書(`dict/`)、各種ログ(`log/`)はコンテナ外のホスト側に紐付けられ、永続化されるモダンな構成を実現しています。
