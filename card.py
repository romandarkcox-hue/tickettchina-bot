"""Карточка к посту с билетом: минималистичная, на фоне неба.

У каждого города своё небо (ночь со звёздами, закат, рассвет с облаками…) — всегда одно и то же,
чтобы город узнавался по картинке.
"""
import io
import json
import os
import random
import re
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).parent / "fonts"
HEAD = FONTS / "Unbounded[wght].ttf"
BODY = FONTS / "Manrope[wght].ttf"

W, H = 1280, 720
SCALE = 2                       # рисуем вдвое крупнее и уменьшаем — ровные края

# Небо: цвет сверху, цвет снизу, свечение (x, y, радиус, цвет), звёзды, облака, тёмное ли
SKIES = {
    "night": dict(top=(8, 14, 46), bottom=(38, 44, 104), glow=(0.86, 0.22, 0.30, (90, 110, 200)),
                  moon=True, stars=1.0, clouds=0, dark=True),
    "twilight": dict(top=(24, 34, 86), bottom=(214, 112, 100), glow=(0.80, 1.00, 0.55, (255, 160, 120)),
                     stars=0.6, clouds=0, dark=True),
    "sunset": dict(top=(84, 56, 150), bottom=(236, 124, 86), glow=(0.82, 0.92, 0.38, (255, 176, 104)),
                   stars=0.15, clouds=0, dark=True),
    "aurora": dict(top=(6, 22, 40), bottom=(12, 58, 66), glow=(0.70, 0.15, 0.55, (40, 200, 150)),
                   stars=0.9, clouds=0, dark=True),
    "lavender": dict(top=(52, 36, 104), bottom=(198, 140, 186), glow=(0.15, 0.10, 0.40, (140, 110, 220)),
                     stars=0.5, clouds=0, dark=True),
    "dawn": dict(top=(176, 208, 255), bottom=(255, 222, 200), glow=(0.85, 0.85, 0.40, (255, 236, 190)),
                 stars=0, clouds=4, dark=False),
    "day": dict(top=(126, 186, 250), bottom=(226, 242, 255), glow=(0.90, 0.05, 0.30, (255, 250, 225)),
                stars=0, clouds=5, dark=False),
    # Градиенты «в цветах города» (ОАЭ): без звёзд и облаков, accent — цвет города прилёта
    "dubai": dict(top=(10, 14, 34), bottom=(34, 30, 52), glow=(1.00, 1.05, 0.50, (226, 168, 70)),
                  stars=0, clouds=0, dark=True, accent=(255, 208, 110)),
    "abudhabi": dict(top=(244, 246, 242), bottom=(96, 196, 200), glow=(0.90, 0.10, 0.40, (255, 255, 255)),
                     stars=0, clouds=0, dark=False, accent=(0, 112, 128)),
    "sharjah": dict(top=(8, 22, 66), bottom=(22, 92, 158), glow=(0.92, 0.05, 0.40, (70, 160, 220)),
                    stars=0, clouds=0, dark=True, accent=(240, 206, 150)),
    # Таиланд
    "bangkok": dict(top=(26, 8, 44), bottom=(96, 22, 96), glow=(0.95, 0.95, 0.45, (226, 52, 140)),
                    stars=0, clouds=0, dark=True, accent=(255, 206, 96)),
    "phuket": dict(top=(226, 248, 246), bottom=(20, 172, 188), glow=(0.90, 0.05, 0.40, (255, 252, 236)),
                   stars=0, clouds=0, dark=False, accent=(0, 96, 120)),
    "pattaya": dict(top=(48, 26, 92), bottom=(226, 98, 92), glow=(0.92, 1.00, 0.40, (255, 150, 90)),
                    stars=0, clouds=0, dark=True, accent=(255, 214, 140)),
    "krabi": dict(top=(4, 34, 32), bottom=(12, 112, 92), glow=(0.92, 0.10, 0.40, (60, 170, 130)),
                  stars=0, clouds=0, dark=True, accent=(196, 240, 160)),
    "samui": dict(top=(255, 244, 228), bottom=(255, 188, 156), glow=(0.92, 0.95, 0.40, (255, 222, 170)),
                  stars=0, clouds=0, dark=False, accent=(206, 72, 52)),
    "chiangmai": dict(top=(12, 14, 42), bottom=(46, 28, 66), glow=(0.92, 0.92, 0.45, (236, 132, 56)),
                      stars=0, clouds=0, dark=True, accent=(255, 182, 96)),
}
# Популярные города — вручную, чтобы соседние по популярности не совпадали
CITY_SKY = {
    "BJS": "night", "SHA": "twilight", "CAN": "sunset", "SZX": "dawn", "HRB": "aurora",
    "SYX": "day", "CTU": "lavender", "URC": "aurora", "HGH": "dawn", "XMN": "sunset",
    "DLC": "day", "TAO": "twilight", "XIY": "lavender", "KMG": "day", "CKG": "night",
    "WUH": "sunset", "NKG": "twilight", "HAK": "dawn", "SHE": "night", "HKG": "night",
}

