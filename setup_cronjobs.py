"""Будильники на cron-job.org: в нужное время просят GitHub запустить воркфлоу.

Расписание GitHub опаздывает на часы, а cron-job.org срабатывает точно.
Каждый запуск публикует один пост из очереди на день; лишний запуск вне часов
публикаций бот просто пропускает.

Запуск:
    python setup_cronjobs.py          — создать или обновить задания, удалить старые
    python setup_cronjobs.py --list   — показать задания и последний ответ

Нужны в .env (или в переменных окружения):
    CRONJOB_API_KEY   — cron-job.org → Settings → API
    GH_DISPATCH_TOKEN — fine-grained токен GitHub: только репозиторий
                        tickettchina-bot, Actions: Read and write
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.cron-job.org"
REPO = "romandarkcox-hue/tickettchina-bot"
POST = 1  # код метода POST в API cron-job.org
PREFIX = ("tickettchina", "dobroeytro")  # задания с таким началом названия — наши; лишние удаляются

JOBS = [
    # Время по Москве. Китай — в :15 и :45, Турция — в :00 и :30, ОАЭ — в :05 и :35,
    # Таиланд — в :20 и :50, чтобы не совпадали.
    {"title": "tickettchina: Китай каждые 30 мин", "workflow": "post.yml",
     "hours": list(range(9, 16)), "minutes": [15, 45]},
    {"title": "tickettchina: Турция каждые 30 мин", "workflow": "post_turkey.yml",
     "hours": list(range(9, 17)), "minutes": [0, 30]},
    {"title": "tickettchina: ОАЭ каждые 30 мин", "workflow": "post_uae.yml",
     "hours": list(range(9, 14)), "minutes": [5, 35]},
    {"title": "tickettchina: Таиланд каждые 30 мин", "workflow": "post_thai.yml",
     "hours": list(range(9, 14)), "minutes": [20, 50]},
    # Канал @dobroeytroru: у каждого поста своё время и свой режим
    {"title": "dobroeytro: Доброе утро 06:30", "workflow": "utro.yml",
     "hours": [6], "minutes": [30], "inputs": {"mode": "morning"}},
    {"title": "dobroeytro: Какой сегодня день 08:00", "workflow": "utro.yml",
     "hours": [8], "minutes": [0], "inputs": {"mode": "day"}},
    {"title": "dobroeytro: Этот день в истории 09:00", "workflow": "utro.yml",
     "hours": [9], "minutes": [0], "inputs": {"mode": "history"}},
    {"title": "dobroeytro: Доброй ночи 21:30", "workflow": "utro.yml",
     "hours": [21], "minutes": [30], "inputs": {"mode": "night"}},
]


def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"')


def api(key, method, path, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"cron-job.org ответил {e.code} на {method} {path}: {e.read().decode()[:300]}")
    return json.loads(text) if text else {}


def job_body(job, gh_token):
    return {
        "url": f"https://api.github.com/repos/{REPO}/actions/workflows/{job['workflow']}/dispatches",
        "title": job["title"],
        "enabled": True,
        "saveResponses": True,
        "requestMethod": POST,
        "schedule": {
            "timezone": "Europe/Moscow",
            "expiresAt": 0,
            "hours": job["hours"],
            "minutes": job["minutes"],
            "mdays": [-1],
            "months": [-1],
            "wdays": [-1],
        },
        "extendedData": {
            "headers": {
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            "body": json.dumps({"ref": "main", **({"inputs": job["inputs"]} if "inputs" in job else {})}),
        },
        # Письмо на почту аккаунта, если GitHub перестал принимать запуск (например, истёк токен)
        "notification": {"onFailure": True, "onSuccess": False, "onDisable": True},
    }


def main():
    load_env()
    key = os.environ.get("CRONJOB_API_KEY")
    gh_token = os.environ.get("GH_DISPATCH_TOKEN")
    if not key:
        sys.exit("Нет CRONJOB_API_KEY в .env")

    jobs = api(key, "GET", "/jobs").get("jobs", [])

    if "--list" in sys.argv:
        msk = timezone(timedelta(hours=3))
        for j in jobs:
            if not j["title"].startswith(PREFIX):
                continue
            nxt = j.get("nextExecution")
            nxt = datetime.fromtimestamp(nxt, msk).strftime("%d.%m %H:%M") if nxt else "—"
            print(f"{j['title']}: {'включено' if j['enabled'] else 'выключено'}, "
                  f"следующий запуск {nxt} МСК, последний ответ {j.get('lastStatus')}")
        return

    if not gh_token:
        sys.exit("Нет GH_DISPATCH_TOKEN в .env")
    for job in JOBS:
        existing = next((j for j in jobs if j["title"] == job["title"]), None)
        if existing:
            api(key, "PATCH", f"/jobs/{existing['jobId']}", {"job": job_body(job, gh_token)})
            print(f"Обновлено: {job['title']}")
        else:
            job_id = api(key, "PUT", "/jobs", {"job": job_body(job, gh_token)})["jobId"]
            print(f"Создано: {job['title']} (№ {job_id})")
            time.sleep(1.5)  # не больше одного создания в секунду
    # Старые задания (например, прежние 9:05 и 10:35) иначе публиковали бы лишние посты
    titles = {job["title"] for job in JOBS}
    for j in jobs:
        if j["title"].startswith(PREFIX) and j["title"] not in titles:
            api(key, "DELETE", f"/jobs/{j['jobId']}")
            print(f"Удалено старое: {j['title']}")


if __name__ == "__main__":
    main()
