"""
Tel Aviv Apartment Scraper
==========================
Парсит объявления о квартирах и сохраняет в apartments.json.
Поддерживает одиночные URL и страницы поиска (автопарсинг всех объявлений).

Использование:
  python scraper.py "https://www.yad2.co.il/realestate/item/tel-aviv-area/xxxxx"
  python scraper.py "https://www.yad2.co.il/realestate/rent?city=5000&rooms=3-3"
  python scraper.py --file urls.txt
  python scraper.py --list
"""

import sys
import json
import hashlib
import argparse
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Зависимости не установлены. Запусти:")
    print("   pip install requests beautifulsoup4 playwright")
    print("   python3 -m playwright install chromium")
    sys.exit(1)

# ─── Настройки ────────────────────────────────────────────────────────────────

DATA_FILE = Path("apartments.json")

CRITERIA = {
    "rooms": 3,
    "mamad_required": True,
    "furnished_required": True,  # unfurnished → skip
    "price_good": 10000,
    "price_max": 12000,
}

# Разрешённые районы: Тель-Авив и ближайшие (английский и иврит, в нижнем регистре)
ALLOWED_AREAS = [
    "tel aviv", "tel-aviv", "tel aviv-yafo", "tel aviv yafo",
    "ramat aviv", "neve tzedek", "florentin", "lev tel aviv",
    "north tel aviv", "south tel aviv",
    "תל אביב", "תל-אביב", "נווה צדק", "פלורנטין", "לב תל אביב", "רמת אביב",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Ключевые слова для поиска характеристик в тексте страницы
KEYWORDS = {
    "mamad":   ['ממ"ד', 'ממד', 'mamad', 'מרחב מוגן', 'safe room', 'bomb shelter', 'security room'],
    "parking": ['חניה', 'חנייה', 'parking', 'חניון'],
    "gym":       ['חדר כושר', 'gym', 'כושר בבניין'],
    "bathtub":   ['אמבטיה', 'bathtub', 'bath tub'],
    "furnished": ['ריהוט', 'מרוהט', 'מרוהטת', 'כולל ריהוט', 'furnished'],
}

# ─── Работа с данными ─────────────────────────────────────────────────────────

def load_apartments():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_apartments(apartments):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(apartments, f, ensure_ascii=False, indent=2)
    print(f"💾 Сохранено в {DATA_FILE} ({len(apartments)} квартир)")


def make_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]


def find_existing(apartments, url):
    apt_id = make_id(url)
    for i, apt in enumerate(apartments):
        if apt.get("id") == apt_id or apt.get("url") == url:
            return i
    return None


# ─── Статус квартиры ──────────────────────────────────────────────────────────

def is_allowed_area(address):
    """Возвращает True если адрес относится к Тель-Авиву или ближайшим районам."""
    if not address:
        return False
    addr_lower = address.lower()
    return any(area in addr_lower for area in ALLOWED_AREAS)


def calculate_status(apt):
    price = apt.get("price")
    rooms = apt.get("rooms")
    mamad = apt.get("mamad")

    if price is None:
        return "new"
    if price > CRITERIA["price_max"]:
        return "skip"
    if rooms != CRITERIA["rooms"]:
        return "skip"
    if CRITERIA["mamad_required"] and not mamad:
        return "skip"
    if CRITERIA["furnished_required"] and not apt.get("furnished"):
        return "skip"
    if not is_allowed_area(apt.get("address", "")):
        return "skip"
    return "good" if price <= CRITERIA["price_good"] else "over_budget"


# ─── Английский адрес через Nominatim ────────────────────────────────────────

def get_english_address(lat, lng):
    """Reverse-геокодинг через Nominatim для получения адреса на английском."""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "json", "accept-language": "en"},
            headers={"User-Agent": "TelAvivApartmentFinder/1.0 (personal-use)"},
            timeout=10,
        )
        data = resp.json()
        addr = data.get("address", {})
        road  = addr.get("road") or addr.get("pedestrian") or addr.get("path") or ""
        house = str(addr.get("house_number", "")).strip()
        sub   = addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or ""
        city  = addr.get("city") or addr.get("town") or "Tel Aviv-Yafo"
        parts = [f"{road} {house}".strip(), sub, city]
        return ", ".join(p for p in parts if p)
    except Exception:
        return None


# ─── Загрузка страниц ─────────────────────────────────────────────────────────

def fetch_with_playwright(url):
    """
    Загружает страницу через headless Chromium, обходит bot-защиту.
    Возвращает (soup, next_data, page_text) или (None, None, '') при ошибке.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright не установлен.")
        print("   pip install playwright && python3 -m playwright install chromium")
        return None, None, ""

    try:
        print(f"🌐 Загружаю через браузер: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"], locale="he-IL")
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            time.sleep(2)

            html      = page.content()
            page_text = page.evaluate("() => document.body?.innerText || ''")

            next_data = None
            nd_raw = page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
            )
            if nd_raw:
                try:
                    next_data = json.loads(nd_raw)
                except json.JSONDecodeError:
                    pass

            browser.close()
        return BeautifulSoup(html, "html.parser"), next_data, page_text

    except Exception as e:
        print(f"❌ Ошибка Playwright: {e}")
        return None, None, ""


def fetch_craigslist_search(url):
    """
    Загружает страницу поиска Craigslist через Playwright.
    Пробует domcontentloaded (быстрее), потом load как fallback.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None, ""
    for wait_until, timeout in [("domcontentloaded", 30000), ("load", 45000)]:
        try:
            print(f"🌐 Загружаю ({wait_until}): {url}")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx  = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-US")
                page = ctx.new_page()
                page.goto(url, wait_until=wait_until, timeout=timeout)
                time.sleep(4)
                html      = page.content()
                page_text = page.evaluate("() => document.body?.innerText || ''")
                browser.close()
            soup = BeautifulSoup(html, "html.parser")
            # Проверяем — есть ли объявления на странице
            if soup.find("a", href=re.compile(r'craigslist\.org/[^/]+/d/')):
                return soup, None, page_text
            print(f"   ⚠️  Объявления не найдены при {wait_until}, пробую следующий вариант...")
        except Exception as e:
            print(f"   ⚠️  {wait_until} timeout: {e}")
    print("❌ Не удалось загрузить страницу поиска Craigslist")
    return None, None, ""


