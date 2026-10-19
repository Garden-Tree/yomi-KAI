"""GuildSession のテスト（偽のボイスクライアント・偽のエンジンを使う）。"""

import asyncio
import threading
import time

from yomi_kai.session import AFK_NOTICE, PREFETCH_CHUNKS, SYNTH_FAILED_NOTICE, GuildSession
from yomi_kai.tts import Speaker

ZUNDA = "ずんだもん"
TSUMUGI = "春日部つむぎ"


class FakeEngine:
    """テキストごとに合成時間を変えられる偽エンジン。"""

    def __init__(self, delays=None, fail=()):
        self.delays = delays or {}
        self.fail = set(fail)
        self.calls = []

    @property
    def default_speaker(self):
        return ZUNDA

    def speakers(self):
        return [Speaker(ZUNDA, "fake"), Speaker(TSUMUGI, "fake")]

    def synthesize(self, text, speaker, speed=1.0):
        self.calls.append((text, speaker, speed))
        time.sleep(self.delays.get(text, 0))
        if text in self.fail:
            raise RuntimeError("synthesis failed")
        return f"{speaker}:{text}".encode()


class FakeVoiceClient:
    """discord.VoiceClient と同様に、別スレッドから after コールバックを呼ぶ。"""

    def __init__(self, play_time=0.01, auto_finish=True):
        self.play_time = play_time
        self.auto_finish = auto_finish
        self.played = []
        self.disconnected = False
        self.stopped = False
        self._playing = False
        self._after = None
        self._timer = None
        self._lock = threading.Lock()

    def play(self, source, *, after=None):
        with self._lock:
            assert not self._playing, "play() called while already playing"
            self._playing = True
            self._after = after
        self.played.append(source)
        if self.auto_finish:
            self._timer = threading.Timer(self.play_time, self._finish)
            self._timer.start()

    def _finish(self):
        with self._lock:
            if not self._playing:
                return
            self._playing = False
            after, self._after = self._after, None
        if after is not None:
            after(None)

    def is_playing(self):
        return self._playing

    def stop(self):
        self.stopped = True
        if self._timer is not None:
            self._timer.cancel()
        self._finish()

    async def disconnect(self, *, force=False):
        self.disconnected = True


class FakeChannel:
    id = 42

    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


def make_session(engine, afk_time=60.0):
    created = []

    def factory(wav):
        created.append(wav)
        return wav

    session = GuildSession(1, engine, afk_time=afk_time, source_factory=factory)
    return session, created


def wait_until(predicate, timeout=5.0):
    async def _wait():
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("timed out")
            await asyncio.sleep(0.005)

    return _wait()


def test_plays_in_enqueue_order_even_if_synthesis_speed_differs():
    async def run():
        # 1件目の合成が遅くても、先行合成された2件目が先に再生されてはいけない
        engine = FakeEngine(delays={"一": 0.1, "二": 0.0, "三": 0.05})
        session, created = make_session(engine)
        vc, ch = FakeVoiceClient(), FakeChannel()
        await session.start(vc, ch)

        assert session.enqueue("一", ZUNDA)
        assert session.enqueue("二", ZUNDA)
        assert session.enqueue("三", TSUMUGI)

        await wait_until(lambda: len(vc.played) == 3 and not vc.is_playing())
        assert vc.played == [f"{ZUNDA}:一".encode(), f"{ZUNDA}:二".encode(), f"{TSUMUGI}:三".encode()]
        # ソースは再生直前に1件ずつ生成される
        assert created == vc.played
        await session.close()
        assert vc.disconnected
        assert not session.is_connected

    asyncio.run(run())


def test_speaker_and_speed_reach_engine():
    async def run():
        engine = FakeEngine()
        session, _ = make_session(engine)
        vc = FakeVoiceClient()
        await session.start(vc, FakeChannel())

        session.enqueue("一", TSUMUGI, 1.5)
        session.enqueue("二", ZUNDA)  # 速度省略時は 1.0
        session.enqueue("三", ZUNDA, 0.5)

        await wait_until(lambda: len(vc.played) == 3 and not vc.is_playing())
        # 合成は並行なので呼ばれる順番は問わない
        assert sorted(engine.calls) == sorted([("一", TSUMUGI, 1.5), ("二", ZUNDA, 1.0), ("三", ZUNDA, 0.5)])
        await session.close()

    asyncio.run(run())


