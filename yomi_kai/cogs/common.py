"""コマンド共通の小さなヘルパー（プレフィックス / スラッシュの両方から呼ばれる hybrid コマンド用）。"""

from __future__ import annotations

import logging
from typing import Any

from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x3399CC
SAVE_FAILED_NOTICE = "設定の保存に失敗しました"
MANAGE_GUILD_NOTICE = "このコマンドを使うには「サーバー管理」の権限が必要です"


def is_slash(ctx: commands.Context) -> bool:
    """スラッシュコマンドとして呼ばれたか。"""
    return ctx.interaction is not None


async def reply(ctx: commands.Context, content: str | None = None, *, private: bool = False, **kwargs: Any) -> Any:
    """返信する。プレフィックスでは従来どおりチャンネルへの送信、スラッシュではインタラクションへの応答になる。

    ``private=True`` の返信は、スラッシュコマンドのときだけ本人にしか見えない（ephemeral）メッセージにする。
    """
    return await ctx.send(content, ephemeral=private and is_slash(ctx), **kwargs)


def original_error(error: BaseException) -> BaseException:
    """CommandInvokeError / HybridCommandError などに包まれた元の例外を取り出す。"""
    while isinstance(
        error, (commands.CommandInvokeError, commands.HybridCommandError, app_commands.CommandInvokeError)
    ):
        error = error.original
    return error


def guild_only_check(ctx: commands.Context) -> bool:
    """サーバー内でのみ使える Cog の ``cog_check`` 用。DM なら NoPrivateMessage（Bot.on_command_error が案内する）。

    グループの ``commands.guild_only()`` はプレフィックスのサブコマンドには効かない
    （invoke_without_command のグループはサブコマンドの前にグループのチェックを実行しない）ため、
    Cog 単位でここに一本化する。スラッシュコマンドを DM に出さないための指定は ``app_commands.guild_only()`` で行う。
    """
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    return True


def invalid_command_notice(prefix: str, help_command: str = "help") -> str:
    return f"コマンドが間違っています。`{prefix}{help_command}`を参照してください"


async def reply_invalid(ctx: commands.Context, prefix: str, help_command: str = "help") -> None:
    """コマンドの書き方が間違っているときの案内（本人にだけ見える）。"""
    await reply(ctx, invalid_command_notice(prefix, help_command), private=True)
    logger.info("コマンドが間違っています: %s", ctx.command.qualified_name if ctx.command else None)


async def handle_command_error(
    ctx: commands.Context,
    error: BaseException,
    *,
    invalid_notice: str,
    failed_notice: str = SAVE_FAILED_NOTICE,
) -> None:
    """Cog の ``cog_command_error`` の共通処理。利用者に案内を返す（ログは Bot.on_command_error が出す）。

    - 権限不足 → :data:`MANAGE_GUILD_NOTICE`
    - 引数の数・値が不正（例: ``y.dict add 単語``）→ ``invalid_notice``
    - ファイルが壊れている・書き込めない（OSError / ValueError）→ ``failed_notice``

    いずれも返信しないとスラッシュコマンドが「考え中…」のまま残るため、本人にだけ見える返信をする。
    """
    error = original_error(error)
    if isinstance(error, commands.MissingPermissions):
        await reply(ctx, MANAGE_GUILD_NOTICE, private=True)
    elif isinstance(error, commands.UserInputError):
        await reply(ctx, invalid_notice, private=True)
    elif isinstance(error, (OSError, ValueError)):
        await reply(ctx, failed_notice, private=True)