THEMES = {
    False: dict(ink=(20, 20, 22), muted=(96, 88, 92), accent=(214, 38, 46), line=(20, 20, 22, 70),
                dot=(255, 255, 255)),
    True: dict(ink=(255, 255, 255), muted=(232, 230, 244), accent=(255, 205, 96),
               line=(255, 255, 255, 90), dot=(20, 24, 60)),
}


def sky_for(key):
    name = CITY_SKY.get(key) or sorted(SKIES)[zlib.crc32(key.encode()) % len(SKIES)]
    return SKIES[name]


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def sky_image(w, h, sky, seed):
    rnd = random.Random(seed)
    # вертикальный градиент
    col = Image.new("RGB", (1, 256))
    for y in range(256):
        col.putpixel((0, y), mix(sky["top"], sky["bottom"], (y / 255) ** 1.2))
    img = col.resize((w, h), Image.BICUBIC)

    # мягкое свечение (луна, солнце, сияние)
    gx, gy, gr, gc = sky["glow"]
    small = Image.new("L", (w // 8, h // 8), 0)
    sw, sh = small.size
    ImageDraw.Draw(small).ellipse([gx * sw - gr * sw, gy * sh - gr * sw, gx * sw + gr * sw, gy * sh + gr * sw],
                                  fill=150)
    mask = small.filter(ImageFilter.GaussianBlur(sw * 0.10)).resize((w, h), Image.BICUBIC)
    img = Image.composite(Image.new("RGB", (w, h), gc), img, mask)

    # облака: размытые белые «кучки» внизу
    if sky["clouds"]:
        cl = Image.new("L", (w // 4, h // 4), 0)
        cw, ch = cl.size
        dc = ImageDraw.Draw(cl)
        for _ in range(sky["clouds"]):
            cx, cy = rnd.uniform(0.35, 1.0) * cw, rnd.uniform(0.55, 0.95) * ch
            for _ in range(6):
                rx, ry = rnd.uniform(0.05, 0.12) * cw, rnd.uniform(0.03, 0.06) * cw
                ox, oy = rnd.uniform(-0.10, 0.10) * cw, rnd.uniform(-0.03, 0.02) * cw
                dc.ellipse([cx + ox - rx, cy + oy - ry, cx + ox + rx, cy + oy + ry], fill=170)
        cl = cl.filter(ImageFilter.GaussianBlur(cw * 0.015)).resize((w, h), Image.BICUBIC)
        img = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, cl)

    d = ImageDraw.Draw(img, "RGBA")
    # на тёмных небесах слегка затемняем низ — там дата и цена
    if sky["dark"]:
        shade = Image.new("L", (1, 256))
        for y in range(256):
            shade.putpixel((0, y), int(110 * max(0, (y / 255 - 0.55) / 0.45) ** 1.3))
        img = Image.composite(Image.new("RGB", (w, h), (10, 8, 30)), img, shade.resize((w, h), Image.BICUBIC))
        d = ImageDraw.Draw(img, "RGBA")
    # звёзды: гуще вверху, к низу пропадают
    for _ in range(int(260 * sky["stars"])):
        x = rnd.uniform(0, w)
        y = (rnd.random() ** 1.8) * h
        a = int(255 * (1 - y / h) * rnd.uniform(0.35, 1.0))
        r = rnd.choice([1, 1, 1, 1.5, 2]) * SCALE * 0.9
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
    for _ in range(int(5 * sky["stars"])):                     # несколько ярких, с лучиками
        x, y = rnd.uniform(0.45, 0.97) * w, rnd.uniform(0.05, 0.45) * h
        L = rnd.uniform(7, 12) * SCALE
        d.line([(x - L, y), (x + L, y)], fill=(255, 255, 255, 170), width=SCALE)
        d.line([(x, y - L), (x, y + L)], fill=(255, 255, 255, 170), width=SCALE)
        d.ellipse([x - 2.5 * SCALE, y - 2.5 * SCALE, x + 2.5 * SCALE, y + 2.5 * SCALE], fill=(255, 255, 255))

    # луна-полумесяц
    if sky.get("moon"):
        mx, my, mr = 0.86 * w, 0.22 * h, 34 * SCALE
        moon = Image.new("L", (w, h), 0)
        dm = ImageDraw.Draw(moon)
        dm.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=235)
        dm.ellipse([mx - mr + 18 * SCALE, my - mr - 8 * SCALE, mx + mr + 18 * SCALE, my + mr - 8 * SCALE], fill=0)
        img = Image.composite(Image.new("RGB", (w, h), (255, 244, 214)), img, moon)
    return img


# Фото неба с Pixabay: темы, которые понравились владельцу канала
PHOTO_QUERIES = ["sunset clouds sky", "above the clouds", "sea of clouds", "airplane window view",
                 "airplane wing clouds", "night sky stars", "milky way", "sky clouds sunrise"]
PHOTO_BAD_TAGS = {"woman", "women", "girl", "man", "men", "boy", "people", "person", "model",
                  "female", "male", "child", "children", "couple", "wedding", "text", "logo"}


# Фото городов вылета — для обратных билетов (фон — город, куда летим).
# Код города: (темы поиска на Pixabay, слова, одно из которых должно быть в тегах фото)
HOME_CITY_PHOTOS = {
    "MOW": (["moscow kremlin", "red square moscow", "moscow city", "moscow"], ["moscow", "kremlin", "red square", "moskva"]),
    "LED": (["saint petersburg", "st petersburg russia", "hermitage petersburg", "peterhof", "neva river"],
            ["petersburg", "peterhof", "hermitage", "neva", "sankt"]),
    "KZN": (["kazan kremlin", "kazan"], ["kazan"]),
    "SVX": (["yekaterinburg", "ekaterinburg"], ["yekaterinburg", "ekaterinburg"]),
    "OVB": (["novosibirsk"], ["novosibirsk"]),
    "KRR": (["krasnodar"], ["krasnodar"]),
    "AER": (["sochi", "krasnaya polyana"], ["sochi", "krasnaya polyana"]),
    "UFA": (["ufa bashkortostan", "ufa russia"], ["ufa"]),
    "KUF": (["samara russia", "samara volga"], ["samara"]),
    "KJA": (["krasnoyarsk"], ["krasnoyarsk"]),
    "KGD": (["kaliningrad"], ["kaliningrad"]),
    "PEE": (["perm russia"], ["perm"]),
    "CEK": (["chelyabinsk"], ["chelyabinsk"]),
    "MRV": (["pyatigorsk", "kislovodsk"], ["pyatigorsk", "kislovodsk"]),
    "TJM": (["tyumen"], ["tyumen"]),
    "GOJ": (["nizhny novgorod"], ["nizhny novgorod", "nizhny"]),
    "VOG": (["volgograd", "mamayev kurgan"], ["volgograd", "mamayev"]),
    "OMS": (["omsk"], ["omsk"]),
    "SGC": (["surgut"], ["surgut"]),
    "IKT": (["irkutsk", "baikal"], ["irkutsk", "baikal"]),
    "VVO": (["vladivostok"], ["vladivostok"]),
    "KHV": (["khabarovsk"], ["khabarovsk"]),
    "ALA": (["almaty"], ["almaty"]),
    "NQZ": (["astana", "baiterek"], ["astana", "baiterek", "nur-sultan"]),
    "CIT": (["shymkent"], ["shymkent"]),
    "TAS": (["tashkent"], ["tashkent"]),
    "SKD": (["samarkand", "registan"], ["samarkand", "registan"]),
    "MSQ": (["minsk"], ["minsk"]),
    "EVN": (["yerevan"], ["yerevan"]),
    "BAK": (["baku", "baku flame towers"], ["baku"]),
    "TBS": (["tbilisi"], ["tbilisi"]),
    "BSZ": (["bishkek"], ["bishkek"]),
    "OSS": (["osh kyrgyzstan"], ["osh"]),
    "DYU": (["dushanbe"], ["dushanbe"]),
}


def fetch_sky_photo(used_ids, key=None, tries=3):
    """Случайное фото неба, которого ещё не было (по id). Возвращает (PIL.Image, id)."""
    return fetch_photo(PHOTO_QUERIES, used_ids, key, tries)


def fetch_photo(queries, used_ids, key=None, tries=3, must_tags=None):
    """Случайное фото с Pixabay по одной из тем; сначала те, которых ещё не было (по id).
    must_tags — фото подходит, только если в его тегах есть одно из этих слов целиком
    (Pixabay ищет неточно: по «phaselis» находит фазы Луны). Если новых фото не осталось —
    берёт уже использованное. Возвращает (PIL.Image, id)."""
    key = key or os.environ.get("PIXABAY_KEY")
    if not key:
        raise RuntimeError("не задан PIXABAY_KEY")
    ua = {"User-Agent": "Mozilla/5.0 (compatible; tickettchina-bot/1.0)"}
    patterns = [re.compile(r"\b" + re.escape(m.lower()) + r"\b") for m in (must_tags or [])]
    queries = list(queries)
    random.shuffle(queries)
    seen = []
    for q in queries[:tries]:
        params = urllib.parse.urlencode({
            "key": key, "q": q, "image_type": "photo", "orientation": "horizontal",
            "safesearch": "true", "order": "popular", "per_page": 80, "min_width": 1600,
        })
        req = urllib.request.Request("https://pixabay.com/api/?" + params, headers=ua)
        hits = json.load(urllib.request.urlopen(req, timeout=30)).get("hits", [])
        good = [h for h in hits
                if not PHOTO_BAD_TAGS & {t.strip() for t in h.get("tags", "").lower().split(",")}
                and (not patterns or any(p.search(h.get("tags", "").lower()) for p in patterns))]
        fresh = [h for h in good if h["id"] not in used_ids]
        seen += good
        if fresh:
            return _download(random.choice(fresh[:40]), ua)
    if seen:  # новые закончились — повторяем
        return _download(random.choice(seen), ua)
    raise RuntimeError(f"Pixabay не нашёл фото: {', '.join(queries)}")


def _download(hit, ua):
    req = urllib.request.Request(hit["largeImageURL"], headers=ua)
    data = urllib.request.urlopen(req, timeout=60).read()
    return Image.open(io.BytesIO(data)), hit["id"]


def photo_background(photo, w, h):
    """Фото на весь кадр (обрезка по центру) + затемнение слева и снизу, где текст."""
    from PIL import ImageOps
    img = ImageOps.fit(photo.convert("RGB"), (w, h), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(w * 0.002))
    shade = Image.new("L", (256, 256))
    for x in range(256):
        for y in range(256):
            left = 1 - x / 255
            bottom = max(0, (y / 255 - 0.5) / 0.5)
            shade.putpixel((x, y), min(235, int(80 + 130 * left ** 1.4 + 70 * bottom ** 1.5)))
    dark = Image.new("RGB", (w, h), (8, 10, 28))
    return Image.composite(dark, img, shade.resize((w, h), Image.BICUBIC))


def plane(size, color):
    """Силуэт самолёта носом вниз."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c, u = size / 2, size / 100
    d.rounded_rectangle([c - 6 * u, 8 * u, c + 6 * u, 90 * u], radius=6 * u, fill=color)
    d.ellipse([c - 6 * u, 3 * u, c + 6 * u, 19 * u], fill=color)
    d.polygon([(c - 5 * u, 36 * u), (c - 48 * u, 58 * u), (c - 48 * u, 65 * u), (c - 5 * u, 55 * u),
               (c + 5 * u, 55 * u), (c + 48 * u, 65 * u), (c + 48 * u, 58 * u), (c + 5 * u, 36 * u)],
              fill=color)
    d.polygon([(c - 4 * u, 78 * u), (c - 20 * u, 90 * u), (c - 20 * u, 96 * u), (c, 89 * u),
               (c + 20 * u, 96 * u), (c + 20 * u, 90 * u), (c + 4 * u, 78 * u)], fill=color)
    return im.rotate(180)


def font(path, size, weight):
    f = ImageFont.truetype(str(path), int(size * SCALE))
    f.set_variation_by_name(weight)
    return f


def fit(draw, text, path, weight, max_w, size, min_size=40):
    while size > min_size:
        f = font(path, size, weight)
        if draw.textlength(text, font=f) <= max_w * SCALE:
            return f
        size -= 2
    return font(path, min_size, weight)


def make_card(origin, destination, origin_code, dest_code, price, date, details, handle,
              price_note="в одну сторону", sky_key=None, photo=None, sky_name=None):
    """Все аргументы — готовые строки. sky_key — код города, по которому выбирается небо
    (обычно китайский город). Возвращает JPEG-байты."""
    s = SCALE
    key = sky_key or dest_code
    if photo is not None:
        t = THEMES[True]
        img = photo_background(photo, W * s, H * s)
    else:
        sky = SKIES[sky_name] if sky_name else sky_for(key)
        t = dict(THEMES[sky["dark"]])
        if sky.get("accent"):
            t["accent"] = sky["accent"]
        img = sky_image(W * s, H * s, sky, seed=key)   # RGB: полупрозрачное смешивается с фоном
    d = ImageDraw.Draw(img, "RGBA")
    pad = 80

    # верхняя строка: коды аэропортов и канал
    small = font(BODY, 26, "Bold")
    d.text((pad * s, 64 * s), f"{origin_code}  →  {dest_code}", font=small, fill=t["muted"])
    d.text(((W - pad) * s, 64 * s), handle, font=small, fill=t["muted"], anchor="ra")

    # маршрут: точка — пунктир — самолётик — пунктир — точка
    x_line = pad + 14
    y1, y2 = 205, 375
    ym = (y1 + y2) / 2
    ps = 46
    for a, b in ((y1, ym - ps / 2 - 6), (ym + ps / 2 + 6, y2)):
        y = a + 18
        while y < b - 10:
            d.line([(x_line * s, y * s), (x_line * s, min(y + 9, b - 10) * s)], fill=t["line"], width=4 * s)
            y += 17
    pl = plane(ps * s, t["ink"])
    img.paste(pl, (int((x_line - ps / 2) * s), int((ym - ps / 2) * s)), pl)
    r = 13
    d.ellipse([(x_line - r) * s, (y1 - r) * s, (x_line + r) * s, (y1 + r) * s],
              outline=t["ink"], width=5 * s, fill=t["dot"])
    d.ellipse([(x_line - r) * s, (y2 - r) * s, (x_line + r) * s, (y2 + r) * s], fill=t["accent"])

    tx = x_line + 50
    max_w = W - tx - pad
    f_city = min((fit(d, c, HEAD, "SemiBold", max_w, 92) for c in (origin, destination)),
                 key=lambda f: f.size)
    d.text((tx * s, y1 * s), origin, font=f_city, fill=t["ink"], anchor="lm")
    d.text((tx * s, y2 * s), destination, font=f_city, fill=t["accent"], anchor="lm")

    d.line([(pad * s, 500 * s), ((W - pad) * s, 500 * s)], fill=t["line"], width=2 * s)

    d.text((pad * s, 560 * s), date, font=font(BODY, 40, "Bold"), fill=t["ink"], anchor="lm")
    d.text((pad * s, 616 * s), details, font=font(BODY, 28, "SemiBold"), fill=t["muted"], anchor="lm")
    d.text(((W - pad) * s, 548 * s), price_note, font=font(BODY, 28, "SemiBold"), fill=t["muted"], anchor="rm")
    d.text(((W - pad) * s, 610 * s), price, font=font(HEAD, 64, "SemiBold"), fill=t["ink"], anchor="rm")

    out = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()