def test_close_drains_queue_and_stops_playback():
    async def run():
        engine = FakeEngine()
        session, _ = make_session(engine)
        vc, ch = FakeVoiceClient(auto_finish=False), FakeChannel()  # 再生が終わらない
        await session.start(vc, ch)
        for text in ("一", "二", "三"):
            session.enqueue(text, ZUNDA)

        await wait_until(lambda: len(vc.played) == 1)
        await session.close()

        assert vc.stopped
        assert vc.disconnected
        assert session.pending == 0
        assert len(vc.played) == 1
        assert session._worker is None
        assert not session.enqueue("四", ZUNDA)  # 切断後は受け付けない
        assert ch.sent == []  # dc では通知しない（コマンド側が送る）
        await session.close()  # 2回目も安全

    asyncio.run(run())


def test_reconnect_after_close_reads_again():
    # 話者の保持は SettingsStore 側の責務（tests/test_settings.py）。ここでは再接続後も読み上げられることを確認する
    async def run():
        session, _ = make_session(FakeEngine())
        await session.start(FakeVoiceClient(), FakeChannel())
        await session.close()

        vc = FakeVoiceClient()
        await session.start(vc, FakeChannel())
        session.enqueue("やあ", TSUMUGI)
        await wait_until(lambda: len(vc.played) == 1)
        assert vc.played == [f"{TSUMUGI}:やあ".encode()]
        await session.close()

    asyncio.run(run())


def test_synthesis_failure_notifies_and_continues():
    async def run():
        session, _ = make_session(FakeEngine(fail={"二"}))
        vc, ch = FakeVoiceClient(), FakeChannel()
        await session.start(vc, ch)
        for text in ("一", "二", "三"):
            session.enqueue(text, ZUNDA)
        await wait_until(lambda: len(vc.played) == 2 and not vc.is_playing())
        assert vc.played == [f"{ZUNDA}:一".encode(), f"{ZUNDA}:三".encode()]
        assert ch.sent == [SYNTH_FAILED_NOTICE]
        await session.close()

    asyncio.run(run())


def test_afk_timer_starts_on_connect():
    async def run():
        session, _ = make_session(FakeEngine(), afk_time=0.05)
        vc, ch = FakeVoiceClient(), FakeChannel()
        await session.start(vc, ch)  # 一度も発言がなくても切断される
        await wait_until(lambda: vc.disconnected)
        assert ch.sent == [AFK_NOTICE]
        assert not session.is_connected

    asyncio.run(run())


def test_afk_timer_resets_on_enqueue():
    async def run():
        session, _ = make_session(FakeEngine(), afk_time=0.2)
        vc, ch = FakeVoiceClient(), FakeChannel()
        await session.start(vc, ch)
        for _ in range(4):
            await asyncio.sleep(0.1)
            session.enqueue("まだいるよ", ZUNDA)
        assert not vc.disconnected  # 合計0.4秒経過しているがタイマーは張り直されている
        await wait_until(lambda: vc.disconnected)
        assert ch.sent == [AFK_NOTICE]

    asyncio.run(run())


def test_afk_timer_waits_while_playing():
    """AFK 時間を過ぎても、再生中・未再生キューがある間は切断しない。"""

    async def run():
        session, _ = make_session(FakeEngine(), afk_time=0.1)
        vc, ch = FakeVoiceClient(play_time=0.35), FakeChannel()
        await session.start(vc, ch)
        session.enqueue("長い一", ZUNDA)
        session.enqueue("長い二", ZUNDA)
        await asyncio.sleep(0.5)  # AFK 時間を大きく超えるが、まだ2件目を再生中
        assert not vc.disconnected
        await wait_until(lambda: vc.disconnected, timeout=3.0)
        assert vc.played == [f"{ZUNDA}:長い一".encode(), f"{ZUNDA}:長い二".encode()]
        assert ch.sent == [AFK_NOTICE]

    asyncio.run(run())


# --- B-6 スキップ ---