def fetch_page(url):
    """
    Загружает страницу подходящим методом:
      yad2/madlan → Playwright (bot-защита)
      остальные   → requests
    Возвращает (soup, next_data, page_text).
    """
    if any(d in url for d in ["yad2.co.il", "madlan.co.il", "homeless.co.il"]):
        return fetch_with_playwright(url)
    # Craigslist search pages нужен networkidle для рендера списка
    if "craigslist.org" in url and "/search/" in url:
        return fetch_craigslist_search(url)

    try:
        print(f"🌐 Загружаю: {url}")
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup, None, soup.get_text()
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code
        print(f"❌ HTTP {code}: {url}")
        if code == 403:
            print("   Сайт блокирует запросы — попробуй Playwright.")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    return None, None, ""


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def text_has(text, keywords):
    """Проверяет, встречается ли хотя бы одно ключевое слово в тексте."""
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def extract_number(text):
    if not text:
        return None
    s = str(text).replace(",", "").replace(" ", "").replace(" ", "")
    m = re.search(r'\d+(?:\.\d+)?', s)
    return float(m.group()) if m else None


# ─── Парсер yad2.co.il ────────────────────────────────────────────────────────

def parse_yad2_json(next_data, url):
    """
    Парсит данные yad2 из __NEXT_DATA__ JSON (Next.js SSR).

    Структура: props.pageProps.dehydratedState.queries[0].state.data

    Поля:
      price       ← listing.price
      rooms       ← listing.additionalDetails.roomsCount
      bedrooms    ← roomsCount - 1 (в Israeli-формате: 3 комнаты = 2 спальни)
      floor       ← listing.address.house.floor
      size_sqm    ← listing.additionalDetails.squareMeter
      mamad       ← listing.inProperty.includeSecurityRoom
      parking     ← listing.inProperty.includeParking
      gym/bathtub ← поиск в searchText + description
      lat/lng     ← listing.address.coords.lat/lon
      photos      ← listing.metaData.images[]
      address     ← Nominatim reverse-geocode (английский)
    """
    try:
        queries = (next_data
                   .get("props", {})
                   .get("pageProps", {})
                   .get("dehydratedState", {})
                   .get("queries", []))
        listing = queries[0]["state"]["data"] if queries else {}
        if not listing:
            return {}

        addr    = listing.get("address", {})
        details = listing.get("additionalDetails", {})
        in_prop = listing.get("inProperty", {})
        coords  = addr.get("coords", {})
        meta    = listing.get("metaData", {})

        lat = coords.get("lat")
        lng = coords.get("lon")

        # Английский адрес через Nominatim
        address = None
        if lat and lng:
            address = get_english_address(lat, lng)
            time.sleep(1)  # rate-limit Nominatim

        # Fallback: иврит
        if not address:
            street    = (addr.get("street") or {}).get("text", "")
            house_num = str((addr.get("house") or {}).get("number", "")).strip()
            neigh     = (addr.get("neighborhood") or {}).get("text", "")
            city      = (addr.get("city") or {}).get("text", "Tel Aviv-Yafo")
            address   = ", ".join(p for p in [f"{street} {house_num}".strip(), neigh, city] if p)

        corpus = listing.get("searchText", "") + " " + meta.get("description", "")
        rooms  = details.get("roomsCount")

        # includeFurniture — прямое поле; текстовый fallback на случай отсутствия
        furnished = in_prop.get("includeFurniture")
        if furnished is None:
            furnished = text_has(corpus, KEYWORDS["furnished"])

        return {
            "price":     listing.get("price"),
            "rooms":     rooms,
            "bedrooms":  max(0, int(rooms) - 1) if rooms else None,
            "floor":     (addr.get("house") or {}).get("floor"),
            "size_sqm":  details.get("squareMeter"),
            "mamad":     in_prop.get("includeSecurityRoom", False),
            "parking":   in_prop.get("includeParking", False),
            "furnished": furnished,
            "gym":       text_has(corpus, KEYWORDS["gym"]),
            "bathtub":   text_has(corpus, KEYWORDS["bathtub"]),
            "lat":      lat,
            "lng":      lng,
            "address":  address or "Address not found",
            "photos":   meta.get("images", []),
            "source":   "yad2",
        }
    except Exception as e:
        print(f"⚠️  Ошибка парсинга yad2 JSON: {e}")
        return {}


def parse_yad2_html(soup, url):
    """Резервный HTML-парсер для yad2 (если __NEXT_DATA__ недоступен)."""
    apt = {}
    page_text = soup.get_text()

    for sel in ["[data-testid='price']", ".price", "[class*='price']"]:
        el = soup.select_one(sel)
        if el:
            price = extract_number(el.get_text(strip=True))
            if price:
                apt["price"] = price
                break

    for sel in ["[data-testid='address']", "[class*='address']", "h1"]:
        el = soup.select_one(sel)
        if el:
            apt["address"] = el.get_text(strip=True)
            break

    apt["mamad"]     = text_has(page_text, KEYWORDS["mamad"])
    apt["parking"]   = text_has(page_text, KEYWORDS["parking"])
    apt["gym"]       = text_has(page_text, KEYWORDS["gym"])
    apt["bathtub"]   = text_has(page_text, KEYWORDS["bathtub"])
    apt["furnished"] = text_has(page_text, KEYWORDS["furnished"])
    apt["photos"]    = []
    apt["source"]    = "yad2"
    return apt


def parse_yad2(soup, url, next_data=None, page_text=""):
    """Главный парсер yad2: __NEXT_DATA__ → HTML fallback."""
    if next_data:
        result = parse_yad2_json(next_data, url)
        if result.get("price") or result.get("address"):
            return result
    print("⚠️  __NEXT_DATA__ не найден, пробую HTML-парсер...")
    return parse_yad2_html(soup, url)


# ─── Парсер homeless.co.il ────────────────────────────────────────────────────

