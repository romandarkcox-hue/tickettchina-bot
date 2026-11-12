"""Автопостинг дешёвых авиабилетов в Telegram-канал.

Цены берутся из Travelpayouts Data API (кэш поисков Aviasales).
Каждый канал — свой файл настроек: config.toml (Китай), config_turkey.toml (Турция).
Переменные окружения:
    TRAVELPAYOUTS_TOKEN — токен API Travelpayouts
    TELEGRAM_BOT_TOKEN  — токен бота от @BotFather (имя переменной задаётся в настройках)
    PIXABAY_KEY         — (для картинок к постам) ключ Pixabay, фото неба на фон
    ADMIN_CHAT_ID       — (необязательно) личный чат для отчётов о публикации

Запуск:
    python bot.py            — найти билеты и опубликовать
    python bot.py --config config_turkey.toml — то же для другого канала
    python bot.py --dry-run  — только показать посты, ничего не отправлять
    python bot.py --chat ID  — отправить посты в указанный чат вместо канала
    python bot.py --force    — опубликовать, даже если сегодня уже публиковали
    python bot.py --card     — приложить к постам картинку (card.py), даже если в настройках выключено
"""

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).parent

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
DIRECTIONS_URL = "https://api.travelpayouts.com/v1/city-directions"
MSK = timezone(timedelta(hours=3))

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
    "TK": "Turkish Airlines", "PC": "Pegasus", "XQ": "SunExpress", "AJ": "AJet",
    "UT": "ЮТэйр", "WZ": "Red Wings", "EO": "Pegas Fly", "6R": "Алроса", "YC": "Ямал",
    "J2": "AZAL", "A9": "Georgian Airways", "SZ": "Somon Air", "ZM": "Air Manas",
    "RM": "Aircompany Armenia", "5F": "FlyOne", "C3": "Centrum Air", "C6": "Centrum Air",
    # Таиланд и пересадки по пути
    "TG": "Thai Airways", "FD": "Thai AirAsia", "XJ": "Thai AirAsia X", "PG": "Bangkok Airways", "SL": "Thai Lion Air",
    "VZ": "Thai VietJet", "EK": "Emirates", "QR": "Qatar Airways", "EY": "Etihad",
    "FZ": "flydubai", "G9": "Air Arabia", "VN": "Vietnam Airlines", "VJ": "VietJet",
    "MH": "Malaysia Airlines", "AK": "AirAsia", "OD": "Batik Air", "KE": "Korean Air",
    "OZ": "Asiana",
}


def load_config(path):
    with open(ROOT / path, "rb") as f:
        return tomllib.load(f)


def load_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"posted": []}


def save_state(state, path):
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
            # 429 — слишком частые запросы, 5xx — сбой сервера: ждём и пробуем снова
            if (e.code != 429 and e.code < 500) or attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # Сетевой сбой: тоже пробуем снова
            if attempt == attempts - 1:
                raise
            time.sleep(3 * (attempt + 1))


def find_cheapest(origin, destination, cfg, token):
    """Самый дешёвый билет по направлению с вылетом в ближайшие дни или None.

    В ticket["popularity"] — сколько цен по направлению в кэше Aviasales на эти дни:
    чем чаще направление ищут, тем их больше.
    """
    search = cfg["search"]
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=search["days_ahead"])
    best = None
    popularity = 0
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
            popularity += 1
            if ticket.get("transfers", 0) > search["max_transfers"]:
                continue
            if best is None or ticket["price"] < best["price"]:
                best = ticket
        time.sleep(0.3)
    if best:
        best["popularity"] = popularity
    return best


def search_popularity(origins, token):
    """Популярность маршрутов по рейтингу поисков Aviasales: {(откуда, куда): очки}.

    Для каждого города берём его рейтинг популярных направлений: 1-е место — 1 очко,
    последнее — почти 0. Чтобы сравнивать разные города, умножаем на вес города
    вылета — как высоко его самого ищут из других городов (Москва выше Сургута).
    """
    ranks = {}
    for origin in origins:
        try:
            data = http_get_json(DIRECTIONS_URL, {"origin": origin, "currency": "rub", "token": token})
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"  ! рейтинг {origin}: {e}", file=sys.stderr)
            continue
        dests = list((data.get("data") or {}).keys())
        ranks[origin] = {d: (len(dests) - i) / len(dests) for i, d in enumerate(dests)}
        time.sleep(0.3)
    weight = {o: sum(r.get(o, 0) for r in ranks.values()) for o in origins}
    # Город, которого нет ни в одном рейтинге, не обнуляем — даём вес самого слабого
    floor = min((w for w in weight.values() if w), default=1)
    return {(o, d): max(weight[o], floor) * score
            for o, r in ranks.items() for d, score in r.items()}


