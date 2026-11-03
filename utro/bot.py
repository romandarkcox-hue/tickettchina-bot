"""Автопостинг канала @dobroeytroru: открытки «Доброе утро», «Какой сегодня день», «Доброй ночи».

Картинки — бесплатный фотобанк Pixabay, на фото накладывается надпись и пожелание.
Праздники дня — my-calend.ru (запасной источник calend.ru).

Запуск:
    python bot.py --mode morning|day|night            — опубликовать в канал
    python bot.py --mode morning --chat 460504040     — тест: отправить в личку, без записи в state.json
    python bot.py --mode morning --dry-run            — только собрать картинку в preview_<mode>.jpg

Нужны переменные окружения: UTRO_BOT_TOKEN, PIXABAY_KEY.
"""

import argparse
import html
import io
import json
import os
import random
import re
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import wishes

HERE = Path(__file__).parent
STATE = HERE / "state.json"
CHANNEL = "@dobroeytroru"
MSK = timezone(timedelta(hours=3))
TITLE_FONT = str(HERE / "fonts" / "Lobster-Regular.ttf")
TEXT_FONT = str(HERE / "fonts" / "Comfortaa[wght].ttf")
UA = {"User-Agent": "Mozilla/5.0 (compatible; dobroeytro-bot/1.0)"}

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

# Темы для поиска в Pixabay (по-английски поиск точнее). Сезонные добавляются к общим.
MORNING_THEMES = ["morning coffee cup", "cup of tea morning", "flowers bouquet", "roses",
                  "cute kitten", "cute puppy", "sunrise landscape", "breakfast table",
                  "peonies", "daisies field", "tulips", "coffee and flowers"]
NIGHT_THEMES = ["starry night sky", "full moon night", "candle cozy evening", "sleeping kitten",
                "night lake moon", "evening lights lantern", "sunset sea", "cozy bedroom night"]
DAY_THEMES = ["calendar flowers", "notebook flowers coffee", "festive bouquet", "balloons celebration"]
SEASON_THEMES = {
    "winter": (["snow winter forest", "winter cozy cup", "snowy branches", "winter landscape"],
               ["winter night snow", "christmas lights night"]),
    "spring": (["spring flowers", "cherry blossom", "lilac", "spring morning nature"],
               ["spring evening sunset"]),
    "summer": (["summer field flowers", "sunflowers", "summer morning sea", "strawberries breakfast"],
               ["summer night stars", "fireflies night"]),
    "autumn": (["autumn leaves", "autumn forest sunlight", "autumn coffee cozy", "pumpkin autumn"],
               ["autumn evening rain window", "autumn night candle"]),
}

# Фото с такими метками не берём: открытки должны быть про уют и природу, без людей и мрачного
BAD_TAGS = {"woman", "women", "girl", "man", "men", "boy", "people", "person", "model", "female",
            "male", "lady", "portrait", "face", "body", "fashion", "sexy", "beauty", "bikini",
            "fantasy", "warrior", "weapon", "gun", "bow", "sword", "blood", "horror",
            "halloween", "skull", "death", "zombie", "witch", "devil", "demon", "gothic",
            "smoking", "cigarette", "alcohol", "wine", "beer", "whiskey", "cocktail", "drink alcohol"}
# Приметы чужого сезона: ёлка в ноябре или осенние листья в мае выглядят странно
SEASON_TAGS = {
    "winter": {"winter", "snow", "snowy", "snowflake", "frost", "ice", "christmas", "xmas",
               "christmas tree", "santa", "new year", "snowman"},
    "spring": {"spring", "cherry blossom", "easter"},
    "summer": {"summer", "beach", "sunflower", "sunflowers"},
    "autumn": {"autumn", "fall", "fall foliage", "pumpkin", "maple leaves", "halloween"},
}
# Ёлки и новогоднее — только в декабре и январе


def off_season_tags(month):
    cur = season(month)
    tags = set().union(*(t for s, t in SEASON_TAGS.items() if s != cur))
    if month != 12 and month != 1:
        tags |= {"christmas", "xmas", "christmas tree", "santa", "new year", "christmas lights"}
    return tags


# Пункты календаря, которые не подходят для доброго канала
SKIP_HOLIDAYS = ("культ",)

MORNING_TAGS ="#доброеутро #сдобрымутром #открытки #пожелания"
NIGHT_TAGS = "#добройночи #спокойнойночи #открытки #пожелания"
DAY_TAGS = "#какойсегоднядень #праздники #календарь"


# ---------- общее ----------