def test_skip_stops_current_and_continues():
    async def run():
        session, _ = make_session(FakeEngine())
        vc = FakeVoiceClient(auto_finish=False)  # stop() されるまで再生し続ける
        await session.start(vc, FakeChannel())
        session.enqueue("一", ZUNDA)
        session.enqueue("二", ZUNDA)
        await wait_until(lambda: len(vc.played) == 1)
        assert session.skip()
        await wait_until(lambda: len(vc.played) == 2)
        assert vc.played == [f"{ZUNDA}:一".encode(), f"{ZUNDA}:二".encode()]
        await session.close()

    asyncio.run(run())


def test_skip_while_synthesizing_drops_that_message():
    async def run():
        session, _ = make_session(FakeEngine(delays={"遅い": 0.3}))
        vc = FakeVoiceClient()
        await session.start(vc, FakeChannel())
        session.enqueue("遅い", ZUNDA)
        await asyncio.sleep(0.05)  # まだ合成中で再生は始まっていない
        assert not vc.is_playing()
        assert session.skip()
        session.enqueue("次", ZUNDA)
        await wait_until(lambda: len(vc.played) == 1)
        await asyncio.sleep(0.1)
        assert vc.played == [f"{ZUNDA}:次".encode()]
        await session.close()

    asyncio.run(run())


def test_skip_with_nothing_to_read():
    async def run():
        session, _ = make_session(FakeEngine())
        await session.start(FakeVoiceClient(), FakeChannel())
        assert not session.skip()
        assert session.skip_all() == 0
        await session.close()

    asyncio.run(run())


def test_skip_all_drops_playing_prefetched_and_queued():
    async def run():
        session, _ = make_session(FakeEngine())
        vc = FakeVoiceClient(auto_finish=False)
        await session.start(vc, FakeChannel())
        for t in ("一", "二", "三", "四"):
            session.enqueue(t, ZUNDA)
        await wait_until(lambda: len(vc.played) == 1)
        await asyncio.sleep(0.05)  # 「二」が先読み合成される
        assert session.skip_all() == 4  # 再生中1 + 先読み1 + 待ち2
        await asyncio.sleep(0.1)
        assert vc.played == [f"{ZUNDA}:一".encode()]  # 先読み済みの「二」も再生されない
        assert not session.is_busy

        # スキップ後に来たメッセージは普通に読まれる
        vc.auto_finish = True
        session.enqueue("五", ZUNDA)
        await wait_until(lambda: len(vc.played) == 2)
        assert vc.played[-1] == f"{ZUNDA}:五".encode()
        await session.close()

    asyncio.run(run())


# --- かたまりごとの合成・再生 ---


class CountingEngine(FakeEngine):
    """同時に合成している数の最大値を記録する偽エンジン。"""

    def __init__(self, delays=None, fail=(), default_delay=0.0):
        super().__init__(delays, fail)
        self.default_delay = default_delay
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def synthesize(self, text, speaker, speed=1.0):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if text not in self.delays:
                time.sleep(self.default_delay)
            return super().synthesize(text, speaker, speed)
        finally:
            with self._guard:
                self.active -= 1


def wav(text, speaker=ZUNDA):
    return f"{speaker}:{text}".encode()


def test_message_is_split_and_chunks_play_in_order_despite_delays():
    async def run():
        # 1文目の合成が一番遅くても、再生はかたまりの順番どおり（メッセージをまたいでも）
        engine = FakeEngine(delays={"一文目。": 0.15, "二文目！": 0.0, "三文目？": 0.05, "次の発言": 0.0})
        session, _ = make_session(engine)
        vc = FakeVoiceClient()
        await session.start(vc, FakeChannel())
        session.enqueue("一文目。二文目！\n三文目？", ZUNDA)
        session.enqueue("次の発言", TSUMUGI)
        await wait_until(lambda: len(vc.played) == 4 and not vc.is_playing())
        assert vc.played == [wav("一文目。"), wav("二文目！"), wav("三文目？"), wav("次の発言", TSUMUGI)]
        await session.close()

    asyncio.run(run())


def test_synthesis_concurrency_is_bounded_by_prefetch():
    async def run():
        engine = CountingEngine(default_delay=0.05)
        session, _ = make_session(engine)
        vc = FakeVoiceClient(play_time=0.01)
        await session.start(vc, FakeChannel())
        session.enqueue("".join(f"文{i}。" for i in range(8)), ZUNDA)
        session.enqueue("別の発言。もう一文。", ZUNDA)
        await wait_until(lambda: len(vc.played) == 10 and not vc.is_playing())
        assert engine.max_active == PREFETCH_CHUNKS  # 並行に合成するが、上限は超えない
        assert len(engine.calls) == 10
        await session.close()

    asyncio.run(run())


