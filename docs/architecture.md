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
  jsonfile.py          # JSON の読み込みとアトミック書き込み（DictionaryStore / SettingsStore で共有）
  dictionary.py        # DictionaryStore: dict/{guild_id}.json の読み込み・キャッシュ・アトミック書き込み
  settings.py          # SettingsStore: settings/{guild_id}.json（話者・速度）と resolve_voice（話者・速度の決定）
  session.py           # GuildSession: サーバーごとの接続・キュー・ワーカー・AFKタイマー
  ui.py                # 話者選択ドロップダウン（discord.ui.Select / View）
  tts/
    __init__.py        # TTSEngine プロトコル・Speaker・CompositeEngine（重いライブラリは import しない）
    voicevox.py        # VoicevoxEngine
    google.py          # GoogleEngine
  cogs/
    voice.py           # help / c / dc / s コマンド、on_message（読み上げ）、on_voice_state_update（自動切断）
    settings.py        # voice / speed（ユーザーごとの話者・速度）
    dictionary.py      # dict add / del / list / help
    misc.py            # sd（突然の死）
tests/                 # pytest（text / dictionary / settings / session / tts）
```

### 2.1 TTSエンジン
`TTSEngine` は `synthesize(text, speaker, speed=1.0) -> bytes`（WAV）、`speakers() -> list[Speaker]`、`default_speaker` を持つプロトコルです。`speed` は話速の倍率です。

* **VoicevoxEngine**: ONNX Runtime と Open JTalk 辞書を起動時に一度だけ読み込み、既定では `models/vvms/0.vvm` のみをロードします（`config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を書くと `*.vvm` をすべてロード）。話者一覧はモデルのメタ情報（`VoiceModelFile.metas`）から構築し、ノーマルスタイルはキャラクター名（例: `ずんだもん`、`春日部つむぎ`）、その他のスタイルは `名前（スタイル名）` のラベルになります。ずんだもん・春日部つむぎは常に先頭に並び、Discord の制限によりドロップダウンには最大25件まで表示されます。速度が 1.0 なら `Synthesizer.tts()`、それ以外は `create_audio_query()` → `speed_scale` を設定 → `synthesis()` で合成します。
* **GoogleEngine**: `USE_GOOGLE_TTS = True` の場合のみ構築され、`CompositeEngine` で VOICEVOX と束ねられます（初期話者は `ja-JP-Wavenet-A`）。速度は `AudioConfig.speaking_rate` で指定します。
* **CompositeEngine**: 話者名で担当エンジンへ振り分け、`speed` はそのまま渡します。
* `voicevox_core` / `google-cloud-texttospeech` は `main.build_engine()` の中で遅延 import されるため、テストや CI はこれらをインストールせずに実行できます。

### 2.2 サーバーごとのワーカーモデル
サーバーごとの状態はすべて `GuildSession` が持ち、`VoiceCog.sessions: dict[int, GuildSession]`（キーは `guild.id`）で管理します。モジュールレベルの可変状態はありません。