def parse_homeless(soup, url, next_data=None, page_text=""):
    """
    Парсер homeless.co.il.
    Сайт — ASP.NET, сервер-рендеринг, текстовые блоки характеристик.

    Структура страницы объявления:
      h1              → адрес: "דירה X חדרים להשכרה ב{city}, {street}"
      текст страницы  → ключевые слова: ממד / חניה / חדר כושר / אמבטיה
      "קומה: X"        → этаж
      'מ"ר: X'         → площадь
      "9,000 ₪"        → цена
      img[src*=uploads.homeless.co.il/rent] → фото
    """
    apt = {"source": "homeless", "photos": []}
    if not page_text:
        page_text = soup.get_text()

    # Адрес из h1
    h1 = soup.find("h1")
    if h1:
        apt["address"] = h1.get_text(strip=True)

    # Цена
    price_m = re.search(r'([\d,]+)\s*[₪]', page_text)
    if not price_m:
        price_m = re.search(r'([\d,]+)\s*ש["״]ח', page_text)
    if price_m:
        price = extract_number(price_m.group(1))
        if price and 1000 < price < 50000:
            apt["price"] = price

    # Комнаты из h1 или текста
    room_m = re.search(r'(\d+(?:\.\d+)?)\s*חדר', page_text)
    if room_m:
        rooms = float(room_m.group(1))
        apt["rooms"]    = int(rooms) if rooms == int(rooms) else rooms
        apt["bedrooms"] = max(0, int(apt["rooms"]) - 1)

    # Этаж
    floor_m = re.search(r'קומה[:\s]+(\d+)', page_text)
    if floor_m:
        apt["floor"] = int(floor_m.group(1))

    # Площадь
    size_m = re.search(r'מ["״״]ר[:\s]+(\d+)', page_text)
    if not size_m:
        size_m = re.search(r'(\d{2,4})\s*מ["״״]ר', page_text)
    if size_m:
        apt["size_sqm"] = int(size_m.group(1))

    # Характеристики по ключевым словам
    apt["mamad"]     = text_has(page_text, KEYWORDS["mamad"])
    apt["parking"]   = text_has(page_text, KEYWORDS["parking"])
    apt["gym"]       = text_has(page_text, KEYWORDS["gym"])
    apt["bathtub"]   = text_has(page_text, KEYWORDS["bathtub"])
    apt["furnished"] = text_has(page_text, KEYWORDS["furnished"])

    # Фото
    seen   = set()
    hi_res = []
    lo_res = []
    for img in soup.find_all("img", src=re.compile(r'uploads\.homeless\.co\.il/rent')):
        src = img.get("src", "")
        if not src or src in seen:
            continue
        seen.add(src)
        if "/1200/" in src:
            hi_res.append(src)
        else:
            lo_res.append(src)
    apt["photos"] = hi_res + lo_res

    return apt


# ─── Парсер madlan.co.il ──────────────────────────────────────────────────────

def parse_madlan(soup, url, next_data=None, page_text=""):
    """
    Парсер madlan.co.il.
    Пробует __NEXT_DATA__, затем текстовый парсинг.
    """
    apt = {"source": "madlan", "photos": []}
    if not page_text:
        page_text = soup.get_text()

    # Попытка из __NEXT_DATA__
    if next_data:
        try:
            props   = next_data.get("props", {}).get("pageProps", {})
            listing = props.get("listing") or props.get("data") or {}
            if listing:
                apt["price"]   = listing.get("price") or listing.get("monthlyRent")
                apt["rooms"]   = listing.get("rooms")
                apt["size_sqm"] = listing.get("size") or listing.get("squareMeter")
                apt["floor"]   = listing.get("floor")
                if apt.get("rooms"):
                    apt["bedrooms"] = max(0, int(apt["rooms"]) - 1)
        except Exception:
            pass

    # Fallback: текст
    if not apt.get("price"):
        m = re.search(r'([\d,]+)\s*[₪]', page_text)
        if m:
            apt["price"] = extract_number(m.group(1))

    if not apt.get("rooms"):
        m = re.search(r'(\d+(?:\.\d+)?)\s*חדר', page_text)
        if m:
            rooms = float(m.group(1))
            apt["rooms"]    = int(rooms) if rooms == int(rooms) else rooms
            apt["bedrooms"] = max(0, int(apt["rooms"]) - 1)

    h1    = soup.find("h1")
    title = soup.find("title")
    apt["address"] = (h1 or title).get_text(strip=True)[:200] if (h1 or title) else "Address not found"

    apt["mamad"]     = text_has(page_text, KEYWORDS["mamad"])
    apt["parking"]   = text_has(page_text, KEYWORDS["parking"])
    apt["gym"]       = text_has(page_text, KEYWORDS["gym"])
    apt["bathtub"]   = text_has(page_text, KEYWORDS["bathtub"])

    # Furniture: из __NEXT_DATA__ или по ключевым словам
    furn = None
    if next_data:
        try:
            props   = next_data.get("props", {}).get("pageProps", {})
            listing = props.get("listing") or props.get("data") or {}
            furn = listing.get("furnished") or listing.get("includeFurniture") or listing.get("furniture")
            if furn is not None:
                furn = bool(furn)
        except Exception:
            pass
    if furn is None:
        furn = text_has(page_text, KEYWORDS["furnished"])
    apt["furnished"] = furn

    return apt


# ─── Универсальный парсер ─────────────────────────────────────────────────────

def parse_generic(soup, url, next_data=None, page_text=""):
    apt = {}
    if not page_text:
        page_text = soup.get_text()

    for pat in [r'([\d,]+)\s*[₪]', r'[₪]\s*([\d,]+)', r'([\d,]+)\s*ils']:
        m = re.search(pat, page_text.lower())
        if m:
            price = extract_number(m.group(1))
            if price and 1000 < price < 50000:
                apt["price"] = price
                break

    h1 = soup.find("h1")
    apt["address"] = h1.get_text(strip=True)[:200] if h1 else "Address not found"

    apt["mamad"]     = text_has(page_text, KEYWORDS["mamad"])
    apt["parking"]   = text_has(page_text, KEYWORDS["parking"])
    apt["gym"]       = text_has(page_text, KEYWORDS["gym"])
    apt["bathtub"]   = text_has(page_text, KEYWORDS["bathtub"])
    apt["furnished"] = text_has(page_text, KEYWORDS["furnished"])

    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:חדרים|חדר|rooms?)', page_text.lower())
    if m:
        rooms = float(m.group(1))
        apt["rooms"]    = int(rooms) if rooms == int(rooms) else rooms
        apt["bedrooms"] = max(0, int(apt["rooms"]) - 1)

    apt["photos"] = []
    apt["source"] = "generic"
    return apt


