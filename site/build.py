"""Сайт с дешёвыми билетами для продвижения каналов в Яндексе и Google.

Для каждого маршрута из config.toml (Китай), config_turkey.toml (Турция) и config_uae.toml (ОАЭ) — своя
страница с ценами на ближайшие дни. Цены те же, что у бота (Travelpayouts Data API).
Сайт публикуется в открытый репозиторий romandarkcox-hue/aviabilety (GitHub Pages).

Запуск:
    python site/build.py             — собрать сайт в папку _site
    python site/build.py --out DIR   — собрать в другую папку
    python site/build.py --limit 3   — только первые 3 маршрута раздела (для проверки)
"""

import argparse
import html
import json
import os
import shutil
import statistics
import sys
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import (AIRLINES, API_URL, MONTHS, MSK, WEEKDAYS, build_link, format_price,  # noqa: E402
                 http_get_json, load_config, months_to_search)

BASE_URL = "https://romandarkcox-hue.github.io/aviabilety"
# Ключ IndexNow: Яндекс узнаёт о новых и обновлённых страницах без кабинета вебмастера
INDEXNOW_KEY = "5b0f3c2a9e7d4f61a8c3b2d1e0f9a7c6"
# Сколько дней вперёд показывать цены
DAYS_AHEAD = 60

SECTIONS = [
    {
        "config": "config_turkey.toml",
        "slug": "turciya",
        "country": "Турцию",
        "country_nom": "Турция",
        "channel": "bilety_v_turciyu",
        "channel_pitch": "каждый день 15 самых дешёвых билетов в Анталью и Стамбул из России и СНГ",
    },
    {
        "config": "config.toml",
        "slug": "kitaj",
        "country": "Китай",
        "country_nom": "Китай",
        "channel": "tickettchina",
        "channel_pitch": "каждый день самые дешёвые билеты в Пекин, Шанхай, Гуанчжоу, Санью и другие города Китая",
    },
    {
        "config": "config_uae.toml",
        "slug": "oae",
        "country": "ОАЭ",
        "country_nom": "ОАЭ",
        "channel": "bilety_dubai",
        "channel_pitch": "каждый день самые дешёвые билеты в Дубай, Абу-Даби и Шарджу из России и СНГ",
    },
]

TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))

# Название города «откуда» и «куда» в нужном падеже: «из Казани», «в Анталью»
FROM_CASE = {
    "Москва": "Москвы", "Санкт-Петербург": "Санкт-Петербурга", "Казань": "Казани",
    "Екатеринбург": "Екатеринбурга", "Новосибирск": "Новосибирска", "Краснодар": "Краснодара",
    "Сочи": "Сочи", "Уфа": "Уфы", "Самара": "Самары", "Красноярск": "Красноярска",
    "Калининград": "Калининграда", "Пермь": "Перми", "Челябинск": "Челябинска",
    "Минеральные Воды": "Минеральных Вод", "Тюмень": "Тюмени", "Нижний Новгород": "Нижнего Новгорода",
    "Волгоград": "Волгограда", "Омск": "Омска", "Сургут": "Сургута", "Алматы": "Алматы",
    "Астана": "Астаны", "Шымкент": "Шымкента", "Ташкент": "Ташкента", "Самарканд": "Самарканда",
    "Минск": "Минска", "Ереван": "Еревана", "Баку": "Баку", "Тбилиси": "Тбилиси",
    "Бишкек": "Бишкека", "Ош": "Оша", "Душанбе": "Душанбе", "Иркутск": "Иркутска",
    "Владивосток": "Владивостока", "Хабаровск": "Хабаровска", "Благовещенск": "Благовещенска",
    "Чита": "Читы", "Якутск": "Якутска",
}
TO_CASE = {"Анталья": "Анталью", "Санья": "Санью", "Шарджа": "Шарджу"}
# «Отели в Анталье», «в Стамбуле»
IN_CASE = {"Анталья": "Анталье", "Стамбул": "Стамбуле", "Дубай": "Дубае", "Шарджа": "Шардже"}
MONTH_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
             "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def slugify(name):
    return "".join(TRANSLIT.get(ch, ch) for ch in name.lower()).replace(" ", "-")


