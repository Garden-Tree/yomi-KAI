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
  status_push.py       # ステータスページへの稼働状況の送信（STATUS_PUSH_URL を設定したときだけ。discord を import しない）
  config.py            # config.ini を Config データクラスに読み込む。不備はキー名付きで報告し exit(1)
  logging_setup.py     # コンソール INFO / log/ 配下のファイル DEBUG。ログは最新50個を保持。本文を含む discord.py の DEBUG ログの抑制
  status.py            # y.status の表示内容の整形・バージョン（環境変数 YOMI_KAI_VERSION）
  usage.py             # UsageStore: 月ごとの使用量 usage/{YYYY-MM}.json（文字数・件数のみ）と集計 CLI（python -m yomi_kai.usage）
  blocklist.py         # GuildBlocklist: 利用を断るサーバーの一覧（settings/blocked_guilds.txt）の読み込み（discord を import しない）
  text.py              # 読み上げテキスト整形・添付ファイルの説明・入退室の判定（純粋関数のみ。I/Oなし）
  jsonfile.py          # JSON の読み込みとアトミック書き込み（DictionaryStore / SettingsStore で共有）
  dictionary.py        # DictionaryStore: dict/{guild_id}.json、UserDictionaryStore: 個人辞書 dict/users/{guild_id}.json
  settings.py          # SettingsStore: settings/{guild_id}.json（話者・速度・高さ・各種オン/オフ）と resolve_voice / resolve_pitch
  plans.py             # PlanManager: サーバーごとの上限（limits_for）と使える話者、PremiumGuilds: settings/premium.json
  rejoin.py            # VoiceSessionRegistry: 再起動後の自動再接続のための接続の記録（settings/voice_sessions.json）
  session.py           # GuildSession: サーバーごとの接続・キュー・ワーカー・AFKタイマー
  ui.py                # 話者選択ドロップダウン（discord.ui.Select / View）
  tts/
    __init__.py        # TTSEngine プロトコル・Speaker・CompositeEngine・FilteredEngine（話者の許可リスト）・find_speakers（重いライブラリは import しない）
    voicevox.py        # VoicevoxEngine（asyncio API の Synthesizer を VOICEVOX_WORKERS 個のプールで並行に使う）
    pool.py            # InstancePool（借りて返すプール）・LoopThread（専用スレッドのイベントループ）
    google.py          # GoogleEngine
  cogs/
    common.py          # reply（スラッシュでは ephemeral にできる返信）・original_error などの共通ヘルパー
    voice.py           # help / c / dc / s / status コマンド、on_message（読み上げ）、on_voice_state_update（自動切断・自動接続・入退室の読み上げ）、起動後の再接続
    settings.py        # voice / speed / pitch（ユーザーごと）、announce / readname / speedup / readbots / read（サーバー全体）
    dictionary.py      # dict add / del / list / export / import / help、mydict add / del / list（個人辞書）
    plan.py            # plan（このサーバーの上限の表示）
    misc.py            # sd（突然の死、ENABLE_SUDDEN_DEATH = False なら Cog ごと追加しない）、credit（音声のクレジット表記）
    admin.py           # サーバー管理者向け: ignore（読み上げない人・ロール）、autojoin（自動接続）
