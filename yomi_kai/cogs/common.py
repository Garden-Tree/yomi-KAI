"""コマンド共通の小さなヘルパー（プレフィックス / スラッシュの両方から呼ばれる hybrid コマンド用）。"""

from __future__ import annotations

from typing import Any

from discord import app_commands
from discord.ext import commands

EMBED_COLOR = 0x3399CC
SAVE_FAILED_NOTICE = "設定の保存に失敗しました"


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
