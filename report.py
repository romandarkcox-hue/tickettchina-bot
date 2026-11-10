"""Еженедельный отчёт о статистике Travelpayouts — только в личный чат админа.

Переменные окружения:
    TRAVELPAYOUTS_TOKEN — токен API Travelpayouts
    TELEGRAM_BOT_TOKEN  — токен бота
    ADMIN_CHAT_ID       — личный чат, куда прислать отчёт

Запуск:
    python report.py            — отправить отчёт за последние 7 дней
    python report.py --dry-run  — только показать отчёт
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

from bot import MSK, format_price, load_config, send_telegram

STATS_URL = "https://api.travelpayouts.com/statistics/v1/execute_query"
BALANCE_URL = "https://api.travelpayouts.com/finance/v2/get_user_balance"

COUNTS = ["clicks_count", "searches_count", "actions_count", "paid_actions_count",
          "processing_actions_count", "cancelled_actions_count"]
PROFITS = ["paid_profit_rub", "processing_profit_rub"]
UAE = {"DXB", "AUH", "SHJ", "RKT"}  # города ОАЭ — для разбивки по каналам
THAI = {"BKK", "HKT", "UTP", "KBV", "USM", "CNX"}  # города Таиланда


def tp_request(url, token, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"X-Access-Token": token, "Content-Type": "application/json",
                 "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query(token, fields, date_from, date_to, group_by=None):
    """Счётчики и суммы в API нельзя смешивать в одном запросе — поэтому запросы раздельные."""
    body = {
        "fields": fields,
        "filters": [
            {"field": "date", "op": "ge", "value": date_from},
            {"field": "date", "op": "le", "value": date_to},
        ],
        "limit": 200,
    }
    if group_by:
        body["group_by"] = group_by
    result = tp_request(STATS_URL, token, body)
    if "results" not in result:
        raise RuntimeError(f"Travelpayouts: {result}")
    return result["results"]


def totals(token, date_from, date_to):
    # За период без данных API возвращает пустой список — тогда остаются нули
    row = {f: 0 for f in COUNTS + PROFITS}
    for fields in (COUNTS, PROFITS):
        result = query(token, fields, date_from, date_to)
        if result:
            row.update({k: v for k, v in result[0].items() if v is not None})
    return row


def route_name(sub_id, cities):
    origin, _, destination = sub_id.removeprefix("site_").partition("-")
    if origin in cities and destination in cities:
        return f"{cities[origin]} – {cities[destination]}"
    return sub_id or "без метки"


def source_of(sub_id, turkey_cities):
    if sub_id.startswith("site_"):
        return "сайт"
    if any(code in UAE for code in sub_id.split("-")):
        return "ОАЭ"
    if any(code in THAI for code in sub_id.split("-")):
        return "Таиланд"
    if sub_id == "TURK" or any(code in turkey_cities and code not in ("MOW", "LED", "KZN", "SVX", "OVB")
                               for code in sub_id.split("-")):
        return "Турция"
    return "Китай"


def events(token, date_from, date_to):
    """Сырые события (переходы и поиски) с меткой направления.
    Группировка в API не работает, поэтому считаем сами."""
    body = {"fields": ["date", "sub_id", "type"], "limit": 1000, "filters": [
        {"field": "date", "op": "ge", "value": date_from},
        {"field": "date", "op": "le", "value": date_to}]}
    return tp_request(STATS_URL, token, body).get("results", [])


def breakdown(token, date_from, date_to, cities, turkey_cities, limit=7):
    try:
        rows = events(token, date_from, date_to)
    except Exception as e:
        print(f"Разбивка по направлениям недоступна: {e}", file=sys.stderr)
        return {}, []
    by_source, by_route = {}, {}
    for r in rows:
        sub = r.get("sub_id") or ""
        if sub == "TEST":
            continue
        src = source_of(sub, turkey_cities)
        by_source[src] = by_source.get(src, 0) + 1
        name = route_name(sub, cities) + (" (сайт)" if sub.startswith("site_") else "")
        by_route[name] = by_route.get(name, 0) + 1
    top = sorted(by_route.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return by_source, top


def rub(value):
    return format_price(round(float(value)), "rub")


def build_report(token, cfg, cfg_turkey):
    today = datetime.now(MSK).date()
    date_from = (today - timedelta(days=7)).isoformat()
    date_to = (today - timedelta(days=1)).isoformat()
    week = totals(token, date_from, date_to)
    month_start = today.replace(day=1).isoformat()
    month = totals(token, month_start, today.isoformat())
    balance = tp_request(BALANCE_URL, token).get("balance", {}).get("rub", "0")
    cities = {**cfg_turkey["cities"], **cfg["cities"], "DXB": "Дубай", "AUH": "Абу-Даби", "SHJ": "Шарджа",
              "BKK": "Бангкок", "HKT": "Пхукет", "UTP": "Паттайя", "KBV": "Краби", "USM": "Самуи",
              "CNX": "Чиангмай", "VVO": "Владивосток", "IKT": "Иркутск", "KHV": "Хабаровск", "BAX": "Барнаул"}
    by_source, routes = breakdown(token, date_from, date_to, cities, cfg_turkey["cities"])

    period = f"{datetime.fromisoformat(date_from):%d.%m} – {datetime.fromisoformat(date_to):%d.%m}"
    lines = [
        f"📊 <b>Статистика за неделю</b> ({period})",
        "",
        f"👆 Переходы по ссылкам: <b>{week['clicks_count']}</b>",
        f"🔎 Поиски билетов: {week['searches_count']}",
        f"🎫 Покупки: <b>{week['actions_count']}</b>"
        f" (оплачено {week['paid_actions_count']}, в обработке {week['processing_actions_count']},"
        f" отменено {week['cancelled_actions_count']})",
        f"💰 Заработано: <b>{rub(week['paid_profit_rub'])}</b>"
        f" + в обработке {rub(week['processing_profit_rub'])}",
    ]
    if by_source:
        lines += ["", "📍 Откуда: " + ", ".join(f"{k} — {v}" for k, v in
                                               sorted(by_source.items(), key=lambda kv: -kv[1]))]
    if routes:
        lines += ["", "🏆 <b>Направления:</b>"]
        lines += [f"{i}. {name} — {n}" for i, (name, n) in enumerate(routes, 1)]
    lines += [
        "",
        f"📅 С начала месяца: {month['clicks_count']} перех., {month['searches_count']} поисков,"
        f" {month['actions_count']} покуп., {rub(month['paid_profit_rub'])}"
        f" + {rub(month['processing_profit_rub'])} в обработке",
        f"🏦 Баланс к выплате: <b>{rub(balance)}</b>",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="не отправлять в Telegram")
    args = parser.parse_args()

    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    admin_chat = os.environ.get("ADMIN_CHAT_ID")
    if not tp_token:
        sys.exit("Не задан TRAVELPAYOUTS_TOKEN")
    if not args.dry_run and not (bot_token and admin_chat):
        sys.exit("Не заданы TELEGRAM_BOT_TOKEN и ADMIN_CHAT_ID")

    text = build_report(tp_token, load_config("config.toml"), load_config("config_turkey.toml"))
    print(text)
    if not args.dry_run:
        send_telegram(text, admin_chat, bot_token)  # только в личку, не в канал


if __name__ == "__main__":
    main()