def now():
    return datetime.now(MSK)


def season(month):
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(month, "autumn")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def http_get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


# ---------- картинки ----------

def pick_photo(queries, state):
    """Находит в Pixabay фото по одной из тем, которое ещё не публиковали."""
    used = set(state.get("used_photos", []))
    key = os.environ["PIXABAY_KEY"]
    random.shuffle(queries)
    for q in queries:
        params = urllib.parse.urlencode({
            "key": key, "q": q, "image_type": "photo", "orientation": "vertical",
            "safesearch": "true", "order": "popular", "per_page": 100, "min_width": 1000,
        })
        hits = json.loads(http_get("https://pixabay.com/api/?" + params)).get("hits", [])
        bad = BAD_TAGS | off_season_tags(now().month)
        fresh = [h for h in hits if h["id"] not in used
                 and not bad & {t.strip() for t in h.get("tags", "").lower().split(",")}]
        if fresh:
            hit = random.choice(fresh[:30])
            img = Image.open(io.BytesIO(http_get(hit["largeImageURL"]))).convert("RGB")
            state.setdefault("used_photos", []).append(hit["id"])
            state["used_photos"] = state["used_photos"][-3000:]
            return img
    raise RuntimeError("Pixabay не нашёл новых картинок ни по одной теме")


def fit_font(draw, text, path, max_w, size):
    while size > 24:
        font = ImageFont.truetype(path, size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 4
    return ImageFont.truetype(path, size)


def make_card(img, title, text, color=(255, 236, 200)):
    """Надпись крупным шрифтом и пожелание внизу фото, на мягком затемнении."""
    img.thumbnail((1080, 1350))
    w, h = img.size
    shade = Image.new("L", (1, h))
    for y in range(h):
        t = max(0.0, (y - h * 0.45) / (h * 0.55))
        shade.putpixel((0, y), int(210 * t ** 1.3))
    shade = shade.resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, shade)

    d = ImageDraw.Draw(img)
    tf = fit_font(d, title, TITLE_FONT, w * 0.88, int(w * 0.14))
    wf = ImageFont.truetype(TEXT_FONT, int(w * 0.042))
    try:
        wf.set_variation_by_axes([600])
    except Exception:
        pass
    lines = textwrap.wrap(text, width=30) if text else []
    line_h = int(wf.size * 1.4)
    bottom = h - int(h * 0.06)
    y = bottom - line_h * len(lines)
    for line in lines:
        d.text((w / 2, y), line, font=wf, fill="white", anchor="mt")
        y += line_h
    ty = bottom - line_h * len(lines) - int(tf.size * 0.25)
    d.text((w / 2, ty), title, font=tf, fill=color, anchor="mb",
           stroke_width=3, stroke_fill=(50, 25, 10))
    return img


def next_wish(kind, pool, state):
    """Пожелание по кругу: случайный порядок, без повторов, пока не пройдём весь список."""
    order = state.setdefault("wish_order", {}).get(kind)
    if not order:
        order = list(range(len(pool)))
        random.shuffle(order)
    i = order.pop(0)
    state["wish_order"][kind] = order
    return pool[i % len(pool)]


# ---------- праздники ----------

def holidays_my_calend(day):
    url = f"https://my-calend.ru/holidays/{day.day}-{MONTHS_EN[day.month - 1]}"
    s = http_get(url).decode("utf-8", "replace")
    block = re.search(r'<ul class="holidays-items">(.*?)</ul>', s, re.S)
    if not block:
        return []
    items = []
    for li in re.findall(r"<li>(.*?)</li>", block.group(1), re.S):
        m = re.search(r"<a[^>]*>([^<]+)</a>", li) or re.search(r"<span>([^<&][^<]*)</span>", li)
        if not m:
            continue
        name = html.unescape(m.group(1)).strip()
        kinds = " ".join(re.findall(r'title="([^"]+)"', li)).lower()
        items.append((name, kinds))
    return items


def holidays_calend(day):
    url = f"https://www.calend.ru/holidays/{day.month}-{day.day}/"
    s = http_get(url).decode("utf-8", "replace")
    return [(html.unescape(n).strip(), "")
            for n in re.findall(r'<span class="title">\s*<a[^>]*>([^<]+)</a>', s)]


def get_holidays(day):
    for source in (holidays_my_calend, holidays_calend):
        try:
            items = source(day)
            if items:
                return items
        except Exception as e:
            print(f"{source.__name__}: {e}", file=sys.stderr)
    return []