def test_skip_mid_message_skips_remaining_chunks():
    async def run():
        engine = FakeEngine()
        session, _ = make_session(engine)
        vc = FakeVoiceClient(auto_finish=False)
        await session.start(vc, FakeChannel())
        session.enqueue("一。二。三。四。", ZUNDA)
        session.enqueue("次。", TSUMUGI)
        await wait_until(lambda: len(vc.played) == 1)
        await asyncio.sleep(0.05)  # 「二。」「三。」「四。」が先読み合成される
        assert session.skip()  # 残りのかたまり（合成済みのものも）をまとめて飛ばす
        await wait_until(lambda: len(vc.played) == 2)
        await asyncio.sleep(0.05)
        assert vc.played == [wav("一。"), wav("次。", TSUMUGI)]
        assert session.skip()
        await wait_until(lambda: not session.is_busy)
        await session.close()

    asyncio.run(run())


def test_skip_all_with_prefetched_chunks():
    async def run():
        session, _ = make_session(FakeEngine())
        vc = FakeVoiceClient(auto_finish=False)
        await session.start(vc, FakeChannel())
        session.enqueue("一。二。三。", ZUNDA)
        session.enqueue("四。五。", ZUNDA)
        session.enqueue("六。", ZUNDA)
        await wait_until(lambda: len(vc.played) == 1)
        await asyncio.sleep(0.05)
        assert session.skip_all() == 3  # メッセージ単位で数える
        await asyncio.sleep(0.05)
        assert vc.played == [wav("一。")]
        assert not session.is_busy

        vc.auto_finish = True
        session.enqueue("七。八。", ZUNDA)
        await wait_until(lambda: len(vc.played) == 3)
        assert vc.played[1:] == [wav("七。"), wav("八。")]
        await session.close()

    asyncio.run(run())


def test_chunk_failure_notifies_once_per_message():
    async def run():
        engine = FakeEngine(fail={"二。", "三。", "五。"})
        session, _ = make_session(engine)
        vc, ch = FakeVoiceClient(), FakeChannel()
        await session.start(vc, ch)
        session.enqueue("一。二。三。四。", ZUNDA)
        session.enqueue("五。六。", ZUNDA)
        await wait_until(lambda: len(vc.played) == 3 and not vc.is_playing())
        await asyncio.sleep(0.05)
        assert vc.played == [wav("一。"), wav("四。"), wav("六。")]  # 失敗したかたまりだけ飛ばす
        assert ch.sent == [SYNTH_FAILED_NOTICE, SYNTH_FAILED_NOTICE]  # メッセージごとに1回
        await session.close()

    asyncio.run(run())


def test_close_cancels_in_flight_synthesis():
    async def run():
        engine = FakeEngine(delays={"遅い一。": 0.3, "遅い二。": 0.3})
        session, _ = make_session(engine)
        vc = FakeVoiceClient()
        await session.start(vc, FakeChannel())
        session.enqueue("遅い一。遅い二。", ZUNDA)
        await asyncio.sleep(0.05)
        tasks = [job.task for job in session._pipeline]
        assert len(tasks) == 2
        await session.close()
        await asyncio.sleep(0)
        assert all(t.cancelled() for t in tasks)
        assert not session._pipeline and not session.is_busy
        await asyncio.sleep(0.4)
        assert vc.played == []

    asyncio.run(run())


def test_afk_waits_for_remaining_chunks():
    async def run():
        session, _ = make_session(FakeEngine(), afk_time=0.1)
        vc, ch = FakeVoiceClient(play_time=0.15), FakeChannel()
        await session.start(vc, ch)
        session.enqueue("一。二。三。", ZUNDA)
        await asyncio.sleep(0.3)  # AFK 時間を超えたが、まだ残りのかたまりを再生中
        assert not vc.disconnected
        await wait_until(lambda: vc.disconnected, timeout=3.0)
        assert vc.played == [wav("一。"), wav("二。"), wav("三。")]

    asyncio.run(run())
