"""
Геокодер адресов
================
Получает координаты (lat/lng) для квартир без координат.
Использует Nominatim (OpenStreetMap) — бесплатно, без API-ключа.

Использование:
  python geocode.py
"""

import json
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests
except ImportError:
    print("❌ Запусти: pip install requests")
    exit(1)

DATA_FILE = Path("apartments.json")

# Nominatim требует задержку между запросами — минимум 1 секунда
DELAY_SECONDS = 1.2


def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    """
    Получает координаты адреса через Nominatim (OpenStreetMap).
    Возвращает (lat, lng) или None если не нашёл.
    """
    import re
    # Убираем префикс "TYPE N חדרים להשכרה ב..." оставляем город+улица+район
    cleaned = re.sub(r'^.+?להשכרה\s+ב', '', address).strip()
    if not cleaned:
        cleaned = address
    # Разбиваем по запятой/восклицательному знаку
    parts = [p.strip() for p in re.split(r'[,!•\-]', cleaned) if p.strip() and len(p.strip()) > 1]

    # Список запросов для перебора (от точного к общему)
    candidates = []
    if len(parts) >= 3:
        candidates.append(f"{parts[1]}, {parts[2]}, Tel Aviv, Israel")   # улица, район
    if len(parts) >= 2:
        candidates.append(f"{parts[1]}, Tel Aviv, Israel")                # только улица
        candidates.append(f"{parts[0]}, {parts[1]}, Tel Aviv, Israel")   # город, улица
    if parts:
        candidates.append(f"{parts[0]}, Tel Aviv, Israel")               # только первый компонент
    candidates.append(f"{address[:80]}, Israel")                          # полный адрес как fallback

    # Пробуем каждый кандидат
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "TelAvivApartmentFinder/1.0 (personal use)"}
    for query in candidates:
        try:
            response = requests.get(url, params={"q": query, "format": "json", "limit": 1, "countrycodes": "il"},
                                    headers=headers, timeout=10)
            response.raise_for_status()
            results = response.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
            time.sleep(0.5)
        except Exception as e:
            print(f"   ⚠️  {e}")
    return None

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "il",
    }
    headers = {
        "User-Agent": "TelAvivApartmentFinder/1.0 (personal use)"
    }



def main():
    if not DATA_FILE.exists():
        print("❌ Файл apartments.json не найден. Сначала добавь квартиры через scraper.py")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        apartments = json.load(f)

    # Находим квартиры без координат
    need_geocoding = [apt for apt in apartments if apt.get("lat") is None]

    if not need_geocoding:
        print("✅ Все квартиры уже имеют координаты")
        return

    print(f"📍 Нужно геокодировать: {len(need_geocoding)} квартир")
    print("   (Nominatim требует паузы между запросами — это займёт немного времени)\n")

    success = 0
    for apt in need_geocoding:
        address = apt.get("address", "")
        if not address or address == "Адрес не найден":
            print(f"⏭️  Пропускаю — нет адреса: {apt['url']}")
            continue

        print(f"🔍 {address[:60]}")
        result = geocode_address(address)

        if result:
            apt["lat"], apt["lng"] = result
            print(f"   ✅ {apt['lat']:.4f}, {apt['lng']:.4f}")
            success += 1
        else:
            print(f"   ❌ Не нашёл координаты")
            print(f"      Попробуй уточнить адрес вручную в apartments.json")

        time.sleep(DELAY_SECONDS)

    # Сохраняем обновлённые данные
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(apartments, f, ensure_ascii=False, indent=2)

    print(f"\n✨ Геокодировано: {success} из {len(need_geocoding)}")
    print("🗺️  Открой map.html для просмотра карты")


if __name__ == "__main__":
    main()
