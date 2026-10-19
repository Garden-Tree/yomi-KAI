"""インスタンスのプールと、専用スレッドで回すイベントループ（voicevox_core を import しない）。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Coroutine, Iterable
from contextlib import asynccontextmanager
from typing import Any


class InstancePool[T]:
    """空いているインスタンスを1つ借りて使い、終わったら返すプール（1つのイベントループ内で使う）。

    1つのインスタンスが同時に2つの処理へ貸し出されることはない。
    すべて貸し出し中なら、どれかが返されるまで ``borrow`` で待つ。
    """

    def __init__(self, items: Iterable[T]) -> None:
        self._free: asyncio.Queue[T] = asyncio.Queue()
        for item in items:
            self._free.put_nowait(item)
        self.size = self._free.qsize()
        if self.size == 0:
            raise ValueError("pool must not be empty")

    @asynccontextmanager
    async def borrow(self) -> AsyncIterator[T]:
        item = await self._free.get()
        try:
            yield item
        finally:
            self._free.put_nowait(item)


class LoopThread:
    """専用スレッドでイベントループを回し、他のスレッドからコルーチンを実行して結果を待てるようにする。"""

    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self._thread.start()

    def run[R](self, coro: Coroutine[Any, Any, R]) -> R:
        """``coro`` をこのループで実行し、完了まで（呼び出し元のスレッドで）待つ。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join()
