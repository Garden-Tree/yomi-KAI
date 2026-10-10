"""話者選択のドロップダウンUI。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import discord

from .tts import Speaker

logger = logging.getLogger(__name__)

# Discord の Select は25択まで
MAX_OPTIONS = 25


class Dropdown(discord.ui.Select):
    def __init__(self, speakers: Sequence[Speaker], current: str, on_select: Callable[[str], None]) -> None:
        self._on_select = on_select
        options = [discord.SelectOption(label=s.name, description=s.description) for s in speakers[:MAX_OPTIONS]]
        super().__init__(placeholder=f"初期設定は{current}です", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        self._on_select(selected)
        await interaction.response.send_message(f"{selected}が選択されました")
        logger.info("話者を%sに変更しました", selected)


class DropdownView(discord.ui.View):
    def __init__(self, speakers: Sequence[Speaker], current: str, on_select: Callable[[str], None]) -> None:
        super().__init__()
        self.add_item(Dropdown(speakers, current, on_select))