docker-entrypoint.sh   # Docker: ボリュームの所有者を直してから一般ユーザーで起動
tests/                 # pytest（text / dictionary / settings / session / tts / health / config / commands / voice_cog / usage / allowlist / blocklist / plans / pitch / read_toggles / user_dictionary / rejoin）
docs/hosting.md        # 公開版（ホスティング）向けの推奨設定・ブラックリスト・使用量・保存するデータ
```

### 2.1 TTSエンジン
`TTSEngine` は `synthesize(text, speaker, speed=1.0, pitch=0.0) -> bytes`（WAV）、`speakers() -> list[Speaker]`、`default_speaker` を持つプロトコルです。`speed` は話速の倍率、`pitch` は声の高さで VOICEVOX の `pitch_scale`（対数の基本周波数に足す値）と同じ単位です。

* **VoicevoxEngine**: ONNX Runtime と Open JTalk 辞書を起動時に一度だけ読み込み、既定では `models/vvms/0.vvm` のみをロードします（`config.ini` に `VOICEVOX_LOAD_ALL_MODELS = True` を書くと `*.vvm` をすべてロード）。話者一覧はモデルのメタ情報（`VoiceModelFile.metas`）から構築し、ノーマルスタイルはキャラクター名（例: `ずんだもん`、`春日部つむぎ`）、その他のスタイルは `名前（スタイル名）` のラベルになります。ずんだもん・春日部つむぎは常に先頭に並び、Discord の制限によりドロップダウンには最大25件まで表示されます。速度が 1.0・高さが 0.0 なら `Synthesizer.tts()`、それ以外は `create_audio_query()` → `speed_scale` / `pitch_scale` を設定 → `synthesis()` で合成します（いずれも `voicevox_core.asyncio` の API。次項）。
* **GoogleEngine**: `USE_GOOGLE_TTS = True` の場合のみ構築され、`CompositeEngine` で VOICEVOX と束ねられます（初期話者は `ja-JP-Wavenet-A`）。速度は `AudioConfig.speaking_rate`、高さは `AudioConfig.pitch`（`tts.google_semitones()` が `pitch` を同じ変化になる半音に換算、±20 まで）で指定します。
* **CompositeEngine**: 話者名で担当エンジンへ振り分け、`speed` はそのまま渡します。
* **FilteredEngine（話者の許可リスト）**: `config.ini` の `SPEAKER_ALLOWLIST`（`y.voice list` の表示どおりの名前をカンマ区切り）が指定されていれば、`main.build_plans()` → `apply_speaker_allowlist()` が起動時に一度だけ、構築したエンジン（`CompositeEngine` を含む）を `FilteredEngine` で包みます（`PREMIUM_ENABLED = True` なら `PREMIUM_SPEAKER_ALLOWLIST` でも別に包む。3.3節）。`speakers()` は元の順序のまま許可された話者だけを返し、`synthesize()` は元のエンジンに任せます（許可されていない話者が渡されたら既定話者で合成）。元の既定話者が許可されていなければ、許可された最初の話者を既定にします。Cog は `PlanManager.speakers_for(guild_id)` でそのサーバーのプランに応じた包んだエンジンを使うので、ドロップダウン・`y.voice 名前`・`y.voice list`・`resolve_voice`・`y.credit` は許可された話者しか見ません（保存済みの設定が許可外の話者を指していれば `resolve_voice` が次の段へ飛ばす）。見つからない名前は起動時に警告し、1つも残らなければ `ConfigError` として exit(1) します。未設定なら包みません。
* **クレジット表記**: `Speaker.credit` に利用規約で求められる表記を持たせます。`VoicevoxEngine` はモデルのメタ情報（`VoiceModelFile.metas` のキャラクター名）から `VOICEVOX:ずんだもん` を設定し（スタイル名は含まない。ラベルの解析はしない）、Google の話者は `None` です。`y.credit` は使える話者の `credit` を重複なく並べ、VOICEVOX の利用規約（https://voicevox.hiroshiba.jp/term/ ）へのリンクと、Google TTS 有効時はその旨を添えます。
* **VOICEVOX の並行合成（Synthesizer のプール）**: voicevox_core 0.16.4 のソースを確認した結果、次のことが分かっています。
  * ONNX のセッションは `async_lock::Mutex<ort::session::Session>` で守られている（[crates/voicevox_core/src/core/infer/runtimes/onnxruntime.rs](https://github.com/VOICEVOX/voicevox_core/blob/0.16.4/crates/voicevox_core/src/core/infer/runtimes/onnxruntime.rs)）。1つの `Synthesizer` を複数スレッドから呼んでも安全だが、推論は直列になる。
  * Python の blocking API（`voicevox_core.blocking`）は **GIL を握ったまま**合成する（[crates/voicevox_core_python_api/src/lib.rs](https://github.com/VOICEVOX/voicevox_core/blob/0.16.4/crates/voicevox_core_python_api/src/lib.rs) の blocking `tts` は GIL を解放せずに `perform()` する）。スレッドを増やしても速くならないうえ、合成中は Bot のイベントループ（Discord との通信・音声の送信）も止まる。実測でも、合成中に別スレッドが回せた Python の処理は通常の 0.5% 程度だった。
  * asyncio API（`voicevox_core.asyncio`）は GIL を解放して合成する。

  そこで `VoicevoxEngine` は、モデルを読み込んだ asyncio API の `Synthesizer` を `VOICEVOX_WORKERS` 個（1〜8、既定 1）用意し、専用スレッドのイベントループ（`tts/pool.py` の `LoopThread`）で動かします。`GuildSession` は `synthesize_async()` で合成し、VOICEVOX ではそのループのタスクを `asyncio.wrap_future` で待ちます。読み上げを飛ばすと待っている合成がキャンセルされ、借りていた `Synthesizer` がすぐ返ります。Google TTS は合成専用のスレッドプール（`yomi-kai-tts`）で動かし、既定のスレッドプール（設定・辞書の読み書きが使う）を埋めません。`Synthesizer` は `InstancePool` から1つ借りて使い終わったら返すため、1つの `Synthesizer` を同時に2つの合成が使うことはなく（ドキュメント化されていないスレッド安全性に頼らない）、全サーバー合計の同時合成数はワーカー数で頭打ちになります。ONNX Runtime は1つ、Open JTalk（内部で `Mutex` を持つ `Arc`）は全 `Synthesizer` で共有し、`cpu_num_threads` は「CPU 数 ÷ ワーカー数」（最低1）にして CPU の取り合いを避けます。起動時にワーカー数・スレッド数と（psutil か /proc で取れれば）メモリ使用量をログに出します。
  * 参考（Windows・24 論理 CPU・`0.vvm` のみ、4文の合成）: ワーカー1 は読み込み後 RSS 約130MB・4文を並行 2.0秒、ワーカー2 は約200MB・1.0秒、ワーカー4 は約350MB・0.9秒。合成を繰り返した後の RSS は ONNX Runtime のメモリ確保によりそれぞれ約280MB / 530MB / 890MB まで増えます。
  * 既定のワーカー数は 1 です（README の推奨メモリ 512MB に対し、2 では合成後の RSS が約530MB になるため）。1 でも、長文は文ごとに分けて合成するので最初の文ができた時点で再生が始まり（2.2.1）、asyncio API は合成中に GIL を解放するので Bot のイベントループも止まりません。メモリに余裕があり、複数のサーバーで同時に読み上げる場合は増やすと待ち時間が短くなります。
  * `GoogleEngine` はネットワーククライアントなのでプールは持たず、並行数は `GuildSession` の先読み数だけで制限されます。
* **話者名での指定**: `find_speakers(query, names)` が完全一致 → 前方一致 → 部分一致の順に探し（NFKC 正規化・大文字小文字を無視）、最初に見つかった段階の候補を返します。`y.voice 名前` は候補が1つなら設定、複数なら最大10件を表示します。`y.voice list` は全話者を2000文字未満に分けて表示します。
* `voicevox_core` / `google-cloud-texttospeech` は `main.build_engine()` の中で遅延 import されるため、テストや CI はこれらをインストールせずに実行できます。

### 2.2 サーバーごとのワーカーモデル
サーバーごとの状態はすべて `GuildSession` が持ち、`VoiceCog.sessions: dict[int, GuildSession]`（キーは `guild.id`）で管理します。モジュールレベルの可変状態はありません。

* **接続 (`c`)**: ボイスクライアントと読み上げ対象のテキストチャンネルを `session.start()` に渡し、ワーカータスクを起動、AFKタイマーを開始します（発言がなくても `AFK_TIME` 秒後に自動切断）。
* **読み上げ**: `on_message` は発言者の話者・速度を `resolve_voice()`、高さを `resolve_pitch()` で決め（2.3節）、整形済みテキストと一緒に `session.enqueue(text, speaker, speed, pitch=...)` で積むだけです（AFKタイマーも張り直す）。`enqueue` は `text.split_for_synthesis()` でメッセージを合成用のかたまりに分けます（2.2.1）。セッション自体は話者を持ちません。
* **ワーカー**: 1サーバーにつき1本。再生前のかたまりが最大 `PREFETCH_CHUNKS`（3）個になるよう、メッセージをまたいで次のかたまりの合成を `synthesize_async()` で並行に始め、かたまりを必ずキュー投入順に1つずつ再生します（合成が遅いかたまりがあっても追い越さない）。長文でも最初の文の合成が終われば再生が始まり、その再生中に続きを合成します。かたまりの間にはわずかな間が空きます（音声の連結はしない）。1つのかたまりの合成に失敗した場合はそのかたまりだけ飛ばし、「読み上げに失敗しました」はメッセージごとに1回だけ通知します。FFmpeg のソースは `voice_client.play()` の直前に生成し、再生完了の `after` コールバックは `loop.call_soon_threadsafe` でイベントループ側の `asyncio.Event` をセットしてワーカーに制御を戻します。
* **スキップ (`s` / `s all`)**: スキップはメッセージ単位です。`s` は再生中（または合成待ち）のメッセージに印を付けて、その残りのかたまり（先読み合成済みのものを含む）の合成タスクを取り消し、再生中なら `voice_client.stop()` します。`s all` はキューを空にしたうえで「世代番号」を1つ進め、先読み中のタスクを取り消します。各メッセージは積まれた時点の世代番号を持ち、ワーカーは世代が古いかたまりを再生せずに捨てます。切断（`close()`）でも合成中のタスクをすべて取り消します。
* **入退室の読み上げ**: `on_voice_state_update` で、Bot 以外のメンバーが Bot のいるボイスチャンネルに入る・出る（チャンネル間の移動を含む）と、`text.voice_transition()` が入室・退室を判定し、「〇〇さんが入室しました」「〇〇さんが退室しました」をサーバーの話者・速度 1.0 で `session.enqueue` します（個人の話者と区別するため）。ミュート・配信などの変更や Bot 自身の接続では読み上げません。退室で Bot だけが残る場合は読み上げずに自動切断します。サーバー設定 `announce_voice`（`y.announce on|off`、既定オン）で無効にできます。
* **名前の読み上げ（`read_name`）**: オンなら、`build_read_text()` で切り詰めた本文の前に `text.with_name()` で「表示名、」を付けます（`MAX_READ_LENGTH` は本文だけに適用）。`VoiceCog` はサーバーごとに直前に読み上げた発言の (ユーザーID, 時刻) を持ち、`text.should_read_name()` が同じ人の30秒以内の連投では名前を省きます。
* **読み上げる発言の選別**: この Bot 自身の発言は読みません。ほかの Bot・Webhook（`message.webhook_id`）の発言は `read_bots` がオンのときだけ読みます（`settings.should_read_author()`）。`ignored_users` のユーザー、`ignored_roles` のロールを持つメンバーの発言も読みません（`settings.is_ignored()`）。
* **混雑時の早読み（`queue_speedup`、既定オン）**: `enqueue(..., speedup=True)` で積んだメッセージは、ワーカーが最初のかたまりの合成を始める時点で、合成を始めていないメッセージ（そのメッセージ自身を含む）が3件以上なら1.25倍、6件以上なら1.5倍の速度にします（`session.backlog_speed()`、上限2.0倍、元の速度より遅くはしない）。速度はメッセージの読み始めに決め、残りのかたまりも同じ速度で読みます。入退室の読み上げも対象です。読み上げ待ちが溜まったときだけ働く動作の変更で、`y.speedup off` で従来どおりになります。
* **自動接続（`autojoin`）**: `on_voice_state_update` で、Bot 以外のメンバーが登録されたボイスチャンネルに入り（移動を含む）、そのサーバーで Bot がまだ接続していなければ、`channel.connect()` → `session.start()` で対応するテキストチャンネルを読み上げ始め、「自動接続しました」の埋め込みを投稿します（ドロップダウンは出さない。判定は `text.autojoin_text_channel()`）。接続中のサーバーは `_autojoining` で印を付け、同時に複数人が入っても1回だけ接続します。自動切断（AFK タイマー・無人・無人のチャンネルへの移動・キックや切断）の時刻を記録し、そこから30秒間は自動接続しません（`text.autojoin_allowed()`。接続と切断の繰り返しを防ぐ）。AFK タイマーによる切断は `GuildSession.on_auto_close` で知らせます。
* **再起動後の自動再接続（`rejoin.py`）**: `_join()` が接続（移動を含む）のたびに `VoiceSessionRegistry.record()` で `settings/voice_sessions.json` に `{"<guild_id>": {"voice": "<id>", "text": "<id>"}}` を書き、別のチャンネルへ移動させられたときも書き直します。`_leave()`（`dc`・無人・キック・無人のチャンネルへの移動）・AFK タイマーの切断（`on_auto_close`）・`on_guild_remove` で `forget()` します。Bot の終了（`cog_unload`）では `_closing` を立てて消さないので、デプロイや `docker stop` のあとも記録が残ります。起動後の最初の `on_ready` で一度だけ（`_rejoin_started`）`rejoin_sessions()` をタスクで動かし、記録ごとに `rejoin.rejoin_skip_reason()` で、サーバー・チャンネルがある、ブロックされていない、まだ接続していない、Bot 以外のメンバーがいる、接続・発言の権限がある、を確かめてから `_join()` します（`_autojoining` に入れて自動接続と重ならないようにする。埋め込みは投稿せずログだけ）。すでに接続している（自動接続が先に動いた）場合は記録を残したまま飛ばし、それ以外で再接続しなかった・失敗したものは記録を消します。記録の読み書きの失敗は WARNING ログにとどめます。
* **AFKタイマー**: 読み上げ中（再生中・合成中・未合成のかたまりが残っている）は切断せず、読み上げが終わってからさらに `AFK_TIME` 秒待ちます。
* **切断**: `dc` コマンド・Botのキック・AFKタイムアウト・VCが無人になった場合のいずれも `session.close()` を通り、ワーカー停止 → キュー破棄 → 再生停止 → 切断を行います。話者・速度の設定は `SettingsStore` に保存されているため、再接続・再起動後も引き継がれます。

#### 2.2.1 合成用の分割（`text.split_for_synthesis`）
* 「。！？!?」の直後と改行では必ず区切ります。直後に続く「！？」「…」などの記号の連続や、閉じ括弧（」』）など）は前のかたまりに含めます（「えっ！？」が「えっ！」「？」に割れない）。ただし閉じ括弧の直後に同じ行の続きがある場合（`「すごい！」と言った。`、`「はい。」と答えた。`）は文の途中なので区切りません（閉じ括弧が行末、または後ろが空白なら区切る）。空白だけのかたまりは捨て、記号だけのかたまりは前に連結します。
* 読点「、，」では、かたまりが 40 文字（`soft_limit`）を超える場合だけ、上限より前の最後の読点で区切ります（上限より前になければ、後の最初の読点）。読点ごとに区切ると、合成のたびに文のイントネーション（抑揚）がリセットされて不自然な読み方になるためです。
* 句読点も改行もない長い文は途中で切りません（単語の途中で切ると読みが壊れるため）。

### 2.3 サーバーごとの設定（`SettingsStore`）
`settings/{guild_id}.json` に次の形式で保存します（`DictionaryStore` と同じく、メモリキャッシュ・`threading.Lock`・一時ファイル + fsync + `os.replace` によるアトミック書き込み。書き込み処理は `jsonfile.py` で共有）。

```json
{"speaker": "春日部つむぎ", "announce_voice": true, "users": {"<user_id>": {"speaker": "ずんだもん", "speed": 1.2, "pitch": 3}},
 "read_name": false, "queue_speedup": true, "read_bots": false,
 "read_url": true, "read_emoji": true, "read_code": false, "read_spoiler": false,
 "ignored_users": ["<user_id>"], "ignored_roles": ["<role_id>"], "autojoin": {"<voice_channel_id>": "<text_channel_id>"}}
