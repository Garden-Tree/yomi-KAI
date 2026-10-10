# yomi-KAI 改善提案書（機能追加案）

対象: `yomi-KAI.py`（※その後 `yomi_kai/` パッケージへ分割済み。各項目の関数名・変数名は旧構成のものなので、`docs/architecture.md` の「コード構成」を参照して読み替えること）。
このドキュメントは実装担当のAIモデル／開発者向けの指示書です。各項目は独立して実装できます。**優先度順**に並べています。

> **バグ修正（旧A項目）は 2026-07-02 にすべて実装・動作確認済みのため本書から削除しました。**
> 実装内容: TTS合成の非同期化、話者設定のサーバー別化、辞書置換の`.format()`クラッシュ修正、メンションのNoneガード、AFKタイマー再設計、音声ファイル削除タイミング修正、その他コマンドのクラッシュ対応など。詳細はコミット履歴を参照してください。

> **B-1（サーバーごとの設定永続化）・B-3（文字数制限）・B-6（スキップ）・B-7（話者の動的列挙）・B-8（コードブロック除去）・B-10（話者変更コマンド）は 2026-10-10 に実装済みのため削除しました。**
> B-1 / B-10 は `settings/{guild_id}.json`（`yomi_kai/settings.py` の `SettingsStore`）と `y.voice` / `y.speed` コマンドとして実装（ユーザーごとの話者・速度にも対応）。

---

## B. 機能追加案（優先度順）

### B-2. スラッシュコマンド対応

- **概要:** `y.c` 等のプレフィックスコマンドを `/connect` `/disconnect` `/dict` `/skip` などの App Commands として併設する。message_content intent に依存しない操作系になり、UI（Dropdown）とも相性がよい。
- **手順:**
  1. `discord.app_commands` を使用。`bot.tree.command()` で各コマンドを定義し、既存コマンドと実装を共有する（中身を通常の関数に切り出し、両方から呼ぶ）。
  2. `on_ready`ではなく`setup_hook` で `await bot.tree.sync()`。
  3. 既存プレフィックスコマンドは互換のため残す。
  4. 応答は `interaction.response.send_message` を使い、エラーは ephemeral にする。

### B-4. 発言者名の読み上げ

- **概要:** 「（名前）、こんにちは」のように誰の発言か分かるようにする。連続発言時は名前を省略。
- **手順:**
  1. ギルドごとに `last_author: dict[int, int]` を保持。
  2. `on_message` で直前の発言者と異なる場合のみ `read_msg = f"{message.author.display_name}、" + read_msg`。
  3. サーバー設定（`SettingsStore`、`settings/{guild_id}.json`）でオン/オフ可能にする（`read_name` キー）。表示名に記号が多いユーザー対策として、辞書置換を名前にも適用すると親切。

### B-5. 入退室の読み上げ

- **概要:** 接続中のVCへの入退室を「〇〇さんが参加しました」と読み上げる。
- **手順:**
  1. `on_voice_state_update` に追記。`before.channel != after.channel` かつ `voice_client.channel` が関係する場合に、読み上げテキストを生成して既存の合成→enqueue パスに流す。
  2. `on_message` 内の合成～enqueue 処理を `async def speak(guild, text)` として関数に切り出しておくと、ここから再利用できる。

### B-9. 添付ファイル・スタンプの読み上げ

- **概要:** 画像だけのメッセージは現在無音（`build_read_text` が空文字を返しスキップ）。「画像」「ファイル」「スタンプ」と読む。
- **手順:** `on_message` で `message.attachments` / `message.stickers` を確認し、`read_msg` の末尾に「 画像」等を追記。content が空で添付のみの場合も読み上げ対象にする。

---

## C. 実装時の共通注意事項

- 実装後は必ず `python -m py_compile yomi-KAI.py` で構文確認し、可能ならテスト用サーバーで `y.c` → 発言 → `y.dc` の一連の動作を確認すること。
- `config.ini` に項目を足す場合は `config.ini.example` と README の「設定」節も同時に更新すること。既存ユーザーの config に無い項目は `config.getboolean(..., fallback=...)` / `config.get(..., fallback=...)` で後方互換にすること。
- `dist/` と `build_cache/` は生成物なので触らないこと。
- 合成～enqueue 処理（`on_message` 内）を `speak(guild, text)` に切り出すと、B-5（入退室読み上げ）など複数の機能から再利用でき、見通しがよくなる。
