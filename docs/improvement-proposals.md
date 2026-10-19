# yomi-KAI 改善提案書（機能追加案）

対象: `yomi-KAI.py`（※その後 `yomi_kai/` パッケージへ分割済み。各項目の関数名・変数名は旧構成のものなので、`docs/architecture.md` の「コード構成」を参照して読み替えること）。
このドキュメントは実装担当のAIモデル／開発者向けの指示書です。各項目は独立して実装できます。**優先度順**に並べています。

> **バグ修正（旧A項目）は 2026-07-02 にすべて実装・動作確認済みのため本書から削除しました。**
> 実装内容: TTS合成の非同期化、話者設定のサーバー別化、辞書置換の`.format()`クラッシュ修正、メンションのNoneガード、AFKタイマー再設計、音声ファイル削除タイミング修正、その他コマンドのクラッシュ対応など。詳細はコミット履歴を参照してください。

> **B-1（サーバーごとの設定永続化）・B-3（文字数制限）・B-6（スキップ）・B-7（話者の動的列挙）・B-8（コードブロック除去）・B-10（話者変更コマンド）は 2026-10-10 に実装済みのため削除しました。**
> B-1 / B-10 は `settings/{guild_id}.json`（`yomi_kai/settings.py` の `SettingsStore`）と `y.voice` / `y.speed` コマンドとして実装（ユーザーごとの話者・速度にも対応）。

> **B-2（スラッシュコマンド）・B-5（入退室の読み上げ）・B-9（添付ファイル・スタンプの読み上げ）は 2026-10-19 に実装済みのため削除しました。**
> B-2 は処理を別関数に切り出す方式ではなく、discord.py の hybrid コマンドで実装（`y.c` と `/c` が同じ実装）。B-5 は `y.announce on|off`（`announce_voice`）で切り替え可能。
> あわせて、接続監視（ハートビート・Docker の HEALTHCHECK・切断通知の Webhook）、`y.voice 名前` / `y.voice list`、VOICEVOX 合成の並行化（Synthesizer のプール）・長文の分割合成、Docker の一般ユーザー実行も追加しました。詳細は `docs/architecture.md` を参照してください。

---

## B. 機能追加案（優先度順）

### B-4. 発言者名の読み上げ

- **概要:** 「（名前）、こんにちは」のように誰の発言か分かるようにする。連続発言時は名前を省略。
- **手順:**
  1. ギルドごとに `last_author: dict[int, int]` を保持。
  2. `on_message` で直前の発言者と異なる場合のみ `read_msg = f"{message.author.display_name}、" + read_msg`。
  3. サーバー設定（`SettingsStore`、`settings/{guild_id}.json`）でオン/オフ可能にする（`read_name` キー）。表示名に記号が多いユーザー対策として、辞書置換を名前にも適用すると親切。

---

## C. 実装時の共通注意事項

- 実装後は必ず `python -m py_compile yomi-KAI.py` で構文確認し、可能ならテスト用サーバーで `y.c` → 発言 → `y.dc` の一連の動作を確認すること。
- `config.ini` に項目を足す場合は `config.ini.example` と README の「設定」節も同時に更新すること。既存ユーザーの config に無い項目は `config.getboolean(..., fallback=...)` / `config.get(..., fallback=...)` で後方互換にすること。
- `dist/` と `build_cache/` は生成物なので触らないこと。
