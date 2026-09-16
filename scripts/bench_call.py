"""Latency of a live call (wave 6): from the end of the operator's question in the line to the
first sound of the caller's reply, through the whole chain Asterisk → snoop → ExternalMedia →
VAD → STT → dialog model → Piper → playback.

The script is the trainee's phone: through ARI it claims the login (device state
``Stasis:bench-<login>`` = INUSE, see deploy/asterisk/extensions.conf), issues a call-intake
attempt like ``scripts/issue_call.py``, answers the call in the Stasis app «bench», bridges it
with an ExternalMedia channel that streams to this machine, plays the questions (voiced by
Piper with a different voice) as RTP and listens for the reply.

    uv run --project backend python scripts/bench_call.py \\
        --questions "Что случилось?" "Диктуйте адрес" "Кто-нибудь пострадал?" \\
        --rounds 3 --out docs/PERFORMANCE.md --note "ноутбук, 1.5B + whisper base"

Requires the stand with profiles ai and telephony (docker compose --profile ai
--profile telephony up -d), ARI published on ARI_PORT, and the questions' voice in MODELS_DIR.
``--from-raw`` re-renders the table from docs/screenshots/wave-06/bench_call.json without
calling anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import issue_call  # noqa: E402
import numpy as np  # noqa: E402


def alaw_encode(pcm: bytes) -> bytes:
    """16-bit PCM → G.711 A-law (ITU-T G.711, table-free arithmetic)."""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.int32)
    sign = np.where(samples >= 0, 0x80, 0)
    magnitude = np.minimum(np.abs(samples) >> 3, 0x0FFF)
    exponent = np.zeros_like(magnitude)
    for shift in range(1, 8):
        exponent = np.where(magnitude >= (1 << (shift + 4)), shift, exponent)
    mantissa = np.where(exponent == 0, (magnitude >> 1) & 0x0F, (magnitude >> exponent) & 0x0F)
    value = (sign | (exponent << 4) | mantissa) ^ 0x55
    return value.astype(np.uint8).tobytes()


def alaw_decode(data: bytes) -> np.ndarray:
    """G.711 A-law → float32 samples in 16-bit range."""
    values = np.frombuffer(data, dtype=np.uint8).astype(np.int32) ^ 0x55
    sign = values & 0x80
    exponent = (values >> 4) & 0x07
    mantissa = values & 0x0F
    magnitude = np.where(
        exponent == 0, (mantissa << 4) + 8, ((mantissa << 4) + 0x108) << (exponent - 1)
    )
    return np.where(sign, magnitude, -magnitude).astype(np.float32)

DEFAULT_QUESTIONS = [
    "Что случилось?",
    "Диктуйте адрес",
    "На каком этаже?",
    "Кто-нибудь пострадал?",
    "Как вас зовут?",
]
RAW_PATH = ROOT / "docs" / "screenshots" / "wave-06" / "bench_call.json"
SECTION_TITLE = "## Телефония: задержка ответа в линии (волна 6)"
# The phone side speaks G.711 A-law: Asterisk accepts inbound RTP on an ExternalMedia channel
# only with a static payload type (docs/DECISIONS.md), and A-law (PT 8) is trivial to encode.
SAMPLE_RATE = 8000
FRAME_SAMPLES = 160  # 20 ms
FRAME_BYTES = FRAME_SAMPLES  # one byte per sample
PAYLOAD_TYPE_ALAW = 8
ENERGY_THRESHOLD = 0.015  # RMS of a frame that counts as sound
SILENCE_AFTER_SPEECH_SECONDS = 1.2
REPLY_TIMEOUT_SECONDS = 25.0
QUESTION_VOICE = "ru_male_2"  # dmitri: not the caller's voice, so the two are easy to tell apart


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--questions", nargs="+", default=DEFAULT_QUESTIONS)
    parser.add_argument("--rounds", type=int, default=1, help="сколько звонков подряд")
    parser.add_argument("--student", default="student12", help="логин, чей телефон играем")
    parser.add_argument("--scenario", default="call_2-1_zadymlenie_musoroprovoda")
    parser.add_argument("--ari", default=None, help="ARI с хоста, по умолчанию из .env (ARI_PORT)")
    parser.add_argument("--ari-password", default=None)
    parser.add_argument(
        "--media-host",
        default="host.docker.internal",
        help="эта машина, как её видит Asterisk (Linux: адрес хоста в сети Docker)",
    )
    parser.add_argument("--media-port", type=int, default=14000)
    parser.add_argument("--rtp-host", default="127.0.0.1", help="куда слать RTP (порты Asterisk)")
    parser.add_argument("--models-dir", default=None)
    parser.add_argument("--out", default=str(ROOT / "docs" / "PERFORMANCE.md"))
    parser.add_argument("--note", default="", help="строка про машину и модели для таблицы")
    parser.add_argument("--from-raw", action="store_true", help="только перерисовать таблицу")
    parser.add_argument("--debug-record", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


# ---------------------------------------------------------------- RTP phone


@dataclass
class Phone(asyncio.DatagramProtocol):
    """UDP end of the ExternalMedia channel: receives the line, sends the questions."""

    transport: asyncio.DatagramTransport | None = None
    remote: tuple[str, int] | None = None
    payload_type: int = PAYLOAD_TYPE_ALAW
    ssrc: int = 0x5B3A1C2D
    seq: int = 1
    timestamp: int = 0
    last_sound_at: float | None = None
    first_sound_after: float | None = None
    watch_from: float | None = None
    received: int = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()

    def datagram_received(self, packet: bytes, addr: tuple[str, int]) -> None:
        if len(packet) < 12 or packet[0] >> 6 != 2:
            return
        self.received += 1
        payload = packet[12 + 4 * (packet[0] & 0x0F) :]
        samples = alaw_decode(payload)
        rms = float(np.sqrt(np.mean((samples / 32768.0) ** 2))) if len(samples) else 0.0
        now = time.monotonic()
        if rms > ENERGY_THRESHOLD:
            self.last_sound_at = now
            if self.watch_from is not None and self.first_sound_after is None and now > self.watch_from:
                self.first_sound_after = now

    async def wait_for_silence(self, timeout: float) -> bool:
        """True once something was heard and then nothing for SILENCE_AFTER_SPEECH_SECONDS."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (
                self.last_sound_at is not None
                and time.monotonic() - self.last_sound_at > SILENCE_AFTER_SPEECH_SECONDS
            ):
                return True
            await asyncio.sleep(0.05)
        return False

    async def send_pcm(self, pcm: bytes) -> float:
        """Sends 8 kHz PCM as 20 ms A-law RTP frames in real time; returns the end time."""
        assert self.remote is not None and self.transport is not None
        encoded = alaw_encode(pcm)
        silence = alaw_encode(bytes(2))
        started = time.monotonic()
        frames = [encoded[i : i + FRAME_BYTES] for i in range(0, len(encoded), FRAME_BYTES)]
        for index, frame in enumerate(frames):
            if len(frame) < FRAME_BYTES:
                frame = frame + silence * (FRAME_BYTES - len(frame))
            header = struct.pack(
                "!BBHII", 0x80, self.payload_type, self.seq & 0xFFFF, self.timestamp, self.ssrc
            )
            self.transport.sendto(header + frame, self.remote)
            self.seq += 1
            self.timestamp = (self.timestamp + FRAME_SAMPLES) & 0xFFFFFFFF
            due = started + (index + 1) * 0.02
            delay = due - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        return time.monotonic()


