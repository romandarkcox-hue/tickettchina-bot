"""Автопостинг дешёвых билетов в Китай в Telegram-канал.

Цены берутся из Travelpayouts Data API (кэш поисков Aviasales).
Переменные окружения:
    TRAVELPAYOUTS_TOKEN — токен API Travelpayouts
    TELEGRAM_BOT_TOKEN  — токен бота от @BotFather

Запуск:
    python bot.py            — найти билеты и опубликовать
    python bot.py --dry-run  — только показать посты, ничего не отправлять
    python bot.py --chat ID  — отправить посты в указанный чат вместо канала
"""

import argparse
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "state.json"

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

AIRLINES = {
    "SU": "Аэрофлот", "S7": "S7 Airlines", "U6": "Уральские авиалинии",
    "FV": "Россия", "DP": "Победа", "N4": "Nordwind", "ZF": "Azur Air",
    "CA": "Air China", "MU": "China Eastern", "CZ": "China Southern",
    "HU": "Hainan Airlines", "3U": "Sichuan Airlines", "ZH": "Shenzhen Airlines",
    "KC": "Air Astana", "FS": "FlyArystan", "DV": "SCAT", "HY": "Uzbekistan Airways",
    "B2": "Белавиа", "9C": "Spring Airlines", "GS": "Tianjin Airlines",
    "KN": "China United", "HO": "Juneyao Air", "MF": "Xiamen Airlines",
    "SC": "Shandong Airlines", "JD": "Beijing Capital", "8L": "Lucky Air",
    "EU": "Chengdu Airlines", "G5": "China Express", "Y8": "Yakutia",
    "HZ": "Аврора", "R3": "Якутия", "5N": "Smartavia", "IO": "IrAero",
}


def load_config():
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"posted": []}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def months_to_search(start, end):
    """Месяцы (YYYY-MM), которые захватывает период от start до end."""
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def http_get_json(url, params, attempts=5):
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers={"Accept-Encoding": "identity"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 — слишком частые запросы: ждём и пробуем снова
            if e.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))


def find_cheapest(origin, destination, cfg, token):
    """Самый дешёвый билет по направлению с вылетом в ближайшие дни или None."""
    search = cfg["search"]
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=search["days_ahead"])
    best = None
    for month in months_to_search(now.date(), until.date()):
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": month,
            "one_way": str(search["one_way"]).lower(),
            "direct": str(search["direct_only"]).lower(),
            "currency": search["currency"],
            "sorting": "price",
            # Самый дешёвый в месяце может быть за пределами окна, поэтому берём все
            "limit": 1000,
            "token": token,
        }
        try:
            data = http_get_json(API_URL, params)
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"  ! {origin}-{destination} {month}: {e}", file=sys.stderr)
            continue
        for ticket in data.get("data") or []:
            if not now <= datetime.fromisoformat(ticket["departure_at"]) <= until:
                continue
            if ticket.get("transfers", 0) > search["max_transfers"]:
                continue
            if best is None or ticket["price"] < best["price"]:
                best = ticket
        time.sleep(0.3)
    return best


def build_link(ticket, links):
    params = {}
    if links["marker"]:
        marker = links["marker"]
        if links.get("sub_id_per_route"):
            marker += f".{ticket['origin']}-{ticket['destination']}"
        params["marker"] = marker
    if links.get("erid"):
        params["erid"] = links["erid"]
    link = "https://www.aviasales.ru" + ticket["link"]
    if params:
        link += ("&" if "?" in link else "?") + urllib.parse.urlencode(params)
    return link


