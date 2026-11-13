"""Автопостинг канала @dobroeytroru: открытки «Доброе утро», «Какой сегодня день», «Доброй ночи».

Картинки — бесплатный фотобанк Pixabay, на фото накладывается надпись и пожелание.
Праздники дня — my-calend.ru (запасной источник calend.ru).

Запуск:
    python bot.py --mode morning|day|night            — опубликовать в канал
    python bot.py --mode morning --chat 460504040     — тест: отправить в личку, без записи в state.json
    python bot.py --mode morning --dry-run            — только собрать картинку в preview_<mode>.jpg

    python bot.py --mode morning --vk-test            — тест ВК: отложенная запись на завтра
    python bot.py --mode morning --only vk            — только в ВК (например, повторить после сбоя)

Нужны переменные окружения: UTRO_BOT_TOKEN, PIXABAY_KEY.
Для ВКонтакте ещё VK_TOKEN (ключ пользователя-админа с правами wall, photos, offline)
и VK_GROUP_ID (номер сообщества). Если их нет, бот публикует только в Telegram.
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
SKIP_HOLIDAYS = ("культ", "бахаи", "девятнадцатого дня", "католикос", "интронизац")

MORNING_TAGS ="#доброеутро #сдобрымутром #открытки #пожелания"
NIGHT_TAGS = "#добройночи #спокойнойночи #открытки #пожелания"
DAY_TAGS = "#какойсегоднядень #праздники #календарь"
HISTORY_TAGS = "#этотденьвистории #история #календарь"
HISTORY_THEMES = ["vintage books", "old map", "antique globe", "vintage clock", "old letters",
                  "antique compass", "old library", "vintage typewriter"]


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


COUNTRY_FLAGS = {
    "японии": "🇯🇵", "китая": "🇨🇳", "сша": "🇺🇸", "великобритании": "🇬🇧", "германии": "🇩🇪",
    "франции": "🇫🇷", "италии": "🇮🇹", "испании": "🇪🇸", "португалии": "🇵🇹", "австралии": "🇦🇺",
    "канады": "🇨🇦", "бразилии": "🇧🇷", "мексики": "🇲🇽", "аргентины": "🇦🇷", "панамы": "🇵🇦",
    "индии": "🇮🇳", "турции": "🇹🇷", "оаэ": "🇦🇪", "египта": "🇪🇬", "израиля": "🇮🇱",
    "казахстана": "🇰🇿", "беларуси": "🇧🇾", "белоруссии": "🇧🇾", "украины": "🇺🇦", "армении": "🇦🇲",
    "грузии": "🇬🇪", "азербайджана": "🇦🇿", "узбекистана": "🇺🇿", "киргизии": "🇰🇬",
    "кыргызстана": "🇰🇬", "таджикистана": "🇹🇯", "туркменистана": "🇹🇲", "молдовы": "🇲🇩",
    "молдавии": "🇲🇩", "польши": "🇵🇱", "чехии": "🇨🇿", "сербии": "🇷🇸", "болгарии": "🇧🇬",
    "греции": "🇬🇷", "финляндии": "🇫🇮", "швеции": "🇸🇪", "норвегии": "🇳🇴", "дании": "🇩🇰",
    "нидерландов": "🇳🇱", "бельгии": "🇧🇪", "австрии": "🇦🇹", "швейцарии": "🇨🇭", "ирландии": "🇮🇪",
    "кореи": "🇰🇷", "южной кореи": "🇰🇷", "вьетнама": "🇻🇳", "таиланда": "🇹🇭", "индонезии": "🇮🇩",
    "мальдив": "🇲🇻", "кубы": "🇨🇺", "монголии": "🇲🇳", "латвии": "🇱🇻", "литвы": "🇱🇹",
    "эстонии": "🇪🇪", "туниса": "🇹🇳", "марокко": "🇲🇦", "кипра": "🇨🇾", "черногории": "🇲🇪",
    "абхазии": "🇬🇪", "филиппин": "🇵🇭", "малайзии": "🇲🇾", "сингапура": "🇸🇬", "пакистана": "🇵🇰",
    "ирана": "🇮🇷", "ирака": "🇮🇶", "сирии": "🇸🇾", "колумбии": "🇨🇴", "чили": "🇨🇱", "перу": "🇵🇪",
    "венесуэлы": "🇻🇪", "новой зеландии": "🇳🇿", "юар": "🇿🇦", "нигерии": "🇳🇬", "кении": "🇰🇪", "румынии": "🇷🇴", "венгрии": "🇭🇺", "словакии": "🇸🇰", "хорватии": "🇭🇷",
}


def first_sentence(text):
    """Первое предложение описания, если оно короткое и законченное, иначе пусто."""
    text = re.sub(r"\s+", " ", text).strip()
    s = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁ0-9«])", text)[0]
    if s.endswith((".", "!")) and 25 <= len(s) <= 170 and s.count("(") == s.count(")"):
        return s
    return ""


def world_holidays(day):
    """Праздники других стран и международные — с calend.ru, с флагом и коротким пояснением."""
    url = f"https://www.calend.ru/holidays/{day.month}-{day.day}/"
    s = http_get(url).decode("utf-8", "replace")
    out = []
    for li in re.findall(r'<li class="three-three">(.*?)</li>', s, re.S):
        t = re.search(r'<span class="title">\s*<a[^>]*>([^<]+)', li)
        c = re.search(r'<div class="link">\s*<a[^>]*>([^<]+)', li)
        d = re.search(r'<p class="descr[^"]*">\s*<a[^>]*>([^<]+)', li)
        if not t or not c:
            continue
        cat = html.unescape(c.group(1)).strip()
        low = cat.lower()
        if "росси" in low or "православ" in low or "церков" in low or "именин" in low:
            continue
        if low.startswith("международ"):
            flag = "🌍"
        else:
            country = low.replace("праздники", "").strip()
            flag = COUNTRY_FLAGS.get(country, "🌐")
        name = html.unescape(t.group(1)).strip()
        descr = first_sentence(html.unescape(d.group(1))) if d else ""
        if any(w in (name + " " + descr).lower() for w in SKIP_HOLIDAYS):
            continue
        # пояснение, которое лишь повторяет название, не нужно
        if " ".join(name.lower().split()[:3]) in descr.lower():
            descr = ""
        out.append((flag, name, descr))
    return out


def history_events(day):
    """Главные события дня в истории (России и мира) с calend.ru: (год, событие), по порядку лет."""
    url = f"https://www.calend.ru/events/{day.month}-{day.day}/"
    s = http_get(url).decode("utf-8", "replace")
    out = []
    for li in re.findall(r'<li class="three-three">(.*?)</li>', s, re.S):
        t = re.search(r'<span class="title">\s*<a[^>]*>([^<]+)', li)
        y = re.search(r'<span class="year[^"]*">\s*(\d{1,4})', li)
        if not t:
            continue
        name = html.unescape(t.group(1)).strip()
        if any(w in name.lower() for w in SKIP_HOLIDAYS):
            continue
        out.append((int(y.group(1)) if y else 0, name))
    return sorted(out)


# В Википедии много трагедий — для доброго канала их пропускаем.
WIKI_SKIP = ("погиб", "катастроф", "теракт", "взрыв", "убит", "убийств", "расстрел", "давк",
             "крушени", "казн", "резн", "жертв", "бомбардир", "землетрясени", "авари")


def wiki_clean(s):
    """Вики-разметку в простой текст."""
    s = re.sub(r"<ref[^>/]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\{\{(?:iw|не переведено[^|]*)\|([^|}]+)[^}]*\}\}", r"\1", s, flags=re.I)
    for _ in range(3):  # шаблоны бывают вложенными
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:Файл|File|Изображение):[^\]]*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    s = html.unescape(re.sub(r"\s+", " ", s)).strip(" .;:—-")
    return s[:1].upper() + s[1:]


def history_events_wiki(day, count=8):
    """Запасной источник: раздел «События» страницы дня в ru.wikipedia.
    Событий там десятки, поэтому берём короткие и равномерно по эпохам."""
    title = urllib.parse.quote(f"{day.day}_{MONTHS[day.month - 1]}")
    s = http_get(f"https://ru.wikipedia.org/w/index.php?title={title}&action=raw").decode("utf-8")
    m = re.search(r"^==\s*События\s*==\s*$(.*?)^==[^=]", s, re.S | re.M)
    if not m:
        return []
    out, year = [], 0
    for line in m.group(1).splitlines():
        top = re.match(r"\*(?!\*)\s*(?:\[\[)?(\d{1,4})(?: год)?(?:\|[^\]]*)?(?:\]\])?\s*(?:—|-|–|$)\s*(.*)",
                       line)
        if top:
            year, text = int(top.group(1)), top.group(2)
        elif line.startswith("**") and year:
            text = line.lstrip("* ")
        else:
            if line.startswith("*") and not line.startswith("**"):
                year = 0  # строка без года — её подпункты не привязываем к прошлому году
            continue
        text = wiki_clean(text)
        low = text.lower()
        if (20 <= len(text) <= 180 and year <= day.year - 30
                and not any(w in low for w in SKIP_HOLIDAYS + WIKI_SKIP)):
            out.append((year, text))
    out.sort()
    if len(out) <= count:
        return out
    step = (len(out) - 1) / (count - 1)
    return [out[round(i * step)] for i in range(count)]


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

    try:
        world = world_holidays(today)
    except Exception as e:
        print(f"calend.ru (праздники мира): {e}", file=sys.stderr)
        world = []

    esc = html.escape
    weekday = WEEKDAYS[today.weekday()]
    parts = [f"📅 <b>Сегодня {date_str}, {weekday}</b>"]
    items = [(n, k) for n, k in items if not any(w in n.lower() for w in SKIP_HOLIDAYS)]
    parts.append("\n\n🇷🇺 <b>Праздники в России</b>\n" + "\n".join(
        f"{holiday_icon(n, k)} {esc(n)}" for n, k in items[:10]))
    if world:
        lines = []
        for flag, name, descr in world[:6]:
            lines.append(f"{flag} <b>{esc(name)}</b>" + (f". {esc(descr)}" if descr else ""))
        parts.append("\n\n🌍 <b>Праздники в мире</b>\n" + "\n".join(lines))
    parts.append(f"\n\nА что сегодня празднуете вы? 😊\n\n{DAY_TAGS} "
                 f"#{today.day}{MONTHS[today.month - 1]}\n👉 {CHANNEL}")
    text = "".join(parts)[:4000]
    short = f"📅 <b>{date_str}, {weekday}</b> — какой сегодня день? Рассказываем ниже 👇"
    return card, text, short


def post_history(state, today):
    try:
        events = history_events(today)
    except Exception as e:
        print(f"calend.ru (события): {e}", file=sys.stderr)
        events = []
    if not events:
        print("calend.ru не дал событий — беру из Википедии", file=sys.stderr)
        events = history_events_wiki(today)
    if not events:
        raise RuntimeError("не удалось получить события дня ни с calend.ru, ни из Википедии")
    date_str = f"{today.day} {MONTHS[today.month - 1]}"
    img = pick_photo(HISTORY_THEMES, state)
    card = make_card(img, "Этот день в истории", date_str, color=(240, 220, 180))
    lines = []
    for year, name in events[:8]:
        lines.append(f"📌 <b>{year} г.</b> — {html.escape(name)}" if year else f"📌 {html.escape(name)}")
    text = (f"📜 <b>Этот день в истории — {date_str}</b>\n\n" + "\n\n".join(lines)
            + f"\n\nА какое событие удивило вас больше всего? 🤔\n\n{HISTORY_TAGS} "
            f"#{today.day}{MONTHS[today.month - 1]}\n👉 {CHANNEL}")
    short = f"📜 <b>Этот день в истории — {date_str}</b>. Рассказываем ниже 👇"
    return card, text, short


def tg_call(token, method, data):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urllib.parse.urlencode(data).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(f"Telegram: {res}")


def post_multipart(url, fields, img):
    """POST multipart/form-data с картинкой в поле photo, ответ — JSON."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    boundary = "----utro" + str(random.randint(10**8, 10**9))
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="card.jpg"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n".encode() + buf.getvalue() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def send_photo(token, chat, img, caption):
    res = post_multipart(f"https://api.telegram.org/bot{token}/sendPhoto",
                         {"chat_id": str(chat), "caption": caption, "parse_mode": "HTML"}, img)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram: {res}")