```

* 追加したキーはどれも省略可能で、省略時は `read_name` = false、`queue_speedup` = true、`read_bots` = false、`read_url` / `read_emoji` = true、`read_code` / `read_spoiler` = false、`pitch` = 未設定（0）、`ignored_users` / `ignored_roles` / `autojoin` = 空です。ID は文字列で保存します（読み込み時は数字の文字列・整数のどちらも受け付け、それ以外は形式エラー）。`ignored_users` / `ignored_roles` はそれぞれ25件、`autojoin` は5件まで（上限は登録時に `SettingsStore` のロックの中で確かめる）。

* `y.c` のドロップダウン → サーバーの話者（`speaker`）。`y.voice` のドロップダウン → 操作したユーザーの話者（`y.voice 名前` はコマンドを打ったユーザーの話者）。`y.announce on|off` → 入退室の読み上げ（`announce_voice`、未設定なら true）。`y.speed` → コマンドを打ったユーザーの速度（0.5〜2.0）。`y.readname` / `y.speedup` / `y.readbots` → `read_name` / `queue_speedup` / `read_bots`（`set_flag`）。`y.pitch` → コマンドを打ったユーザーの `pitch`（-10〜+10 の整数）。`y.read url|emoji|code|spoiler on|off` → `read_url` / `read_emoji` / `read_code` / `read_spoiler`（表示は誰でも、変更はコマンドの中で「サーバー管理」の権限を確かめる）。`y.ignore add|del` → `ignored_users` / `ignored_roles`。`y.autojoin add|del` → `autojoin`。`reset` で解除し、ユーザーの設定が空になったらエントリごと削除します。
* 書き込みはイベントループから `asyncio.to_thread` 経由で行い、読み込みは `aget()`（キャッシュがあれば I/O なし）を使います。
* 壊れたファイルは読み込み時は空の設定として扱い（ログに出力）、書き込み時は上書きせずに例外を送出します（辞書と同じ方針）。発言のたびに読み直さないよう、読み込みに失敗してから60秒は読み直さずに空の設定を返し（`jsonfile.FailedLoads`）、トレースバックはファイルが変わったときだけ出します。未知のキーは書き込み時もそのまま残します。
* **話者の決定順（`resolve_voice`）**: ユーザー個人の話者 → サーバーの話者 → エンジンの既定話者。保存された話者が現在のエンジンで利用できない場合（Google TTS を無効にした、モデルを減らした等）はその段を飛ばします。速度はユーザーの設定、なければ 1.0（範囲外の値は 0.5〜2.0 に丸める）。高さ（`resolve_pitch`）はユーザーの段階 × 0.015（±10 で ±0.15、約 ±2.6 半音）を `pitch` として渡し、未設定なら 0.0。

### 2.4 コマンド（hybrid コマンド）
コマンドはすべて `commands.hybrid_command` / `hybrid_group` で定義しており、1つの実装で `y.c`（プレフィックス）と `/c`（スラッシュコマンド）の両方に対応します。

* スラッシュコマンド名はプレフィックスと同じ（`c`, `dc`, `s`, `voice`, `speed`, `pitch`, `announce`, `dict add|del|list|export|import|help`, `mydict add|del|list`, `sd`, `credit`, `help`, `readname`, `speedup`, `readbots`, `read list|url|emoji|code|spoiler`, `ignore add|del|list`, `autojoin add|del|list`, `status`, `plan`）で、日本語の説明と引数の説明（`app_commands.describe`）を付けています。`s` の `mode` は `all` のみの選択肢、`announce` の `state` は `on` / `off` の選択肢、`speed` の `value` は `reset` も受け付けるため文字列です。サーバー専用のコマンドは `commands.guild_only()` / `app_commands.guild_only()` により DM のスラッシュコマンド一覧にも出ません。設定・辞書・管理者向けの Cog は、DM での実行（プレフィックスのサブコマンドを含む）を Cog の `cog_check`（`cogs/common.guild_only_check`）で一律に `NoPrivateMessage` にしており、`Bot.on_command_error` が「このコマンドはサーバー内でのみ使用できます」と返します（`invoke_without_command` のグループでは、グループの `commands.guild_only()` がプレフィックスのサブコマンドに効かないため）。
* 返信はすべて `ctx.send`（`cogs/common.reply`）を通します。プレフィックスでは従来どおりチャンネルへの送信、スラッシュではインタラクションへの応答（2通目以降はフォローアップ）になるので「アプリケーションが応答しませんでした」になりません。`reply(..., private=True)` はスラッシュのときだけ ephemeral（本人にだけ表示）になり（`ctx.interaction is not None` で判定）、個人設定（`voice` / `speed`）の返信や引数の誤りの案内に使います。`voice` のドロップダウンは誰でも操作できるため公開のままです。`c` / `dc` は接続・切断に時間がかかることがあるため、先に `ctx.defer()` します（プレフィックスでは何もしない）。
* 引数の過不足・不正な値（`commands.UserInputError`）は各 Cog の `cog_command_error` が従来と同じ案内（「コマンドが間違っています」など）を返します（`ignore_extra=False` で余分な引数もエラーにする）。設定・辞書ファイルの読み書きの失敗（`OSError` / `ValueError`）も「設定の保存に失敗しました」「辞書の保存に失敗しました」を本人にだけ返します（返さないとスラッシュが「考え中…」のまま残る）。判定は共通の `cogs/common.handle_command_error` で行い、プレフィックスの `CommandInvokeError` とスラッシュの `HybridCommandError` を `original_error()` でほどいてから判定します。
* **サーバー管理者向けのコマンド**: `ignore add|del`・`autojoin add|del`・`readbots`・`dict import` は `commands.has_guild_permissions(manage_guild=True)` で「サーバー管理」の権限を求めます（スラッシュでも hybrid コマンドのチェックとして働く）。権限がなければ各 Cog の `cog_command_error` が「このコマンドを使うには「サーバー管理」の権限が必要です」を返し（スラッシュでは ephemeral）、`Bot.on_command_error` は `CheckFailure` をエラーとして記録しません。`ignore` の対象は `discord.Member | discord.Role`（スラッシュではメンション可能な値の選択肢）、`dict import` のファイルは `discord.Attachment`（プレフィックスではメッセージの添付、スラッシュでは `attachment` 引数）です。
* **辞書の書き出し・読み込み**: `dict export` は辞書ファイルと同じ形式（indent=2, ensure_ascii=False）の `dict-{guild_id}.json` を添付します。`dict import` は100KBまでの JSON を `parse_dictionary_import()` で検証し（オブジェクトで、単語・読みとも空でない文字列）、`DictionaryStore.merge()` がロックの中でまとめて書き込みます（同じ単語はファイルの読みで上書き、`MAX_DICT_ENTRIES` に収まる分だけ追加し、飛ばした件数を返す）。
* **`status`**: Discord の応答時間（`bot.latency`）、接続中のセッション数、このサーバーの合成待ちのメッセージ数（`session.pending`）、`VOICEVOX_WORKERS`、合成の状態（全セッション共通の `SynthesisStats` が最後の成功・失敗の時刻と失敗回数を持ち、最後の失敗のあとに成功していれば正常）、読み上げの開始までの時間（下記）、稼働時間（`YomiBot.started_at`）、バージョン（環境変数 `YOMI_KAI_VERSION`、無ければ「不明」）を表示します。整形は `status.py` の純粋関数です。
  * 読み上げの開始までの時間: `VoiceCog.on_message` の冒頭で単調時計（`LatencyStats.clock`、既定 `time.monotonic`）の時刻を取り、`enqueue(received_at=...)` でメッセージに持たせます。`GuildSession._play` が、そのメッセージの最初のかたまりについて `voice_client.play()` を呼び終えた時点で差を `LatencyStats` に記録し、DEBUG ログにも出します（本文は持たない）。合成・再生の開始に失敗したメッセージ、スキップして再生しなかったメッセージ、入退室の読み上げは数えません。`LatencyStats` は全セッション共通で、サーバーごとに直近100件（`deque(maxlen=100)`）を持ち、`y.status` では全サーバー分をまとめた中央値・95パーセンタイル（最近傍順位法）・件数を表示します。Discord からイベントが届くまでの時間と、ffmpeg の起動から最初の音声パケットが届くまでの時間は含みません。
* `setup_hook` で Cog を登録したあと `bot.tree.sync()` でグローバルに同期し、件数をログに出します（失敗してもログに出して起動は続ける）。グローバルコマンドは Discord のクライアントに反映されるまで時間がかかることがあり、招待時に `applications.commands` スコープが必要です。メッセージの読み上げには引き続き Message Content Intent が必要です。

### 2.5 接続監視（`health.py`）
* **ハートビート**: `setup_hook` で起動するバックグラウンドタスクが、Gateway に接続している間（`on_ready` / `on_resumed` から `on_disconnect` まで、かつ `is_ready()`・`not is_closed()`・`latency` が有限）だけ `log/heartbeat` に UNIX 時刻を30秒ごとに書き込みます（一時ファイル + `os.replace`）。
* **ヘルスチェック**: `python -m yomi_kai.health` はハートビートが180秒以内なら 0、それ以外（ファイルなしを含む）は 1 で終了します。`discord` / `voicevox_core` を import しないので高速で副作用がありません。Docker の `HEALTHCHECK` から使います。
* **切断通知**: `DisconnectAlerter` が `on_disconnect` で切断開始時刻を記録し、`ALERT_AFTER` 秒（既定300、30以上）切断が続いたら `ALERT_WEBHOOK_URL` の Discord Webhook に「yomi-KAI が N分以上 Discord から切断されています（開始時刻〜）」を送ります。ネットワーク自体が落ちていることが多いので、送れるまで10秒から倍々で最大5分間隔まで再送し続けます。`on_ready` / `on_resumed` で復帰したとき、通知を試みていれば（送れていなくても）「yomi-KAI が復旧しました（停止: 開始〜終了、N分）」を同様に再送付きで送ります。起動直後も接続完了までは切断中として扱うので、起動時に接続できない状態が続いた場合も通知されます。起動のお知らせは送りません。時刻はローカル時刻（コンテナは `TZ=Asia/Tokyo`）の `%m/%d %H:%M` 表記です。
* 送信は aiohttp（discord.py の依存）で行い、タイムアウトは10秒。失敗はすべて WARNING ログにとどめ、Bot 側へ例外を伝播させません。Webhook の URL は秘密情報なので、ログや設定エラーには出力しません（`mask_url` でホスト名以外を伏せる。`Config` の repr にも出さない）。`ALERT_WEBHOOK_URL` が空ならハートビートだけを書きます。
* 時計・sleep・送信関数を差し替えられるので、`tests/test_health.py` で Discord なしに状態遷移（閾値前の復帰・通知・再送・送れなかった場合の復旧通知）を検証しています。
* **稼働状況の送信**（`status_push.py`）: `STATUS_PUSH_URL` と `STATUS_PUSH_TOKEN` を両方設定したときだけ、`setup_hook` で起動するタスク（`StatusPusher`）が、Gateway に接続している間（ハートビートと同じ `is_gateway_alive`）`STATUS_PUSH_INTERVAL` 秒（既定300、60以上）ごとに、バージョン・UNIX 時刻・読み上げの開始までの時間（`VoiceCog.latency` の全サーバー分の中央値・95パーセンタイル・件数、ミリ秒）・ボイスチャンネルの接続数を JSON で POST します（`Authorization: Bearer <token>`）。受け手は5分ごとの区間に受信があったかで稼働率を数えるので、送る時刻は UNIX 時刻で各区間の中ほど（`next_delay`、例: 毎時 2分30秒・7分30秒…）にそろえ、区間の境目で取りこぼさないようにしています。未接続の間は送らず30秒ごとに確かめ直し、接続した直後（起動・復帰）だけはすぐに送ります。タイムアウトは10秒で、失敗しても再送せず次の回を待ちます。失敗の WARNING ログは30分に1回まで（それ以外は DEBUG）で、URL・トークンはログや設定エラーに出しません。受け手は yomi-kai-site（Cloudflare Pages Functions + D1）の `/api/heartbeat` で、公開のステータスページに表示します。`tests/test_status_push.py` で HTTP をモックして検証しています。

## 3. アプリケーションの動作フロー
1. **テキスト受信**: `VoiceCog.on_message` でテキストをフック（コマンド用のプレフィックスから始まるもの、Bot・DM・読み上げ対象外チャンネルの発言は除外）。
2. **辞書置換**: 発言者の個人辞書（`dict/users/{guild.id}.json` の発言者の分、`UserDictionaryStore`）を先に、続いてサーバー辞書を当てます。`dict/{guild.id}.json` の内容を `DictionaryStore` が初回のみスレッドで読み込み、コンパイル済み正規表現と一緒にメモリへキャッシュ（`dict add` / `dict del` 時は書き込んだ内容で差し替えるため、読み上げ時にファイル I/O は発生しない）。長い単語を優先して読み仮名に置換。
3. **前処理**: コードブロック（「コード省略」に置換、インラインコードは中身だけ残す）、URL、ネタバレタグ(`\|\|...\|\|`)、メンション(`<@...>` / `<@&...>` / `<#...>`)、カスタム絵文字、簡易なマークダウンを置換（`text.build_read_text`）。URL・カスタム絵文字・コードブロック・ネタバレの扱いはサーバー設定（`y.read`）の `text.ReadOptions` で切り替えます（URL・絵文字は読まない、コードブロック・ネタバレは中身を読む、を選べる。既定は従来どおり）。`*強調*` は記号の内側が空白でない場合のみ、`_強調_` は英数字に挟まれていない場合のみ除去するため、`snake_case_name` や `2 * 3 * 4` はそのまま読まれます。続いて添付ファイル・スタンプの説明（`text.describe_attachments`: 「画像」「画像2枚」「動画」「音声」「ファイル」「スタンプ 名前」。種類は content_type、無ければ拡張子で判断）を末尾に付け（本文が空でも読み上げる）、最後にそれも含めて、そのサーバーのプランの最大文字数（`PlanManager.limits_for().max_read_length`。フリーは `MAX_READ_LENGTH`）を超えた分を切り詰めて「以下略」を付けます。
4. **音声合成・再生**: 上記のワーカーが、メッセージを文ごとのかたまりに分けて先読みしながら、発言者ごとに決めた話者・速度（2.3節）に応じたエンジンでメモリ上に WAV を生成し、FFmpeg 経由で順番にVCへ送信。
5. **AFK監視**: 接続時および読み上げのたびにタイマーを張り直し、`AFK_TIME` 秒間読み上げがない場合や、VCにBot以外誰もいなくなった場合は自動でボイスチャンネルから切断。