def days_word(n):
    """«1 день», «3 дня», «53 дня», «11 дней» — в винительном падеже."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


def esc(text):
    return html.escape(str(text))


def short_date(d):
    return f"{d.day} {MONTHS[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def fetch_by_day(origin, destination, cfg, token):
    """Самый дешёвый билет на каждый день вылета в ближайшие DAYS_AHEAD дней."""
    search = cfg["search"]
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=DAYS_AHEAD)
    by_day = {}
    for month in months_to_search(now.date(), until.date()):
        params = {
            "origin": origin, "destination": destination, "departure_at": month,
            "one_way": "true", "direct": "false", "currency": search["currency"],
            "sorting": "price", "limit": 1000, "token": token,
        }
        try:
            data = http_get_json(API_URL, params)
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"  ! {origin}-{destination} {month}: {e}", file=sys.stderr)
            continue
        for ticket in data.get("data") or []:
            departure = datetime.fromisoformat(ticket["departure_at"])
            if not now <= departure <= until or ticket.get("transfers", 0) > search["max_transfers"]:
                continue
            day = departure.date()
            if day not in by_day or ticket["price"] < by_day[day]["price"]:
                by_day[day] = ticket
        time.sleep(0.3)
    return [by_day[d] for d in sorted(by_day)]


def ticket_link(ticket, links):
    # Отдельная метка «site», чтобы в статистике Travelpayouts продажи с сайта были видны отдельно
    return build_link(ticket, links, sub_id=f"site_{ticket['origin']}-{ticket['destination']}")


def page(title, description, canonical, body, schema=None):
    schema_tag = ""
    if schema:
        # «<» внутри JSON экранируем, чтобы название не могло закрыть тег script
        schema_json = json.dumps(schema, ensure_ascii=False).replace("<", "\\u003c")
        schema_tag = f'<script type="application/ld+json">{schema_json}</script>'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:locale" content="ru_RU">
<link rel="stylesheet" href="{BASE_URL}/style.css">
{schema_tag}
</head>
<body>
<header class="top"><a href="{BASE_URL}/">✈️ Дешёвые авиабилеты</a>
<nav><a href="{BASE_URL}/turciya/">Турция</a><a href="{BASE_URL}/kitaj/">Китай</a><a href="{BASE_URL}/oae/">ОАЭ</a></nav></header>
<main>
{body}
</main>
<footer>Цены — по данным поиска Aviasales, обновляются каждый день. Цена и наличие могут измениться
в любой момент — точную стоимость смотрите по ссылке на билет.</footer>
</body>
</html>
"""


def channel_box(section):
    return f"""<aside class="channel">
<p><b>Telegram-канал @{section['channel']}</b> — {esc(section['channel_pitch'])}.</p>
<a class="button" href="https://t.me/{section['channel']}">Подписаться в Telegram</a>
</aside>"""


def ad_labels(links, with_hotels):
    labels = [links.get("ad_label")]
    if with_hotels:
        labels.append(links.get("hotels_ad_label"))
    return "".join(f'<p class="ad">{esc(label)}</p>' for label in labels if label)


def breadcrumbs(items):
    return {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": name, "item": url}
                            for i, (name, url) in enumerate(items)],
    }