# ─── Парсер onmap.co.il (REST API) ───────────────────────────────────────────

ONMAP_SEARCH_API = "https://phoenix.onmap.co.il/v1/properties/mixed_search"
ONMAP_API_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Referer":    "https://www.onmap.co.il/",
    "Origin":     "https://www.onmap.co.il",
}


def parse_onmap_listing(listing, detail):
    """Парсит данные одного объявления из JSON onmap API."""
    ai      = detail.get("additional_info", {}) or {}
    addr    = detail.get("address", {}) or {}
    addr_en = addr.get("en", {}) or {}
    loc     = addr.get("location", {}) or {}

    street  = addr_en.get("street_name", "") or ""
    house   = str(addr_en.get("house_number", "") or "").strip()
    neigh   = addr_en.get("neighborhood", "") or ""
    city    = addr_en.get("city_name", "") or ""
    address = ", ".join(p for p in [f"{street} {house}".strip(), neigh, city] if p)

    parking = ai.get("parking", {}) or {}
    has_parking = (parking.get("aboveground", "none") not in ("none", None, "") or
                   parking.get("underground", "none") not in ("none", None, ""))

    photos = [img["gallery"] for img in (detail.get("images") or []) if img.get("gallery")]

    corpus = (str(detail.get("description", "") or "") + " " +
              " ".join(detail.get("commodities", []) or []))

    rooms = (ai.get("rooms") or
             (listing.get("additional_info", {}) or {}).get("rooms"))

    return {
        "price":     detail.get("price") or listing.get("price"),
        "rooms":     rooms,
        "bedrooms":  max(0, int(rooms) - 1) if rooms else None,
        "floor":     (ai.get("floor") or {}).get("on_the"),
        "size_sqm":  (ai.get("area") or {}).get("base"),
        "mamad":     text_has(corpus, KEYWORDS["mamad"]),
        "parking":   has_parking,
        "gym":       text_has(corpus, KEYWORDS["gym"]),
        "bathtub":   text_has(corpus, KEYWORDS["bathtub"]),
        "furnished": text_has(corpus, KEYWORDS["furnished"]),
        "lat":       loc.get("lat"),
        "lng":       loc.get("lon"),
        "address":   address or "Address not found",
        "photos":    photos,
        "source":    "onmap",
    }


def process_onmap_search_api(url, apartments):
    """
    Парсит поисковую страницу onmap через прямые запросы к phoenix.onmap.co.il API.
    Не использует Playwright — данные возвращаются напрямую в JSON.
    """
    print(f"🔍 onmap.co.il (API): {url}")

    # Извлекаем параметры из URL вида /price_0-12000/rooms_3/
    price_m   = re.search(r'price_\d+-(\d+)', url)
    rooms_m   = re.search(r'rooms_(\d+)', url)
    max_price = int(price_m.group(1)) if price_m else CRITERIA["price_max"]
    rooms_filter = int(rooms_m.group(1)) if rooms_m else CRITERIA["rooms"]

    base_params = {
        "option":     "rent,rent-short",
        "section":    "residence",
        "max":        max_price,
        "rooms[]":    rooms_filter,
        "is_mobile":  "false",
        "$sort":      "-search_date",
        "$limit":     50,
        "country":    "Israel",
    }

    # Собираем все тель-авивские объявления постранично
    all_listings = []
    skip = 0
    while True:
        try:
            r = requests.get(ONMAP_SEARCH_API, params={**base_params, "$skip": skip},
                             headers=ONMAP_API_HEADERS, timeout=15)
            r.raise_for_status()
            page_data = r.json().get("data", [])
        except Exception as e:
            print(f"❌ API ошибка: {e}")
            break
        if not page_data:
            break
        tav = [l for l in page_data
               if "tel aviv" in (l.get("address", {}).get("en", {}).get("city_name", "") or "").lower()]
        all_listings.extend(tav)
        print(f"   стр.{skip//50+1}: {len(page_data)} результатов, {len(tav)} Тель-Авив")
        if len(page_data) < 50:
            break
        skip += 50
        time.sleep(0.5)

    if not all_listings:
        print("⚠️  Объявления в Тель-Авиве не найдены")
        return 0

    print(f"📋 Найдено {len(all_listings)} объявлений в Тель-Авиве")

    processed = 0
    for i, listing in enumerate(all_listings, 1):
        lid     = listing.get("id")
        slug    = listing.get("slug", lid)
        apt_url = f"https://www.onmap.co.il/en/listing/{slug}"
        print(f"\n[{i}/{len(all_listings)}] ────────────────────────────")

        # Получаем детали объявления
        try:
            dr = requests.get(f"https://phoenix.onmap.co.il/v1/properties/{lid}",
                              headers=ONMAP_API_HEADERS, timeout=15)
            dr.raise_for_status()
            detail = dr.json()
        except Exception as e:
            print(f"❌ Detail API: {e}")
            time.sleep(0.5)
            continue

        apt_data = parse_onmap_listing(listing, detail)
        apt = {
            "id":        make_id(apt_url),
            "url":       apt_url,
            "source":    "onmap",
            "address":   apt_data.get("address", "Address not found"),
            "lat":       apt_data.get("lat"),
            "lng":       apt_data.get("lng"),
            "rooms":     apt_data.get("rooms"),
            "bedrooms":  apt_data.get("bedrooms"),
            "price":     apt_data.get("price"),
            "mamad":     apt_data.get("mamad", False),
            "parking":   apt_data.get("parking", False),
            "gym":       apt_data.get("gym", False),
            "bathtub":   apt_data.get("bathtub", False),
            "furnished": apt_data.get("furnished", False),
            "floor":     apt_data.get("floor"),
            "size_sqm":  apt_data.get("size_sqm"),
            "photos":    apt_data.get("photos", []),
            "notes":     "",
            "status":    "new",
            "added_at":  datetime.now().isoformat(),
        }

        apt["status"] = calculate_status(apt)

        if apt["status"] == "skip":
            reasons = []
            if apt.get("rooms") != CRITERIA["rooms"]:
                reasons.append(f"комнаты={apt['rooms']}")
            if apt.get("price") and apt["price"] > CRITERIA["price_max"]:
                reasons.append(f"цена={apt['price']}")
            if CRITERIA["mamad_required"] and not apt.get("mamad"):
                reasons.append("нет mamad")
            if CRITERIA["furnished_required"] and not apt.get("furnished"):
                reasons.append("без мебели")
            if not is_allowed_area(apt.get("address", "")):
                reasons.append(f"чужой район ({apt.get('address','?')[:40]})")
            print(f"⏭️  Пропускаю: {', '.join(reasons) or 'не проходит фильтр'}")
            time.sleep(0.5)
            continue

        existing_idx = find_existing(apartments, apt_url)
        if existing_idx is not None:
            print("🔄 Обновляю существующую запись")
            old = apartments[existing_idx]
            if old.get("status") in ("interested", "rejected", "contacted"):
                apt["status"] = old["status"]
            apt["notes"] = old.get("notes", "")
            apartments[existing_idx] = apt
        else:
            apartments.append(apt)
            print("✅ Добавлена новая квартира")

        emoji     = {"good": "🟢", "over_budget": "🟡", "new": "🔵"}.get(apt["status"], "⚪")
        price_str = f"{int(apt['price']):,} ILS" if apt["price"] else "не найдена"
        print(f"\n{emoji} {apt['address']}")
        print(f"   Цена: {price_str}  Комнаты: {apt['rooms']}  Площадь: {apt['size_sqm']} м²  Этаж: {apt['floor']}")
        print(f"   Mamad: {'✓' if apt['mamad'] else '✗'}  Parking: {'✓' if apt['parking'] else '✗'}  Furnished: {'✓' if apt['furnished'] else '✗'}  Фото: {len(apt['photos'])}")

        processed += 1
        time.sleep(0.5)

    return processed


