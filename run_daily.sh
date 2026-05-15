#!/usr/bin/env bash
# Ежедневный парсинг квартир + публикация на GitHub Pages
# Запускается cron'ом в 8:00 каждый день

PROJECT_DIR="/Users/nastyabaturlina/Downloads/tel-aviv-apartments"
PYTHON="/usr/bin/python3"
GIT="/usr/bin/git"
LOG="$PROJECT_DIR/scraper_log.txt"

cd "$PROJECT_DIR"

echo "" >> "$LOG"
echo "======================================" >> "$LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск" >> "$LOG"

# Количество квартир до парсинга
COUNT_BEFORE=$("$PYTHON" -c "import json; d=json.load(open('apartments.json')); print(len(d))" 2>/dev/null || echo "0")

# ─── Парсинг по источникам ─────────────────────────────────────────────────

echo "[$(date '+%Y-%m-%d %H:%M:%S')] yad2..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/scraper.py" \
  'https://www.yad2.co.il/realestate/rent/tel-aviv-area?maxPrice=12000&minRooms=3&maxRooms=3' \
  >> "$LOG" 2>&1 || echo "  [!] yad2: ошибка (вероятно, IP-блокировка)" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] homeless..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/scraper.py" \
  'https://www.homeless.co.il/rent/Tel-Aviv/$inumber4=5$inumber4_1=5$flong3_1=12000' \
  >> "$LOG" 2>&1 || echo "  [!] homeless: ошибка" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] janglo..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/scraper.py" \
  'https://www.janglo.net/real-estate-rentals/apartments/telaviv?rooms=opt3&price_max=12000' \
  >> "$LOG" 2>&1 || echo "  [!] janglo: ошибка" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] craigslist..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/scraper.py" \
  'https://telaviv.craigslist.org/search/apa?is_furnished=1&max_price=12000&min_bedrooms=2' \
  >> "$LOG" 2>&1 || echo "  [!] craigslist: ошибка" >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] onmap..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/scraper.py" \
  'https://www.onmap.co.il/en/search/homes/rent/price_0-12000/rooms_3/c_31.943030,34.796780/t_32.115630,35.071440/z_10' \
  >> "$LOG" 2>&1 || echo "  [!] onmap: ошибка" >> "$LOG"

# ─── Геокодирование новых квартир ──────────────────────────────────────────

echo "[$(date '+%Y-%m-%d %H:%M:%S')] geocode..." >> "$LOG"
"$PYTHON" "$PROJECT_DIR/geocode.py" >> "$LOG" 2>&1 || echo "  [!] geocode: ошибка" >> "$LOG"

# ─── Итог ──────────────────────────────────────────────────────────────────

COUNT_AFTER=$("$PYTHON" -c "import json; d=json.load(open('apartments.json')); print(len(d))" 2>/dev/null || echo "$COUNT_BEFORE")
NEW=$((COUNT_AFTER - COUNT_BEFORE))
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Итого: +$NEW новых (было $COUNT_BEFORE → стало $COUNT_AFTER)" >> "$LOG"

# ─── Git: коммит и push только если apartments.json изменился ──────────────

if ! "$GIT" -C "$PROJECT_DIR" diff --quiet apartments.json; then
  "$GIT" -C "$PROJECT_DIR" add apartments.json
  "$GIT" -C "$PROJECT_DIR" commit -m "Auto: +$NEW квартир ($(date '+%Y-%m-%d'))" >> "$LOG" 2>&1
  "$GIT" -C "$PROJECT_DIR" push >> "$LOG" 2>&1 \
    && echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запушено на GitHub ✓" >> "$LOG" \
    || echo "[$(date '+%Y-%m-%d %H:%M:%S')] [!] push не удался" >> "$LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Нет изменений — push не нужен" >> "$LOG"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Готово" >> "$LOG"
