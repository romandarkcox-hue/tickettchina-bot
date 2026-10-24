"""Будильники на cron-job.org: в нужное время просят GitHub запустить воркфлоу.

Расписание GitHub иногда пропускает запуски, а cron-job.org срабатывает точно.
Дубли не страшны: бот сам пропускает запуск, если сегодня уже публиковал.

Запуск:
    python setup_cronjobs.py          — создать или обновить задания
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
WORKFLOW = "post.yml"
POST = 1  # код метода POST в API cron-job.org

JOBS = [
    # Время по Москве. 09:05 — страховка основного запуска, 10:35 — запасного.
    {"title": "tickettchina: билеты 9:05", "hours": [9], "minutes": [5]},
    {"title": "tickettchina: билеты 10:35", "hours": [10], "minutes": [35]},
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
        "url": f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
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
            "body": json.dumps({"ref": "main"}),
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
            if not j["title"].startswith("tickettchina"):
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


if __name__ == "__main__":
    main()
