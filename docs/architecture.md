# yomi-KAI 開発者向けドキュメント：技術スタックとアーキテクチャ

このドキュメントでは、yomi-KAIの内部で利用されている技術や、処理の流れ、ポータブルな配布へのアプローチについて解説します。

## 1. コア技術・ライブラリ
| 分野 | 技術・ライブラリ | 説明 |
| --- | --- | --- |
| **言語** | Python 3.12 | アプリケーションのベース言語。モダンな文法と豊富なパッケージエコシステムを利用。 |
| **Discord Botフレームワーク** | discord.py (v2.0+) | Discord APIのラッパー。Voice機能の制御（`discord.FFmpegPCMAudio`）を利用し、VCへの音声ストリーミングを実行。コマンドは hybrid コマンド（プレフィックスとスラッシュコマンドの両対応）。また `discord.ui.Select` による話者変更のドロップダウンUI等にも利用。 |
| **合成エンジン (Local)** | voicevox_core | ローカルで完結する高品質な音声合成エンジン。ONNX RuntimeとOpen JTalk辞書を利用して高速に音声を生成。`.vvm` モデルファイルをロードし、ずんだもんや春日部つむぎ等の音声を使用。 |
| **合成エンジン (Cloud)** | google-cloud-texttospeech | GCPのAPIを利用した音声合成。`config.ini` の `GOOGLE_API_KEY`（APIキー）で認証し、Wavenet音声（`ja-JP-Wavenet-A` / `D`）を LINEAR16 (WAV) で生成（`USE_GOOGLE_TTS = True` のときのみ利用）。 |
| **メディア処理** | FFmpeg | 合成した WAV のバイト列を標準入力（`pipe=True`）で受け取り、Discordが要求するPCM形式に変換しながらストリーミングするために利用。一時ファイルは作らない。 |

## 2. コード構成

本体は `yomi_kai/` パッケージにあり、`yomi-KAI.py` は起動用の薄いスクリプトです。

```
yomi-KAI.py            # 起動スクリプト。lib/ 配下から起動された場合は親ディレクトリへ chdir し、
                       # lib/packages を sys.path に追加してから yomi_kai.main.main() を呼ぶ
yomi_kai/
  main.py              # Config・TTSエンジン・Bot を組み立てて起動（import 時の副作用なし）。スラッシュコマンドの同期、接続監視の結線
  health.py            # ハートビート・ヘルスチェック（python -m yomi_kai.health）・切断通知（discord を import しない）
  config.py            # config.ini を Config データクラスに読み込む。不備はキー名付きで報告し exit(1)
  logging_setup.py     # コンソール INFO / log/ 配下のファイル DEBUG。ログは最新50個を保持
  text.py              # 読み上げテキスト整形・添付ファイルの説明・入退室の判定（純粋関数のみ。I/Oなし）
  jsonfile.py          # JSON の読み込みとアトミック書き込み（DictionaryStore / SettingsStore で共有）
  dictionary.py        # DictionaryStore: dict/{guild_id}.json の読み込み・キャッシュ・アトミック書き込み
  settings.py          # SettingsStore: settings/{guild_id}.json（話者・速度・入退室の読み上げ）と resolve_voice（話者・速度の決定）
  session.py           # GuildSession: サーバーごとの接続・キュー・ワーカー・AFKタイマー
  ui.py                # 話者選択ドロップダウン（discord.ui.Select / View）
  tts/
    __init__.py        # TTSEngine プロトコル・Speaker・CompositeEngine・find_speakers（重いライブラリは import しない）
    voicevox.py        # VoicevoxEngine（asyncio API の Synthesizer を VOICEVOX_WORKERS 個のプールで並行に使う）
    pool.py            # InstancePool（借りて返すプール）・LoopThread（専用スレッドのイベントループ）
    google.py          # GoogleEngine
  cogs/
    common.py          # reply（スラッシュでは ephemeral にできる返信）・original_error などの共通ヘルパー
    voice.py           # help / c / dc / s コマンド、on_message（読み上げ）、on_voice_state_update（自動切断・入退室の読み上げ）
    settings.py        # voice / speed（ユーザーごとの話者・速度）、announce（入退室の読み上げのオン/オフ）
    dictionary.py      # dict add / del / list / help
    misc.py            # sd（突然の死）
docker-entrypoint.sh   # Docker: ボリュームの所有者を直してから一般ユーザーで起動
tests/                 # pytest（text / dictionary / settings / session / tts / health / config / commands）
```