# ---------------------------------------------------------------- one call


async def one_call(args: argparse.Namespace, ari, questions_pcm: list[bytes]) -> list[dict]:
    """Issues an attempt, plays the questions, measures every reply."""
    from websockets.asyncio.client import connect

    from app.telephony.sip import endpoint_login

    login = endpoint_login(args.student)
    device = f"Stasis:bench-{login}"
    await ari.set_device_state(device, "INUSE")
    loop = asyncio.get_running_loop()
    _, phone = await loop.create_datagram_endpoint(Phone, local_addr=("0.0.0.0", args.media_port))
    results: list[dict] = []
    bridge_id = f"bench-{int(time.time())}"
    channel_id: str | None = None
    media_id: str | None = None
    events_url = ari.events_url()
    try:
        async with connect(events_url, max_size=4 * 1024 * 1024) as ws:
            issue_args = argparse.Namespace(
                student=args.student, scenario=args.scenario, dialog_mode="select", norm=90,
                close_open=True,
            )
            attempt_id = await issue_call.issue(issue_args)
            print(f"  попытка {attempt_id}: ждём звонок…")
            # Wait for our Local channel to reach the bench application.
            async with asyncio.timeout(30):
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "StasisStart" and event.get("args", [None])[0] == login:
                        channel_id = event["channel"]["id"]
                        break
            await ari.answer(channel_id)
            await ari.create_bridge(bridge_id)
            await ari.add_channel(bridge_id, channel_id)
            media_id = f"bench-media-{int(time.time())}"
            created = await ari.external_media(
                media_id, args.media_host, args.media_port, app_args="media", fmt="alaw"
            )
            rtp_port = int(created["channelvars"]["UNICASTRTP_LOCAL_PORT"])
            phone.remote = (args.rtp_host, rtp_port)
            # StasisStart of the media channel, then bridge it.
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "StasisStart" and event["channel"]["id"] == media_id:
                        break
            await ari.add_channel(bridge_id, media_id)
            if args.debug_record:
                await ari.record_bridge(bridge_id, "recordings/bench-bridge")
            # A little silence so Asterisk learns our RTP source before the questions.
            await phone.send_pcm(bytes(2 * FRAME_SAMPLES * 25))
            print("  слушаем открывающую реплику…")
            if not await phone.wait_for_silence(REPLY_TIMEOUT_SECONDS):
                print("  открывающая реплика не прозвучала: телефония не отдаёт звук")
                return results
            for question, pcm in zip(args.questions, questions_pcm, strict=True):
                phone.first_sound_after = None
                sent_end = await phone.send_pcm(pcm)
                phone.watch_from = sent_end + 0.1
                phone.last_sound_at = None
                deadline = time.monotonic() + REPLY_TIMEOUT_SECONDS
                while phone.first_sound_after is None and time.monotonic() < deadline:
                    await asyncio.sleep(0.02)
                if phone.first_sound_after is None:
                    print(f"  «{question}»: ответа нет за {REPLY_TIMEOUT_SECONDS:.0f} с")
                    results.append({"question": question, "latency_s": None})
                    continue
                latency = phone.first_sound_after - sent_end
                print(f"  «{question}»: ответ через {latency:.2f} с")
                results.append({"question": question, "latency_s": round(latency, 3)})
                await phone.wait_for_silence(REPLY_TIMEOUT_SECONDS)
            print(f"  RTP: принято {phone.received} пакетов A-law")
    finally:
        phone.stop()
        for cid in (media_id, channel_id):
            if cid:
                try:
                    await ari.hangup(cid)
                except Exception:
                    pass
        try:
            await ari.destroy_bridge(bridge_id)
        except Exception:
            pass
        await ari.set_device_state(device, "NOT_INUSE")
    return results