* **接続 (`c`)**: ボイスクライアントと読み上げ対象のテキストチャンネルを `session.start()` に渡し、ワーカータスクを起動、AFKタイマーを開始します（発言がなくても `AFK_TIME` 秒後に自動切断）。
* **読み上げ**: `on_message` は発言者の話者・速度を `resolve_voice()` で決め（2.3節）、整形済みテキストと一緒に `session.enqueue(text, speaker, speed)` で `asyncio.Queue` に積むだけです（AFKタイマーも張り直す）。セッション自体は話者を持ちません。
* **ワーカー**: 1サーバーにつき1本。キューから1件取り出し `asyncio.to_thread` で合成 → 再生、を繰り返します。再生中に次の1件だけ先行して合成しますが、再生順は必ずキュー投入順です。FFmpeg のソースは `voice_client.play()` の直前に生成し、再生完了の `after` コールバックは `loop.call_soon_threadsafe` でイベントループ側の `asyncio.Event` をセットしてワーカーに制御を戻します。
* **スキップ (`s` / `s all`)**: `s` は再生中なら `voice_client.stop()`、合成中ならその1件を再生しないよう印を付けます。`s all` はキューを空にしたうえで「世代番号」を1つ進めます。キューの各項目は積まれた時点の世代番号を持ち、ワーカーは世代が古い項目（合成中・先読み済みのもの）を再生せずに捨てます。
* **AFKタイマー**: 読み上げ中（再生中・合成中・キューが空でない）は切断せず、読み上げが終わってからさらに `AFK_TIME` 秒待ちます。
* **切断**: `dc` コマンド・Botのキック・AFKタイムアウト・VCが無人になった場合のいずれも `session.close()` を通り、ワーカー停止 → キュー破棄 → 再生停止 → 切断を行います。話者・速度の設定は `SettingsStore` に保存されているため、再接続・再起動後も引き継がれます。

### 2.3 サーバーごとの設定（`SettingsStore`）
`settings/{guild_id}.json` に次の形式で保存します（`DictionaryStore` と同じく、メモリキャッシュ・`threading.Lock`・一時ファイル + fsync + `os.replace` によるアトミック書き込み。書き込み処理は `jsonfile.py` で共有）。

```json
{"speaker": "春日部つむぎ", "users": {"<user_id>": {"speaker": "ずんだもん", "speed": 1.2}}}
```

* `y.c` のドロップダウン → サーバーの話者（`speaker`）。`y.voice` のドロップダウン → 操作したユーザーの話者。`y.speed` → コマンドを打ったユーザーの速度（0.5〜2.0）。`reset` で解除し、ユーザーの設定が空になったらエントリごと削除します。
* 書き込みはイベントループから `asyncio.to_thread` 経由で行い、読み込みは `aget()`（キャッシュがあれば I/O なし）を使います。
* 壊れたファイルは読み込み時は空の設定として扱い（ログに出力）、書き込み時は上書きせずに例外を送出します（辞書と同じ方針）。未知のキーは書き込み時もそのまま残します。
* **話者の決定順（`resolve_voice`）**: ユーザー個人の話者 → サーバーの話者 → エンジンの既定話者。保存された話者が現在のエンジンで利用できない場合（Google TTS を無効にした、モデルを減らした等）はその段を飛ばします。速度はユーザーの設定、なければ 1.0（範囲外の値は 0.5〜2.0 に丸める）。

## 3. アプリケーションの動作フロー
1. **テキスト受信**: `VoiceCog.on_message` でテキストをフック（コマンド用のプレフィックスから始まるもの、Bot・DM・読み上げ対象外チャンネルの発言は除外）。
2. **辞書置換**: `dict/{guild.id}.json` の内容を `DictionaryStore` が初回のみスレッドで読み込み、コンパイル済み正規表現と一緒にメモリへキャッシュ（`dict add` / `dict del` 時は書き込んだ内容で差し替えるため、読み上げ時にファイル I/O は発生しない）。長い単語を優先して読み仮名に置換。
3. **前処理**: コードブロック（「コード省略」に置換、インラインコードは中身だけ残す）、URL、ネタバレタグ(`\|\|...\|\|`)、メンション(`<@...>` / `<@&...>` / `<#...>`)、カスタム絵文字、簡易なマークダウンを置換（`text.build_read_text`）。`*強調*` は記号の内側が空白でない場合のみ、`_強調_` は英数字に挟まれていない場合のみ除去するため、`snake_case_name` や `2 * 3 * 4` はそのまま読まれます。最後に `MAX_READ_LENGTH` 文字を超えた分を切り詰めて「以下略」を付けます。
4. **音声合成・再生**: 上記のワーカーが、発言者ごとに決めた話者・速度（2.3節）に応じたエンジンでメモリ上に WAV を生成し、FFmpeg 経由で順番にVCへ送信。
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
軽量な `python:3.12-slim-bookworm` をベースに設計しており、コンテナビルド時にLinux（x86_64, arm64等）向けの `voicevox-downloader` を自動で叩き、ONNX Runtimeとモデルを準備します。ユーザー設定(`config.ini`)や辞書(`dict/`)、サーバーごとの設定(`settings/`)、各種ログ(`log/`)はコンテナ外のホスト側に紐付けられ、永続化されるモダンな構成を実現しています。