ログはコンソールに INFO 以上、`log/` 配下のファイルに DEBUG 以上を出力します。

* **本文のログ（`LOG_READ_TEXT`、既定 False）**: False のとき、メッセージの本文・読み上げテキスト・合成するかたまりのテキストはどのレベルのログにも出しません（`on_message` は文字数・話者・速度だけを INFO で記録）。discord.py は受信した Gateway のイベント（`MESSAGE_CREATE` の本文を含む）と HTTP の送受信内容を DEBUG で出すため、`logging_setup.set_text_logging(False)` が `discord.gateway` / `discord.http` / `discord.webhook.async_` のロガーを INFO 以上に絞ります。True にすると従来どおり `ReadSentence:本文` を DEBUG で記録し、discord.py のロガーも絞りません。辞書の追加・削除（単語と読み）は管理操作なのでログに残します。
* `tests/test_voice_cog.py` が、既定の設定で偽のエンジンを使って `on_message` → `enqueue` → 合成を通し、DEBUG までのすべてのログに本文が含まれないことを確かめています。

### 3.1 使用量の計測（`usage.py`）
* 読み上げを受け付けた（`session.enqueue` が True を返した）発言ごとに、`UsageStore.record(guild_id, user_id, 文字数)` がメモリ上の増分に足します。文字数は `MAX_READ_LENGTH` で切り詰めたあとの読み上げテキストの長さです。入退室の読み上げはユーザーの使用量に含めず、`record_system()` でサーバーの `system_chars` に数えます。本文は保存しません。上限や制限はありません。
* 既定では無効です（`RECORD_USAGE = False`）。無効のとき `YomiBot.usage` は `None` で、`UsageStore` を作らず、書き込みのタスクも動かしません。`VoiceCog` は `usage` が `None` なら記録しません。
* 形式は `usage/{YYYY-MM}.json` に `{"guilds": {"<guild_id>": {"chars", "messages", "system_chars", "users": {"<user_id>": {"chars", "messages"}}}}}`（`USAGE_DIR` で保存先を変更可）。月はサーバーのローカル時刻（Docker は `TZ=Asia/Tokyo`）で決めます。
* `record()` は I/O をしません。`YomiBot` のバックグラウンドタスクが60秒ごとに、増分があれば `asyncio.to_thread(flush)` で書き込み、終了時（`close()` で Cog を外したあと）にも書き込みます。`flush()` はファイルを読んで増分を足し、`jsonfile.write_json_atomic` で書き込むので、再起動しても月の途中から数え続けられます。書き込みに失敗した増分は戻して次回また書き込みます。
* 壊れたファイル（JSON として読めない・形が違う）は上書きせず `{name}.corrupt-{日時}` に名前を変え、その月を新しく数え直します。権限などで読めないだけの場合（`OSError`）は名前を変えず、増分を残して次回また書き込みます。
* `python -m yomi_kai.usage [YYYY-MM] [--dir DIR]` はサーバー数・利用ユーザー数（重複を除く）・合計の文字数と件数・文字数の多いサーバー上位10件を表示します。`discord` / `voicevox_core` を import しません。