# ---------------------------------------------------------------- report


def summarize(raw: dict) -> dict:
    values = [r["latency_s"] for c in raw["calls"] for r in c if r["latency_s"] is not None]
    missed = sum(1 for c in raw["calls"] for r in c if r["latency_s"] is None)
    if not values:
        return {"n": 0, "missed": missed}
    values.sort()
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return {
        "n": len(values),
        "missed": missed,
        "median": statistics.median(values),
        "p90": p90,
        "min": values[0],
        "max": values[-1],
    }


def render_section(raw: dict) -> str:
    s = summarize(raw)
    lines = [
        SECTION_TITLE,
        "",
        f"Замер {raw['at'][:16].replace('T', ' ')}: `scripts/bench_call.py`, сценарий "
        f"`{raw['scenario']}`, {raw['rounds']} звонок(ов) × {len(raw['questions'])} вопросов, "
        "вопросы озвучены Piper и поданы в линию как RTP; время от конца вопроса в линии до "
        "первого звука ответа заявителя (снуп → ExternalMedia → VAD → STT → модель → Piper → "
        "воспроизведение).",
    ]
    if raw.get("note"):
        lines.append(f"Машина и модели: {raw['note']}.")
    lines.append("")
    if s["n"] == 0:
        lines.append("Ответов не получено." + (f" Без ответа: {s['missed']}." if s["missed"] else ""))
    else:
        lines += [
            "| Ответов | Медиана, с | p90, с | Мин, с | Макс, с | Без ответа |",
            "|---|---|---|---|---|---|",
            f"| {s['n']} | {s['median']:.2f} | {s['p90']:.2f} | {s['min']:.2f} | {s['max']:.2f} "
            f"| {s['missed']} |",
            "",
            "По звонкам (первый звонок холодный: реплики озвучиваются Piper на лету и модель "
            "заполняет кеш промпта; с волны 8 реплики озвучены при утверждении):",
            "",
            "| Звонок | " + " | ".join(raw["questions"]) + " |",
            "|---|" + "---|" * len(raw["questions"]),
        ]
        for index, call in enumerate(raw["calls"], start=1):
            cells = ["—" if r["latency_s"] is None else f"{r['latency_s']:.2f}" for r in call]
            lines.append(f"| {index} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Сырые данные: `docs/screenshots/wave-06/bench_call.json`. Воспроизвести: стенд с "
        "профилями `ai` и `telephony`, затем `uv run --project backend python "
        "scripts/bench_call.py --rounds 3 --note \"…\"`. Таблица без повторного прогона: "
        "`--from-raw`.",
        "",
    ]
    return "\n".join(lines)


