"""Нагрузка через HTTP по цифрам ТЗ (docs/PERFORMANCE.md, «Нагрузка по ТЗ»).

Два сценария:

* ``browse`` — N пользователей (обучающиеся seed и ``load01..``) входят и, как открытые вкладки
  кабинета, раз в секунду читают задания, журнал и карточку. Требование ТЗ: «отклик интерфейса
  не более 2 с при нагрузке до 100 пользователей».
* ``cards`` — N обучающихся одновременно работают карточки в одном занятии: открыть → «Принята»
  → «Начало реагирования» → «Прибытие» → «Проведение работ» → «Работы завершены» → следующая.
  Требование ТЗ: «не менее 20 одновременных сессий (вызовов/карточек)». Нужно идущее занятие
  с этими обучающимися: ``docker compose exec backend python -m app.load_monitor --students 20
  --seconds 1 --keep`` создаёт ``load01..load20``, группу и занятие.

Запуск (пароль обучающихся seed — SEED_PASSWORD стенда):

    LOAD_BASE_URL=https://127.0.0.1:8443 LOAD_PASSWORD=... \\
        uv run --project backend python load/load_http.py browse --users 100 --seconds 60
    LOAD_BASE_URL=... LOAD_PASSWORD=... uv run --project backend python load/load_http.py cards \\
        --users 20 --seconds 120

Печатает таблицу в Markdown: запросов, ошибок, p50/p95/max по каждому запросу и суммарный RPS.
Сертификат стенда самоподписанный — проверка TLS выключена.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

BASE = os.environ.get("LOAD_BASE_URL", "https://127.0.0.1:8443")
PASSWORD = os.environ.get("LOAD_PASSWORD") or os.environ.get("SEED_PASSWORD") or "Demo12345"
CHAIN = [
    ("accepted", {}),
    ("response_started", {"order_number": "14-217", "comment": "Направлен дежурный слесарь"}),
    ("arrived", {}),
    ("works_started", {"comment": "Работы на месте"}),
    ("works_done", {"comment": "Работы выполнены, пострадавших нет"}),
]


@dataclass
class Stats:
    times: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    started: float = 0.0
    finished: float = 0.0

    def add(self, key: str, seconds: float, ok: bool) -> None:
        self.times[key].append(seconds * 1000)
        if not ok:
            self.errors[key] += 1

    def table(self) -> str:
        rows = [
            "| Запрос | Запросов | Ошибок | p50, мс | p95, мс | max, мс |",
            "|---|---|---|---|---|---|",
        ]
        total = 0
        for key in sorted(self.times):
            values = sorted(self.times[key])
            total += len(values)
            p95 = values[int(len(values) * 0.95) - 1] if len(values) >= 20 else values[-1]
            rows.append(
                f"| `{key}` | {len(values)} | {self.errors[key]} | {statistics.median(values):.0f} "
                f"| {p95:.0f} | {values[-1]:.0f} |"
            )
        seconds = max(self.finished - self.started, 0.001)
        rows.append(f"\nВсего {total} запросов за {seconds:.0f} с — {total / seconds:.1f} запр./с.")
        return "\n".join(rows)


async def timed(stats: Stats, key: str, coro):
    t = time.perf_counter()
    try:
        r = await coro
        stats.add(key, time.perf_counter() - t, r.status_code < 400)
        return r
    except httpx.HTTPError:
        stats.add(key, time.perf_counter() - t, False)
        return None


async def login(client: httpx.AsyncClient, stats: Stats, user: str) -> dict[str, str] | None:
    r = await timed(
        stats,
        "POST /api/auth/login",
        client.post("/api/auth/login", json={"login": user, "password": PASSWORD}),
    )
    if r is None or r.status_code != 200:
        return None
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def browse_user(client: httpx.AsyncClient, stats: Stats, user: str, deadline: float) -> None:
    headers = await login(client, stats, user)
    if headers is None:
        return
    r = await timed(
        stats, "GET /api/me/assignments", client.get("/api/me/assignments", headers=headers)
    )
    sessions = r.json() if r is not None and r.status_code == 200 else []
    session_id = sessions[0]["id"] if sessions else None
    attempt_id = None
    while time.perf_counter() < deadline:
        await timed(stats, "GET /api/me", client.get("/api/me", headers=headers))
        if session_id:
            r = await timed(
                stats,
                "GET /api/sessions/{id}/journal",
                client.get(f"/api/sessions/{session_id}/journal", headers=headers),
            )
            if r is not None and r.status_code == 200 and r.json()["items"]:
                attempt_id = r.json()["items"][0]["attempt_id"]
        if attempt_id:
            await timed(
                stats,
                "GET /api/attempts/{id}",
                client.get(f"/api/attempts/{attempt_id}", headers=headers),
            )
        await asyncio.sleep(random.uniform(0.7, 1.3))  # noqa: S311 - think time


async def cards_user(
    client: httpx.AsyncClient, stats: Stats, user: str, deadline: float, cards: list[int]
) -> None:
    headers = await login(client, stats, user)
    if headers is None:
        return
    r = await timed(
        stats, "GET /api/me/assignments", client.get("/api/me/assignments", headers=headers)
    )
    running = [
        s
        for s in (r.json() if r is not None and r.status_code == 200 else [])
        if s["status"] == "running"
    ]
    if not running:
        print(f"{user}: нет идущего занятия", file=sys.stderr)
        return
    session_id = running[0]["id"]
    while time.perf_counter() < deadline:
        r = await timed(
            stats,
            "GET /api/sessions/{id}/journal",
            client.get(f"/api/sessions/{session_id}/journal", headers=headers),
        )
        if r is None or r.status_code != 200:
            await asyncio.sleep(1)
            continue
        active = [
            i for i in r.json()["items"] if i["state"] in ("issued", "received", "in_progress")
        ]
        if not active:
            await asyncio.sleep(1)
            continue
        attempt_id = active[0]["attempt_id"]
        await timed(
            stats,
            "POST /api/attempts/{id}/open",
            client.post(f"/api/attempts/{attempt_id}/open", headers=headers),
        )
        for status, extra in CHAIN:
            if time.perf_counter() >= deadline:
                return
            await timed(
                stats,
                f"POST /api/attempts/{{id}}/status {status}",
                client.post(
                    f"/api/attempts/{attempt_id}/status",
                    headers=headers,
                    json={"status": status, **extra},
                ),
            )
            await asyncio.sleep(random.uniform(0.3, 0.8))  # noqa: S311 - think time
        cards[0] += 1


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scenario", choices=["browse", "cards"])
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument(
        "--prefix", default="load", help="префикс логинов мок-обучающихся (load01…)"
    )
    parser.add_argument(
        "--seed-students", type=int, default=6, help="сколько seed-логинов studentN добавить"
    )
    args = parser.parse_args()

    logins = [f"student{i}" for i in range(1, args.seed_students + 1)] + [
        f"{args.prefix}{i:02d}" for i in range(1, 101)
    ]
    if args.scenario == "cards":
        logins = [f"{args.prefix}{i:02d}" for i in range(1, 101)]
    users = logins[: args.users]
    if len(users) < args.users:
        print(
            f"нужно {args.users} логинов, есть {len(users)} — увеличьте --seed-students",
            file=sys.stderr,
        )

    stats = Stats()
    cards = [0]
    limits = httpx.Limits(
        max_connections=args.users + 10, max_keepalive_connections=args.users + 10
    )
    async with httpx.AsyncClient(base_url=BASE, verify=False, timeout=30, limits=limits) as client:  # noqa: S501
        stats.started = time.perf_counter()
        deadline = stats.started + args.seconds

        # Ramp up over the first 10 % of the run: a class logging in, not a synthetic spike.
        async def run(user: str, delay: float):
            await asyncio.sleep(delay)
            if args.scenario == "browse":
                await browse_user(client, stats, user, deadline)
            else:
                await cards_user(client, stats, user, deadline, cards)

        ramp = max(args.seconds * 0.1, 1)
        await asyncio.gather(*[run(u, i * ramp / max(len(users), 1)) for i, u in enumerate(users)])
        stats.finished = time.perf_counter()

    print(f"### {args.scenario}: {len(users)} пользователей, {args.seconds} с, {BASE}\n")
    print(stats.table())
    if args.scenario == "cards":
        print(f"Карточек отработано целиком (5 статусов): {cards[0]}.")
    worst = max(
        (sorted(v)[int(len(v) * 0.95) - 1] if len(v) >= 20 else v[-1]) for v in stats.times.values()
    )
    verdict = "укладывается" if worst < 2000 else "НЕ укладывается"
    print(f"\nХудший p95: {worst:.0f} мс — {verdict} в 2 с ТЗ.")
    return 0 if sum(stats.errors.values()) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
