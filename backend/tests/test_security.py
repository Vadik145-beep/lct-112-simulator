"""Password hashing must not stall the event loop (#71)."""

import asyncio
import time

from app.security import hash_password, verify_password, verify_password_async


async def _worst_loop_gap(work) -> float:
    """Runs `work` while a watchdog wakes every 10 ms; returns the longest extra pause."""
    stop = asyncio.Event()

    async def watchdog() -> float:
        worst = 0.0
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            worst = max(worst, now - last - 0.01)
            last = now
        return worst

    watch = asyncio.create_task(watchdog())
    await asyncio.sleep(0.02)  # let the watchdog take its first timestamp
    await work()
    stop.set()
    return await watch


async def test_inline_verification_stalls_the_loop() -> None:
    """Documents why the async wrapper exists: one inline argon2 call freezes the loop
    for its whole duration (tens of milliseconds), and a class logging in at once would
    freeze it for seconds."""
    password_hash = hash_password("secret")

    async def inline() -> None:
        for _ in range(3):
            assert verify_password("secret", password_hash)
            await asyncio.sleep(0)

    gap = await _worst_loop_gap(inline)
    assert gap > 0.03, f"argon2 got suspiciously cheap: {gap * 1000:.0f} ms"


async def test_threaded_verification_keeps_the_loop_responsive() -> None:
    """Twenty concurrent verifications in worker threads: the loop keeps ticking."""
    password_hash = hash_password("secret")

    async def burst() -> None:
        checks = [verify_password_async("secret", password_hash) for _ in range(20)]
        results = await asyncio.gather(*checks)
        assert all(results)
        assert not await verify_password_async("wrong", password_hash)

    gap = await _worst_loop_gap(burst)
    # Inline, the same burst stalls the loop for the whole 20 × ~60 ms.
    assert gap < 0.1, f"event loop stalled for {gap * 1000:.0f} ms during the burst"