def route_page(route, section, siblings, cfg, updated):
    o, d, days = route["origin_name"], route["dest_name"], route["days"]
    links = cfg["links"]
    cheapest = min(days, key=lambda t: t["price"])
    cheapest_date = datetime.fromisoformat(cheapest["departure_at"])
    median = int(statistics.median(t["price"] for t in days))
    direct = [t for t in days if t.get("transfers", 0) == 0]
    airlines = sorted({AIRLINES.get(t.get("airline"), t.get("airline", "")) for t in days} - {""})
    currency = cfg["search"]["currency"]
    price = format_price(cheapest["price"], currency)
    from_o, to_d = FROM_CASE.get(o, o), TO_CASE.get(d, d)
    # Месяцы в порядке вылетов (декабрь раньше января); в конце месяца текущий уже не пишем
    months = list(dict.fromkeys(datetime.fromisoformat(t["departure_at"]).month for t in days))
    today = datetime.now(MSK)
    if len(months) > 1 and months[0] == today.month and today.day > 24:
        months = months[1:]
    period = "–".join(MONTH_NOM[m - 1] for m in months[:2])

    title = f"Авиабилеты {o} — {d} от {price}: дешёвые билеты на {period}"
    description = (f"Самые дешёвые билеты из {from_o} в {to_d} на каждый день: от {price} в одну сторону. "
                   f"{'Есть прямые рейсы. ' if direct else ''}Цены обновляются ежедневно.")
    canonical = f"{BASE_URL}/{section['slug']}/{route['slug']}/"

    rows = []
    for t in days:
        dep = datetime.fromisoformat(t["departure_at"])
        best = ' class="best"' if t is cheapest else ""
        transfers = "прямой" if t.get("transfers", 0) == 0 else f"пересадок: {t['transfers']}"
        rows.append(
            f"<tr{best}><td>{short_date(dep)}</td><td><b>{format_price(t['price'], currency)}</b></td>"
            f"<td>{esc(AIRLINES.get(t.get('airline'), t.get('airline', '')))}, {transfers}</td>"
            f'<td><a href="{esc(ticket_link(t, links))}" rel="sponsored nofollow">Смотреть</a></td></tr>')

    hotels = links.get("hotels_url")
    hotels_block = (f'<p class="hotels">🏨 <a href="{esc(hotels)}" rel="sponsored nofollow">'
                    f'Отели в {esc(IN_CASE.get(d, d))}</a></p>') if hotels else ""

    faq = [
        (f"Сколько стоит билет {o} — {d}?",
         f"Самый дешёвый билет в одну сторону сейчас стоит {price} — вылет {short_date(cheapest_date)}. "
         f"Обычная цена на ближайшие {DAYS_AHEAD} дней — около {format_price(median, currency)}."),
        (f"Есть ли прямые рейсы из {from_o} в {to_d}?",
         (f"Да: в ближайшие {DAYS_AHEAD} дней прямые рейсы есть в {days_word(len(direct))}, "
          f"самый дешёвый прямой — {format_price(min(t['price'] for t in direct), currency)}.")
         if direct else
         f"В ближайшие {DAYS_AHEAD} дней прямых рейсов в поиске нет, летают с одной пересадкой."),
        (f"Какие авиакомпании летают по маршруту {o} — {d}?",
         ", ".join(airlines) + "." if airlines else "Данных об авиакомпаниях пока нет."),
    ]
    faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in faq)

    related = "".join(
        f'<li><a href="{BASE_URL}/{section["slug"]}/{r["slug"]}/">{esc(r["origin_name"])} — '
        f'{esc(r["dest_name"])}</a> от {format_price(r["min_price"], currency)}</li>' for r in siblings)

    body = f"""<p class="crumbs"><a href="{BASE_URL}/">Главная</a> › <a href="{BASE_URL}/{section['slug']}/">Билеты в {section['country']}</a></p>
<h1>Дешёвые авиабилеты {esc(o)} — {esc(d)}</h1>
<p class="lead">Самый дешёвый билет из {esc(from_o)} в {esc(to_d)} — <b>{price}</b> в одну сторону,
вылет {short_date(cheapest_date)}, {esc(AIRLINES.get(cheapest.get('airline'), cheapest.get('airline', '')))}.
Ниже — минимальная цена на каждый день вылета. Обновлено {updated}.</p>
{channel_box(section)}
<h2>Цены на билеты {esc(o)} — {esc(d)} по дням</h2>
<div class="table"><table>
<thead><tr><th>Вылет</th><th>Цена</th><th>Рейс</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
{hotels_block}
{ad_labels(links, bool(hotels))}
<h2>Частые вопросы</h2>
{faq_html}
<h2>Похожие направления</h2>
<ul class="related">{related}</ul>
"""
    schema = [
        breadcrumbs([("Главная", f"{BASE_URL}/"),
                     (f"Билеты в {section['country']}", f"{BASE_URL}/{section['slug']}/"),
                     (f"{o} — {d}", canonical)]),
        {"@context": "https://schema.org", "@type": "FAQPage",
         "mainEntity": [{"@type": "Question", "name": q,
                         "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]},
    ]
    return page(title, description, canonical, body, schema)


def section_page(section, routes, cfg, updated):
    currency = cfg["search"]["currency"]
    canonical = f"{BASE_URL}/{section['slug']}/"
    cheapest = min(r["min_price"] for r in routes)
    by_dest = {}
    for r in sorted(routes, key=lambda r: r["min_price"]):
        by_dest.setdefault(r["dest_name"], []).append(r)
    groups = []
    for dest, items in by_dest.items():
        lis = "".join(f'<li><a href="{canonical}{r["slug"]}/">{esc(r["origin_name"])} — {esc(dest)}</a> '
                      f'от {format_price(r["min_price"], currency)}</li>' for r in items)
        groups.append(f"<h2>Билеты в {esc(TO_CASE.get(dest, dest))}</h2><ul class='related'>{lis}</ul>")
    title = f"Дешёвые авиабилеты в {section['country']} от {format_price(cheapest, currency)} — все направления"
    description = (f"Самые дешёвые билеты в {section['country']} из России и СНГ: цены на каждый день "
                   f"по {len(routes)} маршрутам, обновляются ежедневно.")
    body = f"""<p class="crumbs"><a href="{BASE_URL}/">Главная</a></p>
<h1>Дешёвые авиабилеты в {section['country']}</h1>
<p class="lead">Минимальные цены в одну сторону по {len(routes)} маршрутам на ближайшие {DAYS_AHEAD} дней.
Обновлено {updated}.</p>
{channel_box(section)}
{''.join(groups)}
"""
    schema = breadcrumbs([("Главная", f"{BASE_URL}/"), (f"Билеты в {section['country']}", canonical)])
    return page(title, description, canonical, body, schema)


def home_page(built, updated):
    blocks = []
    for section, routes, cfg in built:
        top = sorted(routes, key=lambda r: r["min_price"])[:8]
        lis = "".join(f'<li><a href="{BASE_URL}/{section["slug"]}/{r["slug"]}/">{esc(r["origin_name"])} — '
                      f'{esc(r["dest_name"])}</a> от {format_price(r["min_price"], cfg["search"]["currency"])}</li>'
                      for r in top)
        blocks.append(f"""<h2><a href="{BASE_URL}/{section['slug']}/">Билеты в {section['country']}</a></h2>
<ul class="related">{lis}</ul>
<p><a href="{BASE_URL}/{section['slug']}/">Все направления в {section['country']} →</a></p>
{channel_box(section)}""")
    title = "Дешёвые авиабилеты в Турцию, Китай и ОАЭ — цены на каждый день"
    description = ("Самые дешёвые билеты в Анталью, Стамбул, Пекин, Шанхай, Дубай и другие города из России и СНГ. "
                   "Цены обновляются каждый день.")
    body = f"""<h1>Дешёвые авиабилеты в Турцию, Китай и ОАЭ</h1>
<p class="lead">Каждый день собираем самые дешёвые билеты в одну сторону из России и стран СНГ.
Обновлено {updated}.</p>
{''.join(blocks)}
"""
    return page(title, description, f"{BASE_URL}/", body)


def build(out, limit, token):
    updated = datetime.now(MSK)
    updated_text = f"{updated.day} {MONTHS[updated.month - 1]} {updated.year}"
    built = []
    for section in SECTIONS:
        cfg = load_config(section["config"])
        routes = []
        pairs = [(o, d) for o, dests in cfg["routes"].items() for d in dests]
        for origin, destination in pairs[:limit] if limit else pairs:
            print(f"Ищу {origin}-{destination}...")
            days = fetch_by_day(origin, destination, cfg, token)
            if not days:
                continue
            o, d = cfg["cities"].get(origin, origin), cfg["cities"].get(destination, destination)
            routes.append({"origin_name": o, "dest_name": d, "slug": f"{slugify(o)}-{slugify(d)}",
                           "days": days, "min_price": min(t["price"] for t in days)})
        built.append((section, routes, cfg))

    if out.exists():
        shutil.rmtree(out)
    urls = [f"{BASE_URL}/"]
    for section, routes, cfg in built:
        sec_dir = out / section["slug"]
        sec_dir.mkdir(parents=True)
        (sec_dir / "index.html").write_text(section_page(section, routes, cfg, updated_text), encoding="utf-8")
        urls.append(f"{BASE_URL}/{section['slug']}/")
        for r in routes:
            # Похожие: тот же город назначения из других городов и из того же города в другие
            siblings = sorted((s for s in routes if s is not r and
                               (s["dest_name"] == r["dest_name"] or s["origin_name"] == r["origin_name"])),
                              key=lambda s: s["min_price"])[:10]
            route_dir = sec_dir / r["slug"]
            route_dir.mkdir()
            (route_dir / "index.html").write_text(
                route_page(r, section, siblings, cfg, updated_text), encoding="utf-8")
            urls.append(f"{BASE_URL}/{section['slug']}/{r['slug']}/")

    (out / "index.html").write_text(home_page(built, updated_text), encoding="utf-8")
    shutil.copy(Path(__file__).parent / "style.css", out / "style.css")
    lastmod = updated.date().isoformat()
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{u}</loc><lastmod>{lastmod}</lastmod></url>\n" for u in urls)
        + "</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n",
                                    encoding="utf-8")
    (out / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "urls.txt").write_text("\n".join(urls), encoding="utf-8")
    print(f"Готово: страниц {len(urls)} в {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="_site", help="куда собрать сайт")
    parser.add_argument("--limit", type=int, default=0, help="только первые N маршрутов раздела")
    args = parser.parse_args()
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        sys.exit("Не задан TRAVELPAYOUTS_TOKEN")
    build(Path(args.out), args.limit, token)


if __name__ == "__main__":
    main()