### 3.2 ブラックリスト（`blocklist.py`）
* `settings/blocked_guilds.txt`（`BLOCKED_GUILDS_FILE`）に1行に1つサーバー ID を書きます（`#` 以降はコメント、空行は無視、ID でない行は警告して無視）。ファイルが無ければ何もブロックしません。
* `GuildBlocklist.reload_if_changed()` は更新時刻が変わったときだけ読み直し、新たにブロックされた ID を返します。`setup_hook` で一度読み、以後はバックグラウンドタスクが30秒ごとに `asyncio.to_thread` で確認します。`is_blocked()` はメモリ上の集合を引くだけです。
* ブロックされたサーバーでは、`YomiBot.on_message` がプレフィックスのコマンドを処理せず、`bot.add_check` のグローバルチェックがコマンド（hybrid コマンドのスラッシュ側も `bot.can_run` を通る）を弾き、`VoiceCog.on_message` が読み上げません。`on_command_error` もブロックされたサーバーには何も返しません。
* `on_ready`（参加中のすべてのサーバー）・`on_guild_join`・ファイルの更新で新たにブロックされたとき、`leave_blocked_guild()` が接続中のセッションを `close()` してから `guild.leave()` し、サーバー ID と名前を WARNING で記録します。

### 3.3 プランと上限（`plans.py`）
* `PlanManager.limits_for(guild_id)` が `Limits`（`plan`・`max_read_length`・`max_dict_entries`・`max_user_dict_entries`・`expires`、数値の 0 は無制限）を返し、`speakers_for(guild_id)` / `engine_for(guild_id)` がそのサーバーで使える話者に絞ったエンジンを返します。読み上げの文字数（`VoiceCog.on_message`）、サーバー辞書・個人辞書の登録数（`DictionaryCog`）、話者の一覧・決定（`VoiceCog` / `SettingsCog` / `MiscCog`）はすべてここを通します。合成は読み込んだ全話者のエンジン（`base_engine`）で行い、使える話者かどうかは `resolve_voice` が確かめます。
* フリーの値は従来のキー（`MAX_READ_LENGTH` / `MAX_DICT_ENTRIES` / `MAX_USER_DICT_ENTRIES` / `SPEAKER_ALLOWLIST`）、プレミアムの値は `PREMIUM_MAX_READ_LENGTH` / `PREMIUM_MAX_DICT_ENTRIES` / `PREMIUM_MAX_USER_DICT_ENTRIES` / `PREMIUM_SPEAKER_ALLOWLIST`（空・0 で無制限、話者は空なら全話者）です。
* `PREMIUM_ENABLED = False`（既定）のときは `PremiumGuilds` を作らず、すべてのサーバーがフリーです（既定の設定ではすべて無制限）。True のときは `settings/premium.json`（`PREMIUM_GUILDS_FILE`、`{"<guild_id>": null | "YYYY-MM-DD" | {"expires": ...}}`）を `setup_hook` で一度読み、以後 `YomiBot._watch_premium` が30秒ごとに `asyncio.to_thread` で更新時刻を確かめて読み直します。期限はその日まで有効で、`limits_for` を呼ぶたびに今日の日付と比べます。壊れたファイルは前回の内容のまま使い（同じファイルでは警告を一度だけ）、読めない項目は警告して飛ばします。`limits_for` は I/O をしません。決済の処理はありません。
* `y.plan`（`cogs/plan.py`）は、プラン（`PREMIUM_ENABLED` のときだけ）・読み上げる文字数・サーバー辞書と自分の個人辞書の件数 / 上限・話者の数を表示し、フリーのサーバーにはプレミアムの上限を1行で案内します。