# ─── Парсер craigslist.org ────────────────────────────────────────────────────

def parse_craigslist(soup, url, next_data=None, page_text=""):
    """
    Парсит объявление с telaviv.craigslist.org.
    Данные берутся из JSON-LD, атрибутов объявления и текста описания.
    """
    if not page_text:
        page_text = soup.get_text()

    # JSON-LD структура (<script id="ld_posting_data">)
    ld_el   = soup.find('script', id='ld_posting_data')
    ld_data = json.loads(ld_el.string) if ld_el and ld_el.string else {}

    # Атрибуты объявления: ['2BR / 1Ba', '80m2', 'furnished', 'no parking', ...]
    attrs      = [a.get_text(strip=True) for a in soup.select('.attrgroup span') if a.get_text(strip=True)]
    attrs_low  = [a.lower() for a in attrs]
    attrs_str  = " ".join(attrs_low)

    # Пропускаем краткосрочную аренду (daily/weekly)
    if "daily" in attrs_str or "weekly" in attrs_str:
        return {"_skip": "short-term"}

    # Цена
    price_el = soup.select_one('.price')
    price_str = (price_el.get_text(strip=True) if price_el else '') or ''
    price = extract_number(re.sub(r'[₪$,\s]', '', price_str))
    # Если цена в USD — конвертируем (~3.7 ILS/USD)
    if price and '$' in price_str:
        price = round(price * 3.7)

    # Комнаты: bedrooms из JSON-LD, Israeli rooms = bedrooms + 1
    bedrooms_raw = ld_data.get('numberOfBedrooms')
    try:
        bedrooms = int(bedrooms_raw) if bedrooms_raw is not None else None
    except (ValueError, TypeError):
        bedrooms = None
    # Fallback: из атрибута "2BR / 1Ba"
    if bedrooms is None:
        br_m = re.search(r'(\d+)\s*br', attrs_str)
        if br_m:
            bedrooms = int(br_m.group(1))
    rooms = bedrooms + 1 if bedrooms is not None else None

    # Площадь из атрибутов ("80m2")
    size_sqm = None
    for a in attrs:
        m = re.search(r'(\d+)\s*m2', a.lower())
        if m:
            size_sqm = int(m.group(1))

    # Parking: есть "parking"/"garage"/"carport" но нет "no parking"
    has_parking = (('parking' in attrs_str or 'garage' in attrs_str or 'carport' in attrs_str)
                   and 'no parking' not in attrs_str)

    # Furnished: прямо в атрибутах
    furnished = 'furnished' in attrs_low

    # Координаты из JSON-LD (не всегда есть)
    lat = ld_data.get('latitude')
    lng = ld_data.get('longitude')

    # Заголовок
    title_el = soup.select_one('#titletextonly')
    title    = title_el.get_text(strip=True) if title_el else ld_data.get('name', '')

    # Фильтруем объявления из других городов по заголовку
    NON_TA = ["eilat", "jerusalem", "haifa", "netanya", "beer sheba", "beersheba",
              "ashdod", "ashkelon", "rishon", "petah tikva", "holon", "nazareth",
              "bnei brak", "kfar saba", "ra'anana", "raanana", "kiryat"]
    if any(c in title.lower() for c in NON_TA):
        return {"_skip": f"не Тель-Авив: {title[:40]}"}

    # Адрес: из mapaddress; иначе "Tel Aviv, Israel" (все telaviv.craigslist.org → ТА)
    mapaddr = soup.select_one('.mapaddress')
    address = (mapaddr.get_text(strip=True) if mapaddr and mapaddr.get_text(strip=True)
               else "Tel Aviv, Israel")

    # Фото из og:image
    og_img = soup.find('meta', property='og:image')
    photos  = [og_img['content']] if (og_img and og_img.get('content')) else []

    corpus = page_text + ' ' + title

    return {
        "price":     price,
        "rooms":     rooms,
        "bedrooms":  bedrooms,
        "floor":     None,
        "size_sqm":  size_sqm,
        "mamad":     text_has(corpus, KEYWORDS["mamad"]),
        "parking":   has_parking,
        "gym":       text_has(corpus, KEYWORDS["gym"]),
        "bathtub":   text_has(corpus, KEYWORDS["bathtub"]),
        "furnished": furnished,
        "lat":       float(lat) if lat else None,
        "lng":       float(lng) if lng else None,
        "address":   address,
        "photos":    photos,
        "source":    "craigslist",
    }


