"""ヘルプ（``y.help`` / ``/help``）。

``y.help`` は目的ごとにまとめたコマンドの一覧、``y.help コマンド名`` はそのコマンドの使い方と例を表示する。
一覧には Bot に登録されているコマンドだけを出す（ENABLE_SUDDEN_DEATH = False なら sd は出さない）。
コマンドを追加したら :data:`HELP_GROUPS` にも追加する（tests/test_help.py で確かめている）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..settings import PITCH_MAX, PITCH_MIN, SPEED_MAX, SPEED_MIN
from .common import EMBED_COLOR, handle_command_error, invalid_command_notice, reply

logger = logging.getLogger(__name__)

WEBSITE_URL = "https://yomi-kai.gadelog.com"
ADMIN_MARK = "（管理）"
# スラッシュコマンドの候補の上限（Discord の制限）
MAX_AUTOCOMPLETE = 25


@dataclass(frozen=True)
class HelpEntry:
    """1つのコマンドのヘルプ。文中の ``{p}`` はプレフィックスに置き換える。"""

    name: str
    summary: str
    usage: tuple[str, ...]
    examples: tuple[str, ...] = ()
    # 「サーバー管理」の権限が必要な操作（無ければ空）
    admin: str = ""


def _speed_range() -> str:
    return f"{SPEED_MIN:g}〜{SPEED_MAX:g}"


HELP_GROUPS: tuple[tuple[str, tuple[HelpEntry, ...]], ...] = (
    (
        "はじめに",
        (
            HelpEntry(
                "c",
                "VC に接続して、このチャンネルを読み上げる",
                (
                    "`{p}c`: あなたのいるボイスチャンネルに接続し、コマンドを打ったチャンネルを読み上げます",
                    "表示されるメニューで、サーバー全体の話者を選べます",
                ),
                ("`{p}c`",),
            ),
            HelpEntry("dc", "VC から切断する", ("`{p}dc`: ボイスチャンネルから切断します",), ("`{p}dc`",)),
            HelpEntry(
                "s",
                "読み上げを飛ばす",
                (
                    "`{p}s`: 読み上げ中のメッセージを飛ばします",
                    "`{p}s all`: 溜まっている分もすべて飛ばします",
                ),
                ("`{p}s`", "`{p}s all`"),
            ),
        ),
    ),
    (
        "自分の声",
        (
            HelpEntry(
                "voice",
                "自分の話者（声）を選ぶ",
                (
                    "`{p}voice`: メニューから選びます",
                    "`{p}voice 名前`: 名前で選びます（一部でも可）",
                    "`{p}voice list`: 使える話者の一覧を表示します",
                    "`{p}voice reset`: 個人設定を解除し、サーバーの話者に戻します",
                ),
                ("`{p}voice ずんだもん`",),
            ),
            HelpEntry(
                "speed",
                "自分の読み上げ速度",
                (
                    f"`{{p}}speed 倍率`: {_speed_range()}倍で設定します",
                    "`{p}speed`: 現在の速度を表示します",
                    "`{p}speed reset`: 個人設定を解除します",
                ),
                ("`{p}speed 1.5`",),
            ),
            HelpEntry(
                "pitch",
                "自分の声の高さ",
                (
                    f"`{{p}}pitch 高さ`: {PITCH_MIN}〜+{PITCH_MAX}の整数で設定します（0 が標準）",
                    "`{p}pitch`: 現在の高さを表示します",
                    "`{p}pitch reset`: 個人設定を解除します",
                ),
                ("`{p}pitch 3`", "`{p}pitch -2`"),
            ),
            HelpEntry(
                "mydict",
                "自分の発言だけに使う個人辞書",
                (
                    "`{p}mydict add 単語 読み`: 登録します",
                    "`{p}mydict del 単語`: 削除します",
                    "`{p}mydict list`: 一覧を表示します",
                    "サーバーの辞書より先に使います。登録できる数は`{p}plan`で確認できます",
                ),
                ("`{p}mydict add yomi よみ`",),
            ),
        ),
    ),
    (
        "サーバーの設定",
        (
            HelpEntry(
                "dict",
                "サーバーの読み上げ辞書",
                (
                    "`{p}dict add 単語 読み`: 登録します",
                    "`{p}dict del 単語`: 削除します",
                    "`{p}dict list`: 一覧を表示します",
                    "`{p}dict export`: JSON ファイルで書き出します",
                    "`{p}dict import`: 添付した JSON ファイルを読み込みます",
                    "`{p}dict help`: 辞書のヘルプを表示します",
                    "登録できる数は`{p}plan`で確認できます",
                ),
                ("`{p}dict add w わら`", "`{p}dict del w`"),
                admin="`dict import`",
            ),
            HelpEntry(
                "read",
                "URL・絵文字・コード・ネタバレの読み方",
                (
                    "`{p}read list`: 現在の設定を表示します",
                    "`{p}read url on|off`: URL を「URL」と読むか",
                    "`{p}read emoji on|off`: サーバー絵文字の名前を読むか",
                    "`{p}read code on|off`: コードブロックの中身を読むか",
                    "`{p}read spoiler on|off`: ネタバレの中身を読むか",
                ),
                ("`{p}read code on`",),
                admin="設定の変更",
            ),
            HelpEntry(
                "readname",
                "発言者の名前を読み上げるか",
                (
                    "`{p}readname on|off`: 名前を読み上げるか設定します（続けて発言したときは省略）",
                    "`{p}readname`: 現在の設定を表示します",
                ),
                ("`{p}readname on`",),
            ),
            HelpEntry(
                "speedup",
                "読み上げ待ちが溜まったら速く読むか",
                (
                    "`{p}speedup on|off`: 溜まったときに速く読むか設定します",
                    "`{p}speedup`: 現在の設定を表示します",
                ),
                ("`{p}speedup off`",),
            ),
            HelpEntry(
                "announce",
                "VC の入退室を読み上げるか",
                (
                    "`{p}announce on|off`: 入退室を読み上げるか設定します",
                    "`{p}announce`: 現在の設定を表示します",
                ),
                ("`{p}announce off`",),
            ),
            HelpEntry(
                "readbots",
                "ほかの Bot・Webhook の発言も読み上げるか",
                (
                    "`{p}readbots on|off`: Bot・Webhook の発言も読み上げるか設定します",
                    "`{p}readbots`: 現在の設定を表示します",
                ),
                ("`{p}readbots on`",),
                admin="このコマンド",
            ),
            HelpEntry(
                "ignore",
                "読み上げない人・ロール",
                (
                    "`{p}ignore add @ユーザー|@ロール`: 読み上げないようにします",
                    "`{p}ignore del @ユーザー|@ロール`: 解除します",
                    "`{p}ignore list`: 一覧を表示します",
                ),
                ("`{p}ignore add @太郎`",),
                admin="登録・解除",
            ),
            HelpEntry(
                "autojoin",
                "VC に人が入ったら自動で接続",
                (
                    "`{p}autojoin add`: いまいる VC に人が入ったら接続し、このチャンネルを読み上げます",
                    "`{p}autojoin del`: いまいる VC の自動接続を解除します",
                    "`{p}autojoin list`: 一覧を表示します",
                ),
                ("VC に入った状態で、読み上げたいチャンネルで`{p}autojoin add`",),
                admin="登録・解除",
            ),
        ),
    ),
    (
        "情報",
        (
            HelpEntry("status", "Bot の動作状況", ("`{p}status`: 応答時間・接続数・稼働時間などを表示します",)),
            HelpEntry(
                "plan",
                "このサーバーのプランと上限",
                ("`{p}plan`: このサーバーで読み上げる文字数・辞書の登録数・話者の数などを表示します",),
            ),
            HelpEntry("credit", "読み上げ音声のクレジット", ("`{p}credit`: 使っている音声のクレジットを表示します",)),
            HelpEntry(
                "help",
                "このヘルプ",
                (
                    "`{p}help`: コマンドの一覧を表示します",
                    "`{p}help コマンド名`: そのコマンドの使い方と例を表示します",
                ),
                ("`{p}help voice`",),
            ),
        ),
    ),
    (
        "おまけ",
        (
            HelpEntry(
                "sd",
                "「突然の死」風のテキスト",
                (
                    "`{p}sd`: 既定のメッセージで出力します",
                    "`{p}sd 言葉`: 言葉を囲んで出力します",
                    "`{p}sd help`: 詳しいヘルプを表示します",
                ),
                ("`{p}sd 締め切り`",),
            ),
        ),
    ),
)

HELP_ENTRIES: dict[str, HelpEntry] = {entry.name: entry for _, entries in HELP_GROUPS for entry in entries}


def _fill(text: str, prefix: str) -> str:
    return text.replace("{p}", prefix)


def normalize_query(query: str, prefix: str) -> str:
    """``"y.dict add"`` / ``"/Voice"`` → ``"dict"`` / ``"voice"``（先頭の語だけを使う）。"""
    words = query.strip().split()
    if not words:
        return ""
    name = words[0].lower()
    for head in (prefix.lower(), "/"):
        if head and name.startswith(head):
            name = name[len(head) :]
    return name


def visible_entries(registered: set[str]) -> list[tuple[str, list[HelpEntry]]]:
    """登録されているコマンドだけを残した HELP_GROUPS（空になったまとまりは除く）。"""
    groups = []
    for title, entries in HELP_GROUPS:
        shown = [entry for entry in entries if entry.name in registered]
        if shown:
            groups.append((title, shown))
    return groups


def overview_embed(prefix: str, registered: set[str]) -> discord.Embed:
    """``y.help`` の一覧。"""
    embed = discord.Embed(
        title="yomi-KAI の使い方",
        description=(
            f"ボイスチャンネルに入って`{prefix}c`を打つと、そのチャンネルの発言を読み上げます。\n"
            f"詳しい使い方は`{prefix}help コマンド名`（例: `{prefix}help voice`）で表示します。"
        ),
        color=EMBED_COLOR,
    )
    for title, entries in visible_entries(registered):
        lines = [f"`{prefix}{entry.name}` {entry.summary}{ADMIN_MARK if entry.admin else ''}" for entry in entries]
        embed.add_field(name=title, value="\n".join(lines), inline=False)
    embed.add_field(
        name="ほかに",
        value=(
            f"{ADMIN_MARK}は、一部の操作に「サーバー管理」の権限が必要です。\n"
            f"上限（文字数・辞書の数など）は`{prefix}plan`で確認できます。\n"
            f"サイト: {WEBSITE_URL}"
        ),
        inline=False,
    )
    embed.set_footer(text="どのコマンドもスラッシュコマンド（/c、/voice など）として使えます。")
    return embed


def command_embed(entry: HelpEntry, prefix: str) -> discord.Embed:
    """``y.help コマンド名`` の、1つのコマンドの使い方と例。"""
    embed = discord.Embed(title=f"{prefix}{entry.name}", description=f"{entry.summary}。", color=EMBED_COLOR)
    embed.add_field(name="使い方", value="\n".join(_fill(line, prefix) for line in entry.usage), inline=False)
    if entry.examples:
        embed.add_field(name="例", value="\n".join(_fill(line, prefix) for line in entry.examples), inline=False)
    if entry.admin:
        embed.add_field(name="権限", value=f"{entry.admin}には「サーバー管理」の権限が必要です。", inline=False)
    embed.set_footer(text=f"スラッシュコマンド /{entry.name} としても使えます。")
    return embed


def unknown_command_notice(query: str, prefix: str) -> str:
    return f"「{query}」というコマンドはありません。`{prefix}help`で一覧を確認できます"


def autocomplete_names(current: str, registered: set[str]) -> list[str]:
    """スラッシュコマンドの候補。入力で始まるものを先に、含むものを後に（一覧の順）。"""
    current = current.strip().lower().lstrip("/")
    names = [name for name in HELP_ENTRIES if name in registered]
    starts = [name for name in names if name.startswith(current)]
    contains = [name for name in names if current in name and name not in starts]
    return (starts + contains)[:MAX_AUTOCOMPLETE]


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self.config = config

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        await handle_command_error(ctx, error, invalid_notice=invalid_command_notice(self.config.prefix))

    def registered(self) -> set[str]:
        """Bot に登録されているコマンド（トップレベル）の名前。"""
        return {command.name for command in self.bot.commands}

    @commands.hybrid_command(name="help", description="コマンドの一覧と使い方を表示します")
    @app_commands.describe(command="使い方を見たいコマンドの名前（省略すると一覧を表示）")
    async def help_command(self, ctx: commands.Context, *, command: str | None = None) -> None:
        prefix = self.config.prefix
        registered = self.registered()
        if not command or not command.strip():
            await reply(ctx, embed=overview_embed(prefix, registered))
            logger.info("helpを表示")
            return
        name = normalize_query(command, prefix)
        entry = HELP_ENTRIES.get(name)
        if entry is None or name not in registered:
            await reply(ctx, unknown_command_notice(command.strip(), prefix), private=True)
            return
        await reply(ctx, embed=command_embed(entry, prefix))
        logger.info("help %sを表示", name)

    @help_command.autocomplete("command")
    async def help_command_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=name, value=name) for name in autocomplete_names(current, self.registered())]