### 3.4 個人辞書（`UserDictionaryStore`）
* `dict/users/{guild_id}.json` に `{"<user_id>": {"単語": "読み"}}` で保存します（Docker では `dict/` のボリュームに含まれる）。サーバーごとに1ファイルを読み、ユーザーごとのコンパイル済み辞書をキャッシュします。登録の規則（`validate_entry`）・壊れたファイルの扱い（`FailedLoads`、書き込み時は上書きしない）・アトミックな書き込みは `DictionaryStore` と同じで、上限はユーザーごとに数えます。
* `build_read_text(..., user_dictionary=...)` が、メンション・サーバー絵文字を守ったまま個人辞書 → サーバー辞書の順に当てます。

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
軽量な `python:3.12-slim-bookworm` をベースに設計しており、コンテナビルド時にLinux（x86_64, arm64等）向けの `voicevox-downloader` を自動で叩き、ONNX Runtimeとモデルを準備します。ユーザー設定(`config.ini`)や辞書(`dict/`)、サーバーごとの設定(`settings/`。接続の記録・プレミアムの一覧も含む)、各種ログ(`log/`)、月ごとの使用量(`usage/`、`RECORD_USAGE = True` の場合)はコンテナ外のホスト側に紐付けられ、永続化されるモダンな構成を実現しています。