# ─── Парсер janglo.net ───────────────────────────────────────────────────────

def parse_janglo(soup, url, next_data=None, page_text=""):
    """
    Парсит объявление с janglo.net.
    Данные в .nicebreak span'ах: цена, комнаты, этаж, площадь, удобства.
    Адрес: .embed-responsive-1by1 div или title.
    """
    if not page_text:
        page_text = soup.get_text()

    # Структурированные поля — каждый .nicebreak содержит одно значение
    nicebreaks = [nb.get_text(strip=True) for nb in soup.find_all(class_="nicebreak")]

    price = None
    rooms = None
    floor = None
    size_sqm = None
    amenities = ""

    for nb in nicebreaks:
        if "NIS" in nb or "₪" in nb:
            price = extract_number(re.sub(r'[^\d]', '', nb.replace(",", "")))
        elif re.search(r'^\d+\s+Room', nb, re.I):
            m = re.search(r'(\d+)', nb)
            if m:
                rooms = int(m.group(1))
        elif re.search(r'^Floor\s+\d+', nb, re.I):
            m = re.search(r'(\d+)', nb)
            if m:
                floor = int(m.group(1))
        elif re.search(r'^\d+\s+m²', nb):
            m = re.search(r'(\d+)', nb)
            if m:
                size_sqm = int(m.group(1))
        elif len(nb) > 20 and any(w in nb for w in ["Parking", "Mamad", "Shelter", "AC", "Furnished"]):
            amenities = nb.lower()

    # Удобства также могут быть в тексте объявления
    corpus = amenities + " " + page_text.lower()

    # Основное описание — только параграфы тела объявления (не сайдбар)
    desc_paragraphs = " ".join(p.get_text(strip=True).lower()
                               for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40)
    main_corpus = amenities + " " + desc_paragraphs

    mamad    = "shelter room" in main_corpus or "mamad" in main_corpus
    parking  = "parking" in amenities
    furnished = "furnished" in amenities or "furnished" in desc_paragraphs

    # Адрес из location-блока или title
    addr_el = soup.find(class_=re.compile(r'embed-responsive-1by1'))
    if addr_el:
        address = addr_el.get_text(strip=True)
    else:
        title_el = soup.find(class_=re.compile(r'h2-responsive'))
        address = title_el.get_text(strip=True) if title_el else ""
    if not address:
        address = "Tel Aviv, Israel"

    # Проверяем, что это Тель-Авив
    if not is_allowed_area(address):
        return {"_skip": f"не Тель-Авив: {address[:60]}"}

    # Фото с images.janglo.net
    photos = list(dict.fromkeys(
        img["src"] for img in soup.find_all("img", src=re.compile(r'images\.janglo\.net/uploads'))
        if img.get("src")
    ))

    return {
        "price":    price,
        "rooms":    rooms,
        "floor":    floor,
        "size_sqm": size_sqm,
        "mamad":    mamad,
        "parking":  parking,
        "gym":      text_has(main_corpus, KEYWORDS["gym"]),
        "bathtub":  text_has(main_corpus, KEYWORDS["bathtub"]),
        "furnished": furnished,
        "lat":      None,
        "lng":      None,
        "address":  address,
        "photos":   photos,
        "source":   "janglo",
    }


# ─── Выбор парсера ────────────────────────────────────────────────────────────

def get_parser(url):
    if "yad2.co.il" in url:
        return parse_yad2
    if "homeless.co.il" in url:
        return parse_homeless
    if "madlan.co.il" in url:
        return parse_madlan
    if "craigslist.org" in url:
        return parse_craigslist
    if "janglo.net" in url:
        return parse_janglo
    return parse_generic


# ─── Автопарсинг страниц поиска ──────────────────────────────────────────────

def is_search_results_page(url):
    """Определяет, является ли URL страницей поиска (не отдельным объявлением)."""
    if "yad2.co.il" in url:
        return "/item/" not in url and any(p in url for p in ["/rent", "/sale", "/realestate"])
    if "homeless.co.il" in url:
        return "viewad" not in url and ("/rent/" in url or "/sale/" in url)
    if "madlan.co.il" in url:
        return "listing" not in url and any(p in url for p in ["for-rent", "for-sale"])
    if "onmap.co.il" in url:
        return "/search/" in url
    if "craigslist.org" in url:
        return "/search/" in url
    if "janglo.net" in url:
        return "/item/" not in url and "real-estate" in url
    return False


def extract_urls_yad2(next_data):
    """Извлекает URL объявлений из __NEXT_DATA__ страницы поиска yad2."""
    urls = []
    if not next_data:
        return urls

    AREA_MAP = {"tel_aviv": "tel-aviv-area", "TLV": "tel-aviv-area"}
    queries = (next_data.get("props", {})
               .get("pageProps", {})
               .get("dehydratedState", {})
               .get("queries", []))

    for q in queries:
        if "feed" not in str(q.get("queryKey", [])).lower():
            continue
        data = q.get("state", {}).get("data", {})
        if not isinstance(data, dict):
            continue
        for cat in ["private", "agency", "yad1", "platinum", "kingOfTheHar",
                    "trio", "booster", "leadingBroker"]:
            items = data.get(cat, [])
            if not isinstance(items, list):
                continue  # yad1 может быть dict с галереями, не списком объявлений
            for item in items:
                if not isinstance(item, dict):
                    continue
                token = item.get("token")
                if not token:
                    continue
                area_eng  = (item.get("address", {}).get("area", {}) or {}).get("textEng", "tel_aviv")
                area_slug = AREA_MAP.get(area_eng, "tel-aviv-area")
                urls.append(f"https://www.yad2.co.il/realestate/item/{area_slug}/{token}")

    return list(dict.fromkeys(urls))