### 2.1 TTSエンジン
`TTSEngine` は `synthesize(text, speaker, speed=1.0) -> bytes`（WAV）、`speakers() -> list[Speaker]`、`default_speaker` を持つプロトコルです。`speed` は話速の倍率です。

* **VoicevoxEngine**: ONNX Runtime と Open JTalk 辞書を起動時に一度だけ読み込み、既定では `models/vvms/0.vvm` のみをロードします（`config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を書くと `*.vvm` をすべてロード）。話者一覧はモデルのメタ情報（`VoiceModelFile.metas`）から構築し、ノーマルスタイルはキャラクター名（例: `ずんだもん`、`春日部つむぎ`）、その他のスタイルは `名前（スタイル名）` のラベルになります。ずんだもん・春日部つむぎは常に先頭に並び、Discord の制限によりドロップダウンには最大25件まで表示されます。速度が 1.0 なら `Synthesizer.tts()`、それ以外は `create_audio_query()` → `speed_scale` を設定 → `synthesis()` で合成します（いずれも `voicevox_core.asyncio` の API。次項）。
* **GoogleEngine**: `USE_GOOGLE_TTS = True` の場合のみ構築され、`CompositeEngine` で VOICEVOX と束ねられます（初期話者は `ja-JP-Wavenet-A`）。速度は `AudioConfig.speaking_rate` で指定します。
* **CompositeEngine**: 話者名で担当エンジンへ振り分け、`speed` はそのまま渡します。
* **VOICEVOX の並行合成（Synthesizer のプール）**: voicevox_core 0.16.4 のソースを確認した結果、次のことが分かっています。
  * ONNX のセッションは `async_lock::Mutex<ort::session::Session>` で守られている（[crates/voicevox_core/src/core/infer/runtimes/onnxruntime.rs](https://github.com/VOICEVOX/voicevox_core/blob/0.16.4/crates/voicevox_core/src/core/infer/runtimes/onnxruntime.rs)）。1つの `Synthesizer` を複数スレッドから呼んでも安全だが、推論は直列になる。
  * Python の blocking API（`voicevox_core.blocking`）は **GIL を握ったまま**合成する（[crates/voicevox_core_python_api/src/lib.rs](https://github.com/VOICEVOX/voicevox_core/blob/0.16.4/crates/voicevox_core_python_api/src/lib.rs) の blocking `tts` は GIL を解放せずに `perform()` する）。スレッドを増やしても速くならないうえ、合成中は Bot のイベントループ（Discord との通信・音声の送信）も止まる。実測でも、合成中に別スレッドが回せた Python の処理は通常の 0.5% 程度だった。
  * asyncio API（`voicevox_core.asyncio`）は GIL を解放して合成する。

  そこで `VoicevoxEngine` は、モデルを読み込んだ asyncio API の `Synthesizer` を `VOICEVOX_WORKERS` 個（1〜8、既定 2）用意し、専用スレッドのイベントループ（`tts/pool.py` の `LoopThread`）で動かします。同期の `synthesize()` はそのループへ合成を依頼して結果を待つだけなので、呼び出し側（`GuildSession` の `asyncio.to_thread`）や `TTSEngine` の形は変わりません。`Synthesizer` は `InstancePool` から1つ借りて使い終わったら返すため、1つの `Synthesizer` を同時に2つの合成が使うことはなく（ドキュメント化されていないスレッド安全性に頼らない）、全サーバー合計の同時合成数はワーカー数で頭打ちになります。ONNX Runtime は1つ、Open JTalk（内部で `Mutex` を持つ `Arc`）は全 `Synthesizer` で共有し、`cpu_num_threads` は「CPU 数 ÷ ワーカー数」（最低1）にして CPU の取り合いを避けます。起動時にワーカー数・スレッド数と（psutil か /proc で取れれば）メモリ使用量をログに出します。
  * 参考（Windows・24 論理 CPU・`0.vvm` のみ、4文の合成）: ワーカー1 は読み込み後 RSS 約130MB・4文を並行 2.0秒、ワーカー2 は約200MB・1.0秒、ワーカー4 は約350MB・0.9秒。合成を繰り返した後の RSS は ONNX Runtime のメモリ確保によりそれぞれ約280MB / 530MB / 890MB まで増えます。
  * `GoogleEngine` はネットワーククライアントなのでプールは持たず、並行数は `GuildSession` の先読み数だけで制限されます。
* **話者名での指定**: `find_speakers(query, names)` が完全一致 → 前方一致 → 部分一致の順に探し（NFKC 正規化・大文字小文字を無視）、最初に見つかった段階の候補を返します。`y.voice 名前` は候補が1つなら設定、複数なら最大10件を表示します。`y.voice list` は全話者を2000文字未満に分けて表示します。
* `voicevox_core` / `google-cloud-texttospeech` は `main.build_engine()` の中で遅延 import されるため、テストや CI はこれらをインストールせずに実行できます。

### 2.2 サーバーごとのワーカーモデル
サーバーごとの状態はすべて `GuildSession` が持ち、`VoiceCog.sessions: dict[int, GuildSession]`（キーは `guild.id`）で管理します。モジュールレベルの可変状態はありません。

* **接続 (`c`)**: ボイスクライアントと読み上げ対象のテキストチャンネルを `session.start()` に渡し、ワーカータスクを起動、AFKタイマーを開始します（発言がなくても `AFK_TIME` 秒後に自動切断）。
* **読み上げ**: `on_message` は発言者の話者・速度を `resolve_voice()` で決め（2.3節）、整形済みテキストと一緒に `session.enqueue(text, speaker, speed)` で積むだけです（AFKタイマーも張り直す）。`enqueue` は `text.split_for_synthesis()` でメッセージを合成用のかたまりに分けます（2.2.1）。セッション自体は話者を持ちません。
* **ワーカー**: 1サーバーにつき1本。再生前のかたまりが最大 `PREFETCH_CHUNKS`（3）個になるよう、メッセージをまたいで次のかたまりの合成を `asyncio.to_thread` で並行に始め、かたまりを必ずキュー投入順に1つずつ再生します（合成が遅いかたまりがあっても追い越さない）。長文でも最初の文の合成が終われば再生が始まり、その再生中に続きを合成します。かたまりの間にはわずかな間が空きます（音声の連結はしない）。1つのかたまりの合成に失敗した場合はそのかたまりだけ飛ばし、「読み上げに失敗しました」はメッセージごとに1回だけ通知します。FFmpeg のソースは `voice_client.play()` の直前に生成し、再生完了の `after` コールバックは `loop.call_soon_threadsafe` でイベントループ側の `asyncio.Event` をセットしてワーカーに制御を戻します。
* **スキップ (`s` / `s all`)**: スキップはメッセージ単位です。`s` は再生中（または合成待ち）のメッセージに印を付けて、その残りのかたまり（先読み合成済みのものを含む）の合成タスクを取り消し、再生中なら `voice_client.stop()` します。`s all` はキューを空にしたうえで「世代番号」を1つ進め、先読み中のタスクを取り消します。各メッセージは積まれた時点の世代番号を持ち、ワーカーは世代が古いかたまりを再生せずに捨てます。切断（`close()`）でも合成中のタスクをすべて取り消します。
* **入退室の読み上げ**: `on_voice_state_update` で、Bot 以外のメンバーが Bot のいるボイスチャンネルに入る・出る（チャンネル間の移動を含む）と、`text.voice_transition()` が入室・退室を判定し、「〇〇さんが入室しました」「〇〇さんが退室しました」をサーバーの話者・速度 1.0 で `session.enqueue` します（個人の話者と区別するため）。ミュート・配信などの変更や Bot 自身の接続では読み上げません。退室で Bot だけが残る場合は読み上げずに自動切断します。サーバー設定 `announce_voice`（`y.announce on|off`、既定オン）で無効にできます。
* **AFKタイマー**: 読み上げ中（再生中・合成中・未合成のかたまりが残っている）は切断せず、読み上げが終わってからさらに `AFK_TIME` 秒待ちます。
* **切断**: `dc` コマンド・Botのキック・AFKタイムアウト・VCが無人になった場合のいずれも `session.close()` を通り、ワーカー停止 → キュー破棄 → 再生停止 → 切断を行います。話者・速度の設定は `SettingsStore` に保存されているため、再接続・再起動後も引き継がれます。

#### 2.2.1 合成用の分割（`text.split_for_synthesis`）
* 「。！？!?」の直後と改行では必ず区切ります。直後に続く「！？」「…」などの記号の連続や、閉じ括弧（」』）など）は前のかたまりに含めます（「えっ！？」が「えっ！」「？」に割れない）。空白だけのかたまりは捨て、記号だけのかたまりは前に連結します。
* 読点「、，」では、かたまりが 40 文字（`soft_limit`）を超える場合だけ、上限より前の最後の読点で区切ります（上限より前になければ、後の最初の読点）。読点ごとに区切ると、合成のたびに文のイントネーション（抑揚）がリセットされて不自然な読み方になるためです。
* 句読点も改行もない長い文は途中で切りません（単語の途中で切ると読みが壊れるため）。

### 2.3 サーバーごとの設定（`SettingsStore`）
`settings/{guild_id}.json` に次の形式で保存します（`DictionaryStore` と同じく、メモリキャッシュ・`threading.Lock`・一時ファイル + fsync + `os.replace` によるアトミック書き込み。書き込み処理は `jsonfile.py` で共有）。

```json
{"speaker": "春日部つむぎ", "announce_voice": true, "users": {"<user_id>": {"speaker": "ずんだもん", "speed": 1.2}}}
```

* `y.c` のドロップダウン → サーバーの話者（`speaker`）。`y.voice` のドロップダウン → 操作したユーザーの話者（`y.voice 名前` はコマンドを打ったユーザーの話者）。`y.announce on|off` → 入退室の読み上げ（`announce_voice`、未設定なら true）。`y.speed` → コマンドを打ったユーザーの速度（0.5〜2.0）。`reset` で解除し、ユーザーの設定が空になったらエントリごと削除します。
* 書き込みはイベントループから `asyncio.to_thread` 経由で行い、読み込みは `aget()`（キャッシュがあれば I/O なし）を使います。
* 壊れたファイルは読み込み時は空の設定として扱い（ログに出力）、書き込み時は上書きせずに例外を送出します（辞書と同じ方針）。未知のキーは書き込み時もそのまま残します。
* **話者の決定順（`resolve_voice`）**: ユーザー個人の話者 → サーバーの話者 → エンジンの既定話者。保存された話者が現在のエンジンで利用できない場合（Google TTS を無効にした、モデルを減らした等）はその段を飛ばします。速度はユーザーの設定、なければ 1.0（範囲外の値は 0.5〜2.0 に丸める）。

### 2.4 コマンド（hybrid コマンド）
コマンドはすべて `commands.hybrid_command` / `hybrid_group` で定義しており、1つの実装で `y.c`（プレフィックス）と `/c`（スラッシュコマンド）の両方に対応します。

* スラッシュコマンド名はプレフィックスと同じ（`c`, `dc`, `s`, `voice`, `speed`, `announce`, `dict add|del|list|help`, `sd`, `help`）で、日本語の説明と引数の説明（`app_commands.describe`）を付けています。`s` の `mode` は `all` のみの選択肢、`announce` の `state` は `on` / `off` の選択肢、`speed` の `value` は `reset` も受け付けるため文字列です。サーバー専用のコマンドは `commands.guild_only()` により DM のスラッシュコマンド一覧にも出ません。
* 返信はすべて `ctx.send`（`cogs/common.reply`）を通します。プレフィックスでは従来どおりチャンネルへの送信、スラッシュではインタラクションへの応答（2通目以降はフォローアップ）になるので「アプリケーションが応答しませんでした」になりません。`reply(..., private=True)` はスラッシュのときだけ ephemeral（本人にだけ表示）になり（`ctx.interaction is not None` で判定）、個人設定（`voice` / `speed`）の返信や引数の誤りの案内に使います。`voice` のドロップダウンは誰でも操作できるため公開のままです。`c` / `dc` は接続・切断に時間がかかることがあるため、先に `ctx.defer()` します（プレフィックスでは何もしない）。
* 引数の過不足・不正な値（`commands.UserInputError`）は各 Cog の `cog_command_error` が従来と同じ案内（「コマンドが間違っています」など）を返します（`ignore_extra=False` で余分な引数もエラーにする）。設定の保存失敗は、プレフィックスの `CommandInvokeError` とスラッシュの `HybridCommandError` を `original_error()` でほどいて判定します。
* `setup_hook` で Cog を登録したあと `bot.tree.sync()` でグローバルに同期し、件数をログに出します（失敗してもログに出して起動は続ける）。グローバルコマンドは Discord のクライアントに反映されるまで時間がかかることがあり、招待時に `applications.commands` スコープが必要です。メッセージの読み上げには引き続き Message Content Intent が必要です。

### 2.5 接続監視（`health.py`）
* **ハートビート**: `setup_hook` で起動するバックグラウンドタスクが、Gateway に接続している間（`on_ready` / `on_resumed` から `on_disconnect` まで、かつ `is_ready()`・`not is_closed()`・`latency` が有限）だけ `log/heartbeat` に UNIX 時刻を30秒ごとに書き込みます（一時ファイル + `os.replace`）。
* **ヘルスチェック**: `python -m yomi_kai.health` はハートビートが180秒以内なら 0、それ以外（ファイルなしを含む）は 1 で終了します。`discord` / `voicevox_core` を import しないので高速で副作用がありません。Docker の `HEALTHCHECK` から使います。
* **切断通知**: `DisconnectAlerter` が `on_disconnect` で切断開始時刻を記録し、`ALERT_AFTER` 秒（既定300、30以上）切断が続いたら `ALERT_WEBHOOK_URL` の Discord Webhook に「yomi-KAI が N分以上 Discord から切断されています（開始時刻〜）」を送ります。ネットワーク自体が落ちていることが多いので、送れるまで10秒から倍々で最大5分間隔まで再送し続けます。`on_ready` / `on_resumed` で復帰したとき、通知を試みていれば（送れていなくても）「yomi-KAI が復旧しました（停止: 開始〜終了、N分）」を同様に再送付きで送ります。起動直後も接続完了までは切断中として扱うので、起動時に接続できない状態が続いた場合も通知されます。起動のお知らせは送りません。時刻はローカル時刻（コンテナは `TZ=Asia/Tokyo`）の `%m/%d %H:%M` 表記です。
* 送信は aiohttp（discord.py の依存）で行い、タイムアウトは10秒。失敗はすべて WARNING ログにとどめ、Bot 側へ例外を伝播させません。Webhook の URL は秘密情報なので、ログや設定エラーには出力しません（`mask_url` でホスト名以外を伏せる。`Config` の repr にも出さない）。`ALERT_WEBHOOK_URL` が空ならハートビートだけを書きます。
* 時計・sleep・送信関数を差し替えられるので、`tests/test_health.py` で Discord なしに状態遷移（閾値前の復帰・通知・再送・送れなかった場合の復旧通知）を検証しています。

## 3. アプリケーションの動作フロー
1. **テキスト受信**: `VoiceCog.on_message` でテキストをフック（コマンド用のプレフィックスから始まるもの、Bot・DM・読み上げ対象外チャンネルの発言は除外）。
2. **辞書置換**: `dict/{guild.id}.json` の内容を `DictionaryStore` が初回のみスレッドで読み込み、コンパイル済み正規表現と一緒にメモリへキャッシュ（`dict add` / `dict del` 時は書き込んだ内容で差し替えるため、読み上げ時にファイル I/O は発生しない）。長い単語を優先して読み仮名に置換。
3. **前処理**: コードブロック（「コード省略」に置換、インラインコードは中身だけ残す）、URL、ネタバレタグ(`\|\|...\|\|`)、メンション(`<@...>` / `<@&...>` / `<#...>`)、カスタム絵文字、簡易なマークダウンを置換（`text.build_read_text`）。`*強調*` は記号の内側が空白でない場合のみ、`_強調_` は英数字に挟まれていない場合のみ除去するため、`snake_case_name` や `2 * 3 * 4` はそのまま読まれます。続いて添付ファイル・スタンプの説明（`text.describe_attachments`: 「画像」「画像2枚」「動画」「音声」「ファイル」「スタンプ 名前」。種類は content_type、無ければ拡張子で判断）を末尾に付け（本文が空でも読み上げる）、最後にそれも含めて `MAX_READ_LENGTH` 文字を超えた分を切り詰めて「以下略」を付けます。
4. **音声合成・再生**: 上記のワーカーが、メッセージを文ごとのかたまりに分けて先読みしながら、発言者ごとに決めた話者・速度（2.3節）に応じたエンジンでメモリ上に WAV を生成し、FFmpeg 経由で順番にVCへ送信。
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

* **一般ユーザーでの実行**: Bot はイメージ内で作成した `yomi`（uid/gid 1000）ユーザーで動きます。ボリュームのディレクトリは Docker がホスト側に root 所有で作るため、`docker-entrypoint.sh` が root で起動して `dict/` `settings/` `log/` のうち所有者が違うものだけ `chown` し、`setpriv --reuid --regid --init-groups` で権限を落としてから Bot を起動します（既存の環境でも手作業は不要）。`config.ini`（読み取り専用マウント）が `yomi` で読めない権限の場合だけは、警告を出して root のまま起動します。
* **ヘルスチェック**: `HEALTHCHECK` で `python -m yomi_kai.health` を60秒ごとに実行し（起動後120秒は猶予）、ハートビートが途絶えるとコンテナが `unhealthy` になります（`restart: unless-stopped` は unhealthy では再起動しないため、状態の可視化が目的。長時間の切断は Webhook で通知）。
* レイヤーは apt → ユーザー作成 → pip → VOICEVOX のダウンロード → コードのコピーの順で、コードだけの変更なら最後の層だけが再ビルドされます。

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