## 5. テストとCI

* `tests/` の pytest は、偽のボイスクライアント・偽の合成エンジンを使って、テキスト整形・辞書・設定・セッション（再生順、スキップ、AFK、切断）を検証します。Discord への接続や `voicevox_core` / `google-cloud-texttospeech` のインストールは不要です。
* `.github/workflows/ci.yml`: `main` / `develop` への push とプルリクエストごとに `py_compile`、`ruff check .`、`pytest` を実行します。
* `main` ブランチは保護されており、CI（`check` ジョブ）が成功しないとマージできません（管理者にも適用）。
* `.github/workflows/deploy.yml`: `main` への push で走った CI が**成功したときだけ**、self-hosted ランナーで `scripts/deploy.sh` を実行します（`workflow_run`）。フォークからのプルリクエストでは動かないよう、イベントとリポジトリでも絞っています。

### 5.1 デプロイスクリプト（`scripts/deploy.sh`）
運用ディレクトリ `/root/yomi-KAI` で、CI が通ったコミットそのもの（`head_sha`）に対して次を行います。

1. `git merge --ff-only` でそのコミットへ更新（運用ディレクトリにローカル変更があれば何もせず失敗）。
2. 動作中のコンテナのイメージを `yomi-kai:previous` として退避。
3. `docker compose up -d --build`。ビルドに失敗した場合は git を元に戻して終了（旧版のコンテナは動き続ける）。
4. 起動確認: 最大120秒、コンテナのログに `Launch complete!` が出るのを待つ。
5. 成功なら、タグのないイメージと7日以上使っていないビルドキャッシュを削除。直近のキャッシュは残すので、コードだけの変更なら apt / pip / VOICEVOX のダウンロードはやり直さない。
6. 失敗なら直近のログを出力し、git を直前のコミットに戻して `yomi-kai:previous` で再起動（ロールバック）。デプロイは失敗として GitHub に表示される。

依存ライブラリは `requirements.txt` でバージョンを固定しています。更新するときは手元でテストと動作確認をしてから版を上げてください。

### 5.2 リリース（`.github/workflows/release.yml`）
Windows 版の配布 ZIP の作成と、GitHub リリースの下書き作成を自動化しています。公開は人が確認してから手動で行います。

1. GitHub の Actions タブで「Release」を選び、「Run workflow」で `main` を選んでバージョン（例: `3.2`）を入力して実行。
2. `windows-latest` 上で `build.py` を実行して ZIP を作成（Python Embeddable などのダウンロード物はキャッシュ）。バージョンの形式不正・同名のタグやリリースが既にある・`main` 以外から実行、のいずれかなら何もせず失敗します。
3. ZIP を添付した**下書き**リリースを作成（リリースノートは GitHub の自動生成）。下書きは公開されず、タグも公開時に作られます。
4. Claude Code に「vX.Y のリリースノートを書いて」と依頼し、前回のリリース以降の変更から、過去のリリースノートと同じ形式（`## Change` / `## Fix`）で清書して下書きに反映してもらう。設定の追加・既定値の変更・データの引き継ぎなど、利用者への注意点も確認する。
5. 内容を確認し、GitHub 上で公開ボタンを押す。

`build.py` / `requirements.txt` / `yomi-KAI.py` / `release.yml` を変更したプルリクエストでは、同じビルドだけを実行して ZIP が作れることを確認します（リリースは作りません。ZIP は Actions の成果物として3日間ダウンロード可能）。