def build_link(ticket, links, sub_id=None):
    """Ссылка на билет. С trs (номер проекта Travelpayouts) — через переадресацию tp.media,
    тогда партнёрка считает клики. Без trs — прямая ссылка на Aviasales с marker."""
    url = "https://www.aviasales.ru" + ticket["link"]
    if not links["marker"]:
        return url
    if sub_id is None and links.get("sub_id_per_route"):
        sub_id = f"{ticket['origin']}-{ticket['destination']}"
    if links.get("trs"):
        params = {"campaign_id": 100, "marker": links["marker"], "p": 4114, "trs": links["trs"]}
        if sub_id:
            params["sub_id"] = sub_id
        if links.get("erid"):
            params["erid"] = links["erid"]
        params["u"] = url
        return "https://tp.media/r?" + urllib.parse.urlencode(params)
    params = {"marker": links["marker"] + (f".{sub_id}" if sub_id else "")}
    if links.get("erid"):
        params["erid"] = links["erid"]
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


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
        + (" - в одну сторону" if cfg["search"]["one_way"] else " - туда-обратно"),
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
    ]
    # Ссылка на отели цитатой — только для билетов туда: отели дома не нужны
    hotels = cfg["links"].get("hotels_url")
    show_hotels = hotels and not ticket.get("reverse")
    if show_hotels:
        text = cfg["links"].get("hotels_text", "Отели тут")
        lines += [f'<blockquote>🏨 <a href="{hotels}">{text}</a></blockquote>', ""]
    # Хэштеги: сначала город за границей, потом домашний — по нему люди ищут билеты из своего города
    abroad, home = (origin, destination) if ticket.get("reverse") else (destination, origin)
    lines += [" ".join("#" + city.replace(" ", "").replace("-", "") for city in (abroad, home))]
    labels = [cfg["links"].get("ad_label")]
    if show_hotels:
        labels.append(cfg["links"].get("hotels_ad_label"))
    labels = [label for label in labels if label]
    if labels:
        lines += ["", "<i>" + "\n".join(labels) + "</i>"]
    return "\n".join(lines)


WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def make_ticket_card(ticket, cfg, state):
    """Картинка к посту (card.py): фото неба с Pixabay, а если не вышло — нарисованное небо.
    Pillow нужен только здесь, поэтому импорт внутри."""
    from card import fetch_sky_photo, make_card
    photo = None
    try:
        used = set(state.get("used_photos", []))
        photo, photo_id = fetch_sky_photo(used)
        state["used_photos"] = (state.get("used_photos", []) + [photo_id])[-2000:]
    except Exception as e:
        print(f"Фото неба не получено, рисую небо сам: {e}", file=sys.stderr)
    cities = cfg["cities"]
    d = datetime.fromisoformat(ticket["departure_at"])
    transfers = ticket.get("transfers", 0)
    details = "Прямой рейс" if transfers == 0 else f"Пересадок: {transfers}"
    airline = AIRLINES.get(ticket.get("airline"), ticket.get("airline", ""))
    if airline:
        details += f" · {airline}"
    return make_card(
        cities.get(ticket["origin"], ticket["origin"]),
        cities.get(ticket["destination"], ticket["destination"]),
        ticket["origin"], ticket["destination"],
        format_price(ticket["price"], cfg["search"]["currency"]),
        f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS_FULL[d.weekday()]}",
        details,
        cfg["telegram"]["chat_id"],
        "в одну сторону" if cfg["search"]["one_way"] else "туда-обратно",
        sky_key=ticket["origin"] if ticket.get("reverse") else ticket["destination"],
        photo=photo,
    )


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


def send_photo(img, caption, chat_id, bot_token):
    """Фото с подписью (HTML). multipart собираем вручную, чтобы не тянуть requests."""
    boundary = "----tickettchina" + str(int(time.time() * 1000))
    crlf = "\r\n"
    body = b""
    for name, value in (("chat_id", chat_id), ("caption", caption), ("parse_mode", "HTML")):
        body += (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"{name}\"{crlf}{crlf}"
                 f"{value}{crlf}").encode("utf-8")
    body += (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"photo\"; "
             f"filename=\"ticket.jpg\"{crlf}Content-Type: image/jpeg{crlf}{crlf}").encode()
    body += img + f"{crlf}--{boundary}--{crlf}".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{bot_token}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram: {result}")