* **一般ユーザーでの実行**: Bot はイメージ内で作成した `yomi`（uid/gid 1000）ユーザーで動きます。ボリュームのディレクトリは Docker がホスト側に root 所有で作るため、`docker-entrypoint.sh` が root で起動して `dict/` `settings/` `log/` `usage/` のうち所有者が違うものだけ `chown` し、`setpriv --reuid --regid --init-groups` で権限を落としてから Bot を起動します（既存の環境でも手作業は不要）。`config.ini`（読み取り専用マウント）が `yomi` で読めない権限の場合だけは、警告を出して root のまま起動します。
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
3. `docker compose build --build-arg GIT_SHA=<短いハッシュ>` → `docker compose up -d`（Dockerfile の `ARG GIT_SHA` を `ENV YOMI_KAI_VERSION` にして、`y.status` にバージョンとして表示する。`ARG` はコードのコピーより後なので、値が変わっても再ビルドされるのは最後の層だけ）。ビルドに失敗した場合は git を元に戻して終了（旧版のコンテナは動き続ける）。ビルドしたイメージが動作中のコンテナと同じ（動作中のコミットやそれより古いコミットを指定した場合）なら、コンテナを入れ替えずに成功で終了。`up -d` が失敗した場合は 6. のロールバックを行う。
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