def warn_admin(text):
    """Короткое предупреждение админу в личку от бота (не роняет запуск)."""
    chat = os.environ.get("ADMIN_CHAT_ID")
    if not chat:
        return
    try:
        tg_call(os.environ["UTRO_BOT_TOKEN"], "sendMessage", {"chat_id": chat, "text": text[:4000]})
    except Exception as e:
        print(f"не удалось предупредить админа: {e}", file=sys.stderr)


# ---------- ВКонтакте ----------

def vk_call(method, token=None, **params):
    params.update(access_token=token or os.environ["VK_TOKEN"], v="5.199")
    req = urllib.request.Request(f"https://api.vk.com/method/{method}",
                                 data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    if "error" in res:
        raise RuntimeError(f"ВК {method}: {res['error'].get('error_msg')}")
    return res["response"]


def vk_text(caption):
    """Текст из Telegram (HTML) → обычный текст для стены ВК."""
    text = html.unescape(re.sub(r"<[^>]+>", "", caption))
    return text.replace(f"👉 {CHANNEL}", f"👉 Мы в Telegram: https://t.me/{CHANNEL.lstrip('@')}")


def vk_post(img, caption, delayed=False):
    """Открытка с текстом на стену сообщества. delayed — отложенная запись на завтра (для проверки)."""
    gid = os.environ["VK_GROUP_ID"].lstrip("-")

    def upload():
        server = vk_call("photos.getWallUploadServer", group_id=gid)
        up = post_multipart(server["upload_url"], {}, img)
        return vk_call("photos.saveWallPhoto", group_id=gid, server=up["server"],
                       photo=up["photo"], hash=up["hash"])[0]

    params = {"owner_id": f"-{gid}", "from_group": 1, "message": vk_text(caption)}
    if delayed:
        params["publish_date"] = int(time.time()) + 86400
    try:
        photo = vk_retry(upload, retry_on=Exception)
        params["attachments"] = f"photo{photo['owner_id']}_{photo['id']}"
        # wall.post повторяем только при явной ошибке ВК: тогда записи точно нет.
        # При обрыве связи запись могла создаться — повтор дал бы дубль.
        return vk_retry(lambda: vk_call("wall.post", **params), retry_on=RuntimeError)["post_id"], None
    except Exception as e:
        # Подстраховка: ключ пользователя заблокирован (Flood control 12.11) или ВК сбоит —
        # публикуем хотя бы текст ключом сообщества (фото таким ключом грузить нельзя).
        group_token = os.environ.get("VK_GROUP_TOKEN")
        if not group_token or "attachments" in params:
            raise
        print(f"ВК: фото не загрузилось ({e}), публикую текст ключом сообщества", file=sys.stderr)
        post_id = vk_retry(lambda: vk_call("wall.post", token=group_token, **params),
                           retry_on=RuntimeError)["post_id"]
        return post_id, e


def vk_retry(fn, retry_on, attempts=3, pause=45):
    """ВК иногда разово сбоит (например, «photo is undefined») — пробуем ещё пару раз."""
    for i in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as e:
            # Flood control — блокировка на часы, повторять через 45 с бессмысленно.
            if i == attempts or "Flood control" in str(e):
                raise
            print(f"ВК: попытка {i} не удалась ({e}), повтор через {pause} с", file=sys.stderr)
            time.sleep(pause)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["morning", "day", "history", "night"], required=True)
    p.add_argument("--chat", help="тест: отправить в этот чат вместо канала, state.json не меняется")
    p.add_argument("--dry-run", action="store_true", help="не отправлять, сохранить preview_<mode>.jpg")
    p.add_argument("--force", action="store_true", help="публиковать, даже если сегодня уже было")
    p.add_argument("--only", choices=["tg", "vk"], help="публиковать только в Telegram или только в ВК")
    p.add_argument("--vk-test", action="store_true",
                   help="тест ВК: отложенная запись на завтра (видна только админам), state.json не меняется")
    a = p.parse_args()

    today = now().date()
    state = load_state()
    test = bool(a.chat or a.vk_test)
    # Куда публикуем. ВК — только если заданы VK_TOKEN и VK_GROUP_ID.
    vk_ready = bool(os.environ.get("VK_TOKEN") and os.environ.get("VK_GROUP_ID"))
    targets = {"tg": a.only != "vk" and not a.vk_test,
               "vk": vk_ready and a.only != "tg" and (a.vk_test or not a.chat)}
    if a.vk_test and not vk_ready:
        raise SystemExit("для --vk-test нужны VK_TOKEN и VK_GROUP_ID")
    keys = {"tg": f"done_{a.mode}", "vk": f"done_vk_{a.mode}"}
    if not (test or a.dry_run or a.force):
        for t in targets:
            if targets[t] and state.get(keys[t]) == today.isoformat():
                print(f"{a.mode} ({t}): сегодня уже опубликовано, пропускаю")
                targets[t] = False
    if not a.dry_run and not any(targets.values()):
        return

    build = {"morning": post_morning, "day": post_day, "history": post_history,
             "night": post_night}[a.mode]
    img, caption, *rest = build(state, today)

    if a.dry_run:
        out = HERE / f"preview_{a.mode}.jpg"
        img.save(out, quality=90)
        print(out)
        print(caption)
        return

    errors = []
    if targets["tg"]:
        token, chat = os.environ["UTRO_BOT_TOKEN"], a.chat or CHANNEL
        # Подпись к фото в Telegram — не длиннее 1024 знаков. Длинный текст идёт следом
        # отдельным сообщением, а под открыткой — короткая подпись.
        if len(caption) <= 1024 or not rest:
            send_photo(token, chat, img, caption[:1024])
        else:
            send_photo(token, chat, img, rest[0])
            tg_call(token, "sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML",
                                           "link_preview_options": json.dumps({"is_disabled": True})})
        print(f"{a.mode}: отправлено в {chat}")
        if not test:
            state[keys["tg"]] = today.isoformat()

    if targets["vk"]:
        # Сбой ВК не должен мешать Telegram: запоминаем ошибку и сообщаем о ней в конце.
        try:
            post_id, photo_err = vk_post(img, caption, delayed=a.vk_test)
            print(f"{a.mode}: ВК, запись {post_id}" + (" (отложенная, на завтра)" if a.vk_test else "")
                  + (" — без открытки, только текст" if photo_err else ""))
            if photo_err and not test:
                warn_admin(f"⚠️ @dobroeytroru ({a.mode}): в ВК ушёл только текст, "
                           f"открытка не загрузилась: {photo_err}")
            if not test:
                state[keys["vk"]] = today.isoformat()
        except Exception as e:
            errors.append(f"ВК: {e}")

    if not test:
        save_state(state)
    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()
