"""Проверка снаружи: живы ли портал закупщика, мессенджеры в amoCRM и РОП-бот.

Все три живут на одном сервере. 24.09.2026 хостинг выключил его на семь часов, и
никто не узнал: «Пульт» портала живёт там же и о своей смерти сказать не может.
Эта проверка бежит на GitHub раз в 5 минут и пишет Семёну в Telegram, когда
что-то перестало отвечать, раз в 12 часов, пока не ответит, и когда ответило.

Состояние между запусками — `state/state.json` в кэше GitHub Actions.
Переменные: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID; TEST_MESSAGE=1 — только
проверить связь с Telegram; DRY_RUN=1 — проверить сервисы без сообщений.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SERVICES = [
    ("Портал закупщика", "https://portal.ruspack-news.ru/health"),
    ("Мессенджеры в amoCRM", "https://msgr.ruspack-news.ru/health"),
    ("РОП-бот", "https://bot.ruspack-news.ru/health"),
]
STATE = Path("state/state.json")
# три попытки за минуту: одна неудачная — ещё не повод будить человека
ATTEMPTS, TIMEOUT, PAUSE = 3, 20, 15
REMIND = timedelta(hours=12)
MSK = timezone(timedelta(hours=3))
ALL_DOWN_HINT = ("Молчат все три — скорее всего, выключен сам сервер. Сначала панель "
                 "Fornex: статус VPS и баланс.")


def probe(url: str) -> str | None:
    """None — отвечает. Иначе — что не так, словами.

    Мало открытого порта: выключенный сервер хостинг показывал «живым» по TCP.
    Живой — это ответ 200 с {"status": "ok"} или {"ok": true}.
    """
    problem = None
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ruspack-uptime"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as r:
                data = json.loads(r.read(4000) or b"{}")
            if data.get("status") == "ok" or data.get("ok") is True:
                return None
            problem = "отвечает, но не «ok»"
        except urllib.error.HTTPError as e:
            problem = f"ошибка {e.code}"
        except ValueError:
            problem = "отвечает не тем"
        except Exception:
            problem = "нет ответа"
        if attempt < ATTEMPTS - 1:
            time.sleep(PAUSE)
    return problem


def send(text: str) -> None:
    body = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
    with urllib.request.urlopen(url, data=body, timeout=20) as r:
        if not json.loads(r.read()).get("ok"):
            raise RuntimeError("Telegram не принял сообщение")


def msk(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(MSK).strftime("%d.%m %H:%M")


def duration(since: str, now: datetime) -> str:
    minutes = int((now - datetime.fromisoformat(since)).total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def main() -> int:
    if os.environ.get("TEST_MESSAGE"):
        send("Проверка связи: если сервер портала перестанет отвечать, я напишу "
             "сюда. Проверяю снаружи, с GitHub, раз в 5 минут.")
        return 0
    if os.environ.get("DRY_RUN"):
        for name, url in SERVICES:
            print(f"{name}: {probe(url) or 'отвечает'}")
        return 0
    state = json.loads(STATE.read_text()) if STATE.exists() else {"down": {}}
    down, now, lines = state["down"], datetime.now(UTC), []
    for name, url in SERVICES:
        problem, was = probe(url), down.get(name)
        if problem and not was:
            down[name] = {"since": now.isoformat(), "reminded": now.isoformat()}
            lines.append(f"🔴 Не отвечает: {name} — {problem}.\n{url}")
        elif problem and now - datetime.fromisoformat(was["reminded"]) >= REMIND:
            was["reminded"] = now.isoformat()
            lines.append(f"🔴 Всё ещё не отвечает: {name} — с {msk(was['since'])} МСК.")
        elif not problem and was:
            del down[name]
            lines.append(f"✅ Снова отвечает: {name}. Не отвечал {duration(was['since'], now)}.")
    if not lines:
        return 0
    if len(down) == len(SERVICES) and any(x.startswith("🔴 Не отвечает") for x in lines):
        lines.append(ALL_DOWN_HINT)
    # сначала сообщение, потом состояние: не ушло — следующий запуск повторит
    send("\n\n".join(lines))
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as out:
        out.write("changed=true\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
