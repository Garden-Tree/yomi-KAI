"""話者選択のドロップダウンUI。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import discord

from .tts import Speaker

# Discord の Select は25択まで
MAX_OPTIONS = 25

# 選択時に呼ばれる。押したユーザーは interaction.user で分かる。応答（send_message）も呼び出し側で行う
OnSelect = Callable[[discord.Interaction, str], Awaitable[None]]


class Dropdown(discord.ui.Select):
    def __init__(self, speakers: Sequence[Speaker], placeholder: str, on_select: OnSelect) -> None:
        self._on_select = on_select
        options = [discord.SelectOption(label=s.name, description=s.description) for s in speakers[:MAX_OPTIONS]]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class DropdownView(discord.ui.View):
    def __init__(self, speakers: Sequence[Speaker], placeholder: str, on_select: OnSelect) -> None:
        super().__init__()
        self.add_item(Dropdown(speakers, placeholder, on_select))