def holiday_icon(name, kinds):
    low = name.lower()
    if any(w in low for w in ("памяти", "жертв", "скорб", "траур")):
        return "🕯"
    if "народн" in kinds:
        return "🌾"
    if "церков" in kinds or "православ" in kinds or any(
            w in low for w in ("икон", "святит", "преподоб", "мучени", "апостол", "богородиц")):
        return "⛪"
    if "росси" in low or "народного единства" in low:
        return "🇷🇺"
    return "🎉"


# ---------- посты ----------

def post_morning(state, today):
    themes = MORNING_THEMES + SEASON_THEMES[season(today.month)][0] * 2
    img = pick_photo(themes, state)
    wd = today.weekday()
    title = {4: "Доброй пятницы!", 5: "Хорошей субботы!", 6: "Доброе утро!"}.get(
        wd, random.choice(["Доброе утро!", "С добрым утром!", "Доброе утро!"]))
    wish = next_wish("morning", wishes.MORNING, state)
    caption = (f"☀️ {title}\n\nПерешлите тем, кому хотите пожелать доброго утра 💛"
               f"\n\n{MORNING_TAGS}\n👉 {CHANNEL}")
    return make_card(img, title, wish), caption


def post_night(state, today):
    themes = NIGHT_THEMES + SEASON_THEMES[season(today.month)][1] * 2
    img = pick_photo(themes, state)
    title = random.choice(["Доброй ночи!", "Спокойной ночи!", "Сладких снов!"])
    wish = next_wish("night", wishes.NIGHT, state)
    caption = (f"🌙 {title}\n\nПерешлите тем, кому хотите пожелать спокойной ночи 💙"
               f"\n\n{NIGHT_TAGS}\n👉 {CHANNEL}")
    return make_card(img, title, wish, color=(220, 230, 255)), caption


def post_day(state, today):
    items = get_holidays(today)
    if not items:
        raise RuntimeError("не удалось получить праздники ни с my-calend.ru, ни с calend.ru")
    date_str = f"{today.day} {MONTHS[today.month - 1]}"
    img = pick_photo(DAY_THEMES + SEASON_THEMES[season(today.month)][0], state)
    card = make_card(img, date_str, f"{WEEKDAYS[today.weekday()].capitalize()}. Какой сегодня праздник?")

    head = f"📅 Сегодня {date_str}, {WEEKDAYS[today.weekday()]}\n\nКакой сегодня день:\n"
    tail = (f"\n\nА что сегодня празднуете вы? 😊\n\n{DAY_TAGS} "
            f"#{today.day}{MONTHS[today.month - 1]}\n👉 {CHANNEL}")
    lines = []
    items = [(n, k) for n, k in items if not any(w in n.lower() for w in SKIP_HOLIDAYS)]
    for name, kinds in items[:12]:
        line = f"{holiday_icon(name, kinds)} {name}"
        # подпись к фото в Telegram — не длиннее 1024 знаков
        if len(head) + len("\n".join(lines + [line])) + len(tail) > 1000:
            break
        lines.append(line)
    return card, head + "\n".join(lines) + tail


def send_photo(token, chat, img, caption):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    boundary = "----utro" + str(random.randint(10**8, 10**9))
    parts = []
    for k, v in (("chat_id", str(chat)), ("caption", caption)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="card.jpg"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n".encode() + buf.getvalue() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(f"Telegram: {res}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["morning", "day", "night"], required=True)
    p.add_argument("--chat", help="тест: отправить в этот чат вместо канала, state.json не меняется")
    p.add_argument("--dry-run", action="store_true", help="не отправлять, сохранить preview_<mode>.jpg")
    p.add_argument("--force", action="store_true", help="публиковать, даже если сегодня уже было")
    a = p.parse_args()

    today = now().date()
    state = load_state()
    done_key = f"done_{a.mode}"
    if not (a.chat or a.dry_run or a.force) and state.get(done_key) == today.isoformat():
        print(f"{a.mode}: сегодня уже опубликовано, пропускаю")
        return

    build = {"morning": post_morning, "day": post_day, "night": post_night}[a.mode]
    img, caption = build(state, today)

    if a.dry_run:
        out = HERE / f"preview_{a.mode}.jpg"
        img.save(out, quality=90)
        print(out)
        print(caption)
        return

    send_photo(os.environ["UTRO_BOT_TOKEN"], a.chat or CHANNEL, img, caption)
    print(f"{a.mode}: отправлено в {a.chat or CHANNEL}")
    if not a.chat:
        state[done_key] = today.isoformat()
        save_state(state)


if __name__ == "__main__":
    main()