def extract_urls_homeless(soup, page_text):
    """Извлекает URL объявлений со страницы поиска homeless.co.il."""
    urls = []
    seen = set()
    # Сначала пробуем из HTML-тегов (relative hrefs)
    if soup:
        for a in soup.find_all("a", href=re.compile(r'viewad,\d+')):
            href = a.get("href", "")
            m = re.search(r'viewad,(\d+)', href)
            if m:
                full = f"https://www.homeless.co.il/rent/viewad,{m.group(1)}.aspx"
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
    # Fallback: полные URL в тексте
    for u in re.findall(r'https?://(?:www\.)?homeless\.co\.il/(?:rent|sale)/viewad,\d+\.aspx', page_text):
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def extract_urls_madlan(soup, page_text):
    """Извлекает URL объявлений со страницы поиска madlan.co.il."""
    seen = set()
    urls = []
    if soup:
        for a in soup.find_all("a", href=re.compile(r'^/listing/')):
            href = a.get("href", "").split("?")[0]
            full = f"https://www.madlan.co.il{href}"
            if full not in seen:
                seen.add(full)
                urls.append(full)
    for u in re.findall(r'https?://www\.madlan\.co\.il/listing/[a-zA-Z0-9_/-]+', page_text):
        u = u.rstrip("/")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def extract_urls_craigslist(soup, page_text):
    """Извлекает URL объявлений со страницы поиска Craigslist."""
    seen, urls = set(), []
    if soup:
        for a in soup.find_all("a", href=re.compile(r'craigslist\.org/[^/]+/d/')):
            href = a.get("href", "").split("?")[0]
            if href and href not in seen:
                seen.add(href)
                urls.append(href)
    for u in re.findall(r'https://[a-z]+\.craigslist\.org/[^/]+/d/[^"\'<>\s]+\.html', page_text):
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def extract_urls_janglo(soup, page_text):
    """Извлекает URL объявлений со страницы поиска Janglo."""
    seen, urls = set(), []
    if soup:
        for a in soup.find_all("a", href=re.compile(r'^item/[A-Za-z0-9]+')):
            token = a["href"].split("/")[-1].split("?")[0]
            full = f"https://www.janglo.net/item/{token}"
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


def process_search_page(url, apartments):
    """
    Обрабатывает страницу поиска: извлекает все URL объявлений и парсит каждое.
    Возвращает количество добавленных/обновлённых квартир.
    """
    # onmap использует REST API — отдельный путь без Playwright
    if "onmap.co.il" in url:
        return process_onmap_search_api(url, apartments)

    print(f"🔍 Страница поиска: {url}")
    soup, next_data, page_text = fetch_page(url)

    if not soup and not page_text:
        print("❌ Не удалось загрузить страницу поиска")
        return 0

    if "yad2.co.il" in url:
        listing_urls = extract_urls_yad2(next_data)
    elif "homeless.co.il" in url:
        listing_urls = extract_urls_homeless(soup, page_text)
    elif "madlan.co.il" in url:
        listing_urls = extract_urls_madlan(soup, page_text)
    elif "craigslist.org" in url:
        listing_urls = extract_urls_craigslist(soup, page_text)
    elif "janglo.net" in url:
        listing_urls = extract_urls_janglo(soup, page_text)
    else:
        listing_urls = []

    if not listing_urls:
        print("⚠️  Объявления на странице поиска не найдены")
        return 0

    print(f"📋 Найдено {len(listing_urls)} объявлений на странице")

    processed = 0
    for i, listing_url in enumerate(listing_urls, 1):
        print(f"\n[{i}/{len(listing_urls)}] ────────────────────────────")
        result = process_url(listing_url, apartments)
        if result:
            processed += 1
        time.sleep(1.5)  # вежливая задержка

    return processed


# ─── Основная обработка URL ───────────────────────────────────────────────────

def process_url(url, apartments):
    """
    Парсит одно объявление, проверяет критерии, добавляет/обновляет в базе.
    Возвращает dict квартиры или None если нужно пропустить.
    """
    soup, next_data, page_text = fetch_page(url)
    if not soup:
        return None

    parser   = get_parser(url)
    apt_data = parser(soup, url, next_data, page_text)

    if apt_data.get("_skip"):
        print(f"⏭️  Пропускаю: {apt_data['_skip']}")
        return None
    if apt_data.get("price") is None and apt_data.get("rooms") is None:
        print(f"⏭️  Пропускаю: страница не распарсилась (нет цены и комнат)")
        return None

    apt = {
        "id":       make_id(url),
        "url":      url,
        "source":   apt_data.get("source", "unknown"),
        "address":  apt_data.get("address", "Address not found"),
        "lat":      apt_data.get("lat"),
        "lng":      apt_data.get("lng"),
        "rooms":    apt_data.get("rooms"),
        "bedrooms": apt_data.get("bedrooms"),
        "price":    apt_data.get("price"),
        "mamad":     apt_data.get("mamad", False),
        "parking":   apt_data.get("parking", False),
        "gym":       apt_data.get("gym", False),
        "bathtub":   apt_data.get("bathtub", False),
        "furnished": apt_data.get("furnished", False),
        "floor":     apt_data.get("floor"),
        "size_sqm": apt_data.get("size_sqm"),
        "photos":   apt_data.get("photos", []),
        "notes":    "",
        "status":   "new",
        "added_at": datetime.now().isoformat(),
    }

    apt["status"] = calculate_status(apt)

    if apt["status"] == "skip":
        reasons = []
        if apt.get("rooms") != CRITERIA["rooms"]:
            reasons.append(f"комнаты={apt['rooms']}")
        if apt.get("price") and apt["price"] > CRITERIA["price_max"]:
            reasons.append(f"цена={apt['price']}")
        if CRITERIA["mamad_required"] and not apt.get("mamad"):
            reasons.append("нет mamad")
        if CRITERIA["furnished_required"] and not apt.get("furnished"):
            reasons.append("без мебели")
        if not is_allowed_area(apt.get("address", "")):
            reasons.append(f"чужой район ({apt.get('address','?')[:40]})")
        print(f"⏭️  Пропускаю: {', '.join(reasons) or 'не проходит фильтр'}")
        return None

    existing_idx = find_existing(apartments, url)
    if existing_idx is not None:
        print("🔄 Обновляю существующую запись")
        old = apartments[existing_idx]
        if old.get("status") in ("interested", "rejected", "contacted"):
            apt["status"] = old["status"]
        apt["notes"] = old.get("notes", "")
        apartments[existing_idx] = apt
    else:
        apartments.append(apt)
        print("✅ Добавлена новая квартира")

    emoji     = {"good": "🟢", "over_budget": "🟡", "new": "🔵"}.get(apt["status"], "⚪")
    price_str = f"{int(apt['price']):,} ILS" if apt["price"] else "не найдена"
    print(f"\n{emoji} Квартира:")
    print(f"   Адрес:    {apt['address']}")
    print(f"   Комнаты:  {apt['rooms']}  (спален: {apt['bedrooms']})")
    print(f"   Цена:     {price_str}")
    print(f"   Mamad:     {'✓' if apt['mamad'] else '✗'}   Parking:   {'✓' if apt['parking'] else '✗'}")
    print(f"   Gym:       {'✓' if apt['gym'] else '✗'}   Bathtub:   {'✓' if apt['bathtub'] else '✗'}")
    print(f"   Furnished: {'✓' if apt['furnished'] else '✗'}")
    print(f"   Этаж:     {apt['floor']}   Площадь: {apt['size_sqm']} м²")
    if apt.get("lat"):
        print(f"   Координаты: {apt['lat']:.6f}, {apt['lng']:.6f}")
    print(f"   Фото:     {len(apt['photos'])}   Статус: {apt['status']}")

    return apt


