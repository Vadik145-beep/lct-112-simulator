"""Замер входа под нагрузкой: 20 входов подряд, 5 и 20 параллельно (#71, docs/PERFORMANCE.md).

Запуск с любой машины, у которой есть доступ к стенду:

    BENCH_BASE_URL=https://155-212-186-2.sslip.io BENCH_LOGIN=student BENCH_PASSWORD=... \
        uv run --project backend python scripts/bench_login.py

Пароль только через переменную окружения, в вывод не попадает. Времена включают сеть и TLS.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import statistics
import sys
import time

import httpx

BASE = os.environ.get("BENCH_BASE_URL", "http://localhost:8080")
LOGIN = os.environ.get("BENCH_LOGIN", "student1")
PASSWORD = os.environ.get("BENCH_PASSWORD") or os.environ.get("E2E_PASSWORD")


def login_once() -> tuple[int, int]:
    started = time.perf_counter()
    response = httpx.post(
        BASE + "/api/auth/login", json={"login": LOGIN, "password": PASSWORD}, timeout=30
    )
    return response.status_code, round((time.perf_counter() - started) * 1000)


def burst(size: int) -> list[tuple[int, int]]:
    with cf.ThreadPoolExecutor(size) as pool:
        return list(pool.map(lambda _: login_once(), range(size)))


def report(title: str, results: list[tuple[int, int]]) -> None:
    times = sorted(ms for _, ms in results)
    codes = sorted({code for code, _ in results})
    print(
        f"{title}: min {times[0]} мс, медиана {statistics.median(times):.0f} мс, "
        f"max {times[-1]} мс, коды {codes}"
    )


def main() -> int:
    if not PASSWORD:
        print("нужен BENCH_PASSWORD (или E2E_PASSWORD)", file=sys.stderr)
        return 2
    print(f"стенд {BASE}, пользователь {LOGIN}")
    report("20 подряд", [login_once() for _ in range(20)])
    report("5 параллельно", burst(5))
    report("20 параллельно", burst(20))
    return 0


if __name__ == "__main__":
    sys.exit(main())