def format_date(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def format_price(price, currency):
    sign = {"rub": "₽", "usd": "$", "eur": "€", "kzt": "₸"}.get(currency, currency.upper())
    return f"{price:,}".replace(",", " ") + f" {sign}"


def format_post(ticket, cfg):
    cities = cfg["cities"]
    origin = cities.get(ticket["origin"], ticket["origin"])
    destination = cities.get(ticket["destination"], ticket["destination"])
    transfers = ticket.get("transfers", 0)
    flight_type = "прямой рейс" if transfers == 0 else f"пересадок: {transfers}"
    airline = AIRLINES.get(ticket.get("airline"), ticket.get("airline", ""))

    lines = [
        f"✈️ <b>{origin} – {destination}</b>",
        "",
        f"💰 <b>{format_price(ticket['price'], cfg['search']['currency'])}</b>"
        + (" в одну сторону" if cfg["search"]["one_way"] else " туда-обратно"),
        f"📅 Вылет: {format_date(ticket['departure_at'])}",
    ]
    if ticket.get("return_at"):
        lines.append(f"🔙 Обратно: {format_date(ticket['return_at'])}")
    lines += [
        f"🛫 {airline}, {flight_type}",
        "",
        f'👉 <a href="{build_link(ticket, cfg["links"])}">Смотреть билет</a>',
        "",
        "<i>Цена и наличие актуальны на момент публикации.</i>",
        "",
        "#" + destination.replace(" ", "").replace("-", ""),
    ]
    if cfg["links"].get("ad_label"):
        lines += ["", f"<i>{cfg['links']['ad_label']}</i>"]
    return "\n".join(lines)


def ticket_id(ticket):
    return f"{ticket['origin']}-{ticket['destination']}-{ticket['departure_at'][:10]}-{ticket['price']}"


def recently_posted(state, days):
    """Билеты, опубликованные за последние `days` дней."""
    cutoff = time.time() - days * 86400
    return {p["id"] for p in state["posted"] if p["time"] >= cutoff}


def send_telegram(text, chat_id, bot_token):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    with urllib.request.urlopen(url, data=body, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram: {result}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="не отправлять в Telegram")
    parser.add_argument("--chat", help="отправить в этот чат вместо канала (тест, без записи в state.json)")
    args = parser.parse_args()
    chat_id = args.chat

    cfg = load_config()
    state = load_state()
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tp_token:
        sys.exit("Не задан TRAVELPAYOUTS_TOKEN")
    if not bot_token and not args.dry_run:
        sys.exit("Не задан TELEGRAM_BOT_TOKEN")

    skip_days = cfg["posting"]["skip_same_ticket_days"]
    skip = recently_posted(state, skip_days) if skip_days else set()

    candidates = []
    for origin, destinations in cfg["routes"].items():
        for destination in destinations:
            print(f"Ищу {origin}-{destination}...")
            ticket = find_cheapest(origin, destination, cfg, tp_token)
            if ticket and ticket_id(ticket) not in skip:
                candidates.append(ticket)

    # Топ самых дешёвых по всем направлениям сразу
    candidates.sort(key=lambda t: t["price"])
    top = candidates[: cfg["posting"]["posts_per_run"]]
    for origin in cfg["posting"].get("featured_origins", []):
        if any(t["origin"] == origin for t in top):
            continue
        best = next((t for t in candidates if t["origin"] == origin), None)
        if best:
            top.append(best)
    posted = 0
    for i, ticket in enumerate(top, 1):
        text = format_post(ticket, cfg)
        print(f"\n--- #{i} {ticket['origin']}-{ticket['destination']}: {ticket['price']} ---\n{text}\n")
        if args.dry_run:
            continue
        send_telegram(text, chat_id or cfg["telegram"]["chat_id"], bot_token)
        state["posted"].append({"id": ticket_id(ticket), "time": time.time()})
        posted += 1
        time.sleep(3)

    if not args.dry_run and not chat_id:
        # Храним только то, что ещё нужно для проверки повторов
        cutoff = time.time() - max(skip_days, 1) * 86400
        state["posted"] = [p for p in state["posted"] if p["time"] >= cutoff]
        save_state(state)
    print(f"Готово. Кандидатов: {len(candidates)}, опубликовано: {posted}.")


if __name__ == "__main__":
    main()