def write_section(out: Path, section: str) -> None:
    text = out.read_text("utf-8") if out.exists() else "# Замеры\n\n"
    start = text.find(SECTION_TITLE)
    if start >= 0:
        rest = text[start + len(SECTION_TITLE) :]
        next_section = rest.find("\n## ")
        end = len(text) if next_section < 0 else start + len(SECTION_TITLE) + next_section + 1
        text = text[:start] + section + text[end:]
    else:
        text = text.rstrip("\n") + "\n\n" + section
    out.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- main


async def run(args: argparse.Namespace) -> None:
    env = issue_call.read_env()
    issue_call.configure_app(env)
    from app.providers.tts import PiperTTS
    from app.telephony.ari import AriClient
    from app.telephony.media import resample_pcm

    models_dir = Path(args.models_dir or env.get("MODELS_DIR") or ROOT / "models")
    ari_url = args.ari or f"http://localhost:{env.get('ARI_PORT', '8088')}/ari"
    ari = AriClient(ari_url, "trainer", args.ari_password or env.get("ARI_PASSWORD", "trainer"), "bench")
    if not await ari.ping():
        raise SystemExit(f"ARI не отвечает на {ari_url}: поднят ли профиль telephony?")
    tts = PiperTTS(models_dir / "tts")
    print("озвучиваем вопросы…")
    questions_pcm = []
    for question in args.questions:
        clip = await tts.synthesize(question, QUESTION_VOICE)
        assert clip is not None
        questions_pcm.append(resample_pcm(clip.pcm, clip.sample_rate, SAMPLE_RATE))
    calls = []
    for round_no in range(1, args.rounds + 1):
        print(f"звонок {round_no}/{args.rounds}")
        calls.append(await one_call(args, ari, questions_pcm))
        await asyncio.sleep(1)
    await ari.aclose()
    raw = {
        "at": datetime.now(UTC).isoformat(),
        "scenario": args.scenario,
        "student": args.student,
        "questions": args.questions,
        "rounds": args.rounds,
        "note": args.note,
        "calls": calls,
    }
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    write_section(Path(args.out), render_section(raw))
    s = summarize(raw)
    if s["n"]:
        print(f"итого: {s['n']} ответов, медиана {s['median']:.2f} с, p90 {s['p90']:.2f} с")
    print(f"таблица в {args.out}, сырые данные в {RAW_PATH}")


def main() -> None:
    args = parse_args()
    if args.from_raw:
        raw = json.loads(RAW_PATH.read_text("utf-8"))
        if args.note:
            raw["note"] = args.note
        write_section(Path(args.out), render_section(raw))
        print(f"таблица обновлена в {args.out}")
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