def caption_fits(html_text):
    """Подпись к фото — не больше 1024 символов видимого текста."""
    plain = re.sub(r"<[^>]+>", "", html_text)
    return len(plain.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")) <= 1024


def pick_tickets(cfg, skip, tp_token):
    """Билеты на сегодня в порядке публикации и сколько направлений проверено."""
    posting = cfg["posting"]
    # "cache" — сколько цен в кэше (подходит, когда цены есть не на каждый день),
    # "searches" — рейтинг популярных направлений Aviasales
    by_searches = posting.get("popularity") == "searches"
    reverse_count = posting.get("reverse_count", 0)
    if by_searches or reverse_count:
        print("Загружаю рейтинг популярных направлений...")
        all_cities = set(cfg["routes"]) | {d for ds in cfg["routes"].values() for d in ds}
        route_score = search_popularity(sorted(all_cities), tp_token)

    candidates = []
    for origin, destinations in cfg["routes"].items():
        for destination in destinations:
            print(f"Ищу {origin}-{destination}...")
            ticket = find_cheapest(origin, destination, cfg, tp_token)
            if ticket and ticket_id(ticket) not in skip:
                if by_searches:
                    ticket["popularity"] = route_score.get((origin, destination), 0)
                candidates.append(ticket)

    # Сначала самые дешёвые по всем направлениям сразу
    candidates.sort(key=lambda t: t["price"])
    cheap = candidates[: posting["cheapest_count"]]
    rest = []
    # Обязательно: самый дешёвый из Москвы и Петербурга
    for origin in posting.get("featured_origins", []):
        if any(t["origin"] == origin for t in cheap + rest):
            continue
        best = next((t for t in candidates if t["origin"] == origin), None)
        if best:
            rest.append(best)
    # Обязательно: самые популярные маршруты из СНГ, не больше одного из города
    cis = set(posting.get("cis_origins", []))
    cities_taken = {t["origin"] for t in cheap + rest if t["origin"] in cis}
    for t in sorted((t for t in candidates if t["origin"] in cis), key=lambda t: t["popularity"], reverse=True):
        if len(cities_taken) >= posting.get("cis_count", 0):
            break
        if t["origin"] not in cities_taken:
            rest.append(t)
            cities_taken.add(t["origin"])
    # Остальное — самые популярные направления
    popular = sorted((t for t in candidates if t not in cheap and t not in rest),
                     key=lambda t: t["popularity"], reverse=True)
    rest += popular[: max(posting["posts_per_run"] - len(cheap) - len(rest), 0)]
    # Дешёвые вперемешку с популярными, чтобы самые выгодные не шли подряд
    rest.sort(key=lambda t: t["price"])
    top = [t for pair in zip_longest(cheap, rest) for t in pair if t]

    # Обратные билеты (оттуда домой): самые популярные маршруты по рейтингу поисков
    if reverse_count:
        pairs = {(d, o) for o, ds in cfg["routes"].items() for d in ds}
        pairs = sorted((p for p in pairs if route_score.get(p)),
                       key=lambda p: route_score[p], reverse=True)
        per_city = posting.get("reverse_max_per_city", reverse_count)
        reverse = []
        for origin, destination in pairs:
            if len(reverse) >= reverse_count:
                break
            if sum(t["origin"] == origin for t in reverse) >= per_city:
                continue
            print(f"Ищу обратный {origin}-{destination}...")
            ticket = find_cheapest(origin, destination, cfg, tp_token)
            if ticket and ticket_id(ticket) not in skip:
                ticket["reverse"] = True
                reverse.append(ticket)
        top += sorted(reverse, key=lambda t: t["price"])
    return top, len(candidates)


def refresh(ticket, cfg, skip, tp_token):
    """Свежая цена по маршруту билета из очереди или None, если билетов больше нет."""
    fresh = find_cheapest(ticket["origin"], ticket["destination"], cfg, tp_token)
    if not fresh or ticket_id(fresh) in skip:
        return None
    fresh["reverse"] = ticket.get("reverse", False)
    return fresh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml", help="файл настроек канала")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять в Telegram")
    parser.add_argument("--chat", help="отправить в этот чат вместо канала (тест, без записи в state.json)")
    parser.add_argument("--force", action="store_true", help="публиковать, даже если сегодня уже публиковали")
    parser.add_argument("--card", action="store_true",
                        help="прикладывать картинку к посту, даже если в настройках [card] выключено (тест)")
    parser.add_argument("--next", action="store_true",
                        help="опубликовать один следующий пост из очереди на сегодня (запуск каждые 30 минут)")
    args = parser.parse_args()
    chat_id = args.chat

    cfg = load_config(args.config)
    state_path = ROOT / cfg["telegram"].get("state_file", "state.json")
    state = load_state(state_path)
    token_env = cfg["telegram"].get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    tp_token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    bot_token = os.environ.get(token_env)
    admin_chat = os.environ.get("ADMIN_CHAT_ID")
    if not tp_token:
        sys.exit("Не задан TRAVELPAYOUTS_TOKEN")
    if not bot_token and not args.dry_run:
        sys.exit(f"Не задан {token_env}")

    channel = cfg["telegram"]["chat_id"]
    now_msk = datetime.now(MSK)
    today = now_msk.date().isoformat()
    is_real_run = not args.dry_run and not chat_id
    skip_days = cfg["posting"]["skip_same_ticket_days"]
    skip = recently_posted(state, skip_days) if skip_days else set()

    def publish(ticket):
        text = format_post(ticket, cfg)
        print(f"\n--- {ticket['origin']}-{ticket['destination']}: {ticket['price']} ---\n{text}\n")
        with_card = args.card or cfg.get("card", {}).get("enabled", False)
        img = None
        if with_card and caption_fits(text):
            try:
                img = make_ticket_card(ticket, cfg, state)
            except Exception as e:  # картинка не должна срывать публикацию
                print(f"Картинка не получилась: {e}", file=sys.stderr)
        if args.dry_run:
            if img:
                out = ROOT / f"preview_{ticket['origin']}-{ticket['destination']}.jpg"
                out.write_bytes(img)
                print(f"Картинка: {out}")
            return
        if img:
            send_photo(img, text, chat_id or channel, bot_token)
        else:
            send_telegram(text, chat_id or channel, bot_token)
        state["posted"].append({"id": ticket_id(ticket), "time": time.time()})
        skip.add(ticket_id(ticket))
        if is_real_run:
            save_state(state, state_path)  # чтобы при сбое посередине не повторить уже отправленное

    def report(text):
        if is_real_run and admin_chat:
            send_telegram(text, admin_chat, bot_token)

    if args.next:
        # Режим очереди: первый запуск дня составляет очередь, каждый запуск публикует один пост
        first_hour, last_hour = cfg["posting"].get("hours", [0, 24])
        if not args.force and not first_hour <= now_msk.hour < last_hour:
            print(f"Сейчас {now_msk:%H:%M} МСК — вне времени публикаций ({first_hour}–{last_hour}).")
            return
        if state.get("queue_date") != today:
            queue, checked = pick_tickets(cfg, skip, tp_token)
            state["queue"], state["queue_date"] = queue, today
            if is_real_run:
                save_state(state, state_path)
            if queue:
                report(f"📋 {channel}: план на сегодня — постов: {len(queue)} "
                       f"(проверено направлений: {checked}).")
            else:
                report(f"⚠️ {channel}: сегодня публиковать нечего, подходящих билетов не найдено.")
        while state["queue"]:
            ticket = refresh(state["queue"].pop(0), cfg, skip, tp_token)
            if ticket:
                publish(ticket)
                break
            print("Билета по маршруту больше нет — беру следующий из очереди.")
        else:
            print("Очередь на сегодня пуста.")
        print(f"Осталось в очереди: {len(state['queue'])}.")
    else:
        # Всё сразу: ручной запуск и тесты
        if is_real_run and not args.force and state.get("last_run") == today:
            print(f"Сегодня ({today}) уже публиковали — пропускаю.")
            return
        top, checked = pick_tickets(cfg, skip, tp_token)
        for ticket in top:
            publish(ticket)
            if not args.dry_run:
                time.sleep(3)
        if is_real_run:
            if top:
                state["last_run"] = today
                report(f"✅ {channel}: опубликовано постов: {len(top)} (проверено направлений: {checked}).")
            else:
                report(f"⚠️ {channel}: сегодня ничего не опубликовано, подходящих билетов не найдено.")
        print(f"Готово. Проверено направлений: {checked}, постов: {len(top)}.")

    if is_real_run:
        # Храним только то, что ещё нужно для проверки повторов
        cutoff = time.time() - max(skip_days, 1) * 86400
        state["posted"] = [p for p in state["posted"] if p["time"] >= cutoff]
        save_state(state, state_path)


if __name__ == "__main__":
    main()