# ─── Точка входа ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Парсер квартир для аренды в Тель-Авиве"
    )
    parser.add_argument("url", nargs="?",
                        help="URL объявления или страницы поиска")
    parser.add_argument("--file", "-f",
                        help="Файл со списком URL (по одному на строку)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="Показать все сохранённые квартиры")
    parser.add_argument("--refresh", "-r", action="store_true",
                        help="Перепарсить все квартиры из apartments.json; удалить unfurnished и недоступные")
    args = parser.parse_args()

    if args.refresh:
        apartments = load_apartments()
        if not apartments:
            print("📭 Нет квартир для обновления")
            return
        print(f"🔄 Обновляю {len(apartments)} квартир из apartments.json...\n")
        kept = []
        for i, existing in enumerate(apartments, 1):
            url = existing.get("url", "")
            print(f"\n[{i}/{len(apartments)}] {url}")
            # Сохраняем ручные статусы — их не трогаем при refresh
            manual_status = existing.get("status") if existing.get("status") in ("interested", "rejected", "contacted") else None
            soup, next_data, page_text = fetch_page(url)
            if not soup:
                print(f"❌ Не удалось загрузить — удаляю (недоступно)")
                continue
            parser_fn = get_parser(url)
            apt_data  = parser_fn(soup, url, next_data, page_text)
            furnished = apt_data.get("furnished", False)
            if not furnished:
                print(f"🚫 Без мебели — удаляю")
                continue
            # Обновляем поля, сохраняем ручной статус и заметки
            existing.update({
                "furnished": furnished,
                "mamad":     apt_data.get("mamad", existing.get("mamad", False)),
                "parking":   apt_data.get("parking", existing.get("parking", False)),
                "gym":       apt_data.get("gym", existing.get("gym", False)),
                "bathtub":   apt_data.get("bathtub", existing.get("bathtub", False)),
                "price":     apt_data.get("price") or existing.get("price"),
                "floor":     apt_data.get("floor") or existing.get("floor"),
                "size_sqm":  apt_data.get("size_sqm") or existing.get("size_sqm"),
                "photos":    apt_data.get("photos") or existing.get("photos", []),
            })
            new_status = calculate_status(existing)
            if new_status == "skip":
                print(f"⏭️  Не проходит фильтр — удаляю")
                continue
            existing["status"] = manual_status or new_status
            kept.append(existing)
            print(f"✅ Мебель ✓ — оставляю ({existing['status']})")
            time.sleep(1.5)
        removed = len(apartments) - len(kept)
        print(f"\n{'─'*60}")
        print(f"📊 Было: {len(apartments)}, удалено: {removed}, осталось: {len(kept)}")
        save_apartments(kept)
        return

    if args.list:
        apartments = load_apartments()
        if not apartments:
            print("📭 Нет сохранённых квартир")
            return
        print(f"\n📋 Всего: {len(apartments)}\n")
        for apt in apartments:
            emoji = {"good":"🟢","over_budget":"🟡","interested":"⭐","rejected":"❌"}.get(apt["status"],"🔵")
            price_str = f"{apt['price']:,} ₪" if apt.get("price") else "цена?"
            print(f"{emoji} {apt['address'][:50]:<50} {price_str:>10}  {apt['url']}")
        return

    urls = []
    if args.url:
        urls.append(args.url.strip())
    if args.file:
        fp = Path(args.file)
        if not fp.exists():
            print(f"❌ Файл не найден: {args.file}")
            sys.exit(1)
        with open(fp, "r", encoding="utf-8") as f:
            urls.extend(ln.strip() for ln in f if ln.strip() and not ln.startswith("#"))

    if not urls:
        parser.print_help()
        print("\n💡 Примеры:")
        print('   python scraper.py "https://www.yad2.co.il/realestate/item/tel-aviv-area/xxxxx"')
        print('   python scraper.py "https://www.yad2.co.il/realestate/rent?city=5000&rooms=3-3"')
        print('   python scraper.py "https://www.homeless.co.il/rent/viewad,738048.aspx"')
        print('   python scraper.py --file urls.txt')
        print('   python scraper.py --list')
        return

    apartments    = load_apartments()
    total_added   = 0

    for url in urls:
        print(f"\n{'─'*60}")
        if is_search_results_page(url):
            count = process_search_page(url, apartments)
            total_added += count
        else:
            result = process_url(url, apartments)
            if result:
                total_added += 1

    if total_added > 0:
        save_apartments(apartments)
        print(f"\n✨ Добавлено/обновлено: {total_added}")
        print("🗺️  Открой http://localhost:8080/map.html")
    else:
        print("\n⚠️  Ни одна квартира не была добавлена")


if __name__ == "__main__":
    main()
