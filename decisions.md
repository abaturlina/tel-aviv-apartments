# Журнал решений и проблем

Записывай сюда всё важное: что сломалось, что изменилось на сайтах, какие решения принял.
Claude Code будет читать этот файл и учитывать при работе.

---

## Формат записи

```
### [Дата] Краткое описание
**Проблема:** что случилось
**Причина:** почему
**Решение:** что сделали
```

---

## Записи

### [2026-05-13] yad2: бот-защита блокирует requests, переход на Playwright + __NEXT_DATA__

**Проблема:** `requests.get()` на yad2.co.il получает CAPTCHA-страницу ShieldSquare/Radware вместо реального контента. Все эндпоинты (основной сайт, API gw.yad2.co.il, Next.js /_next/data) блокируются.

**Причина:** yad2 использует агрессивную bot-защиту RadWare ShieldSquare, которая требует выполнения JavaScript для верификации. Простой HTTP-клиент не проходит проверку.

**Решение:**
- Добавлен Playwright (headless Chromium) для загрузки страниц yad2 и madlan
- Вместо парсинга HTML-селекторов используется `__NEXT_DATA__` JSON (Next.js SSR), который содержит все данные объявления в структурированном виде
- Структура JSON: `props.pageProps.dehydratedState.queries[0].state.data`
- Ключевые поля: `price`, `additionalDetails.roomsCount`, `additionalDetails.squareMeter`, `address.house.floor`, `inProperty.includeSecurityRoom` (mamad), `inProperty.includeParking`, `address.coords.lat/lon`
- Координаты (lat/lng) теперь извлекаются напрямую из JSON — geocode.py для yad2 не нужен
- URL формат yad2 изменился: `https://www.yad2.co.il/realestate/item/tel-aviv-area/TOKEN` (не `/item/TOKEN`)
- Python 3.9 не поддерживает `int | None` синтаксис — заменён на `Optional[int]` / убран

**Тестовое объявление:** https://www.yad2.co.il/realestate/item/tel-aviv-area/zoriirhk
— 3 комнаты, 9300 ILS, mamad ✓, parking ✓, координаты уже в JSON

---

### [2026-05-14] madlan.co.il: CAPTCHA-блокировка headless-браузера

**Проблема:** madlan.co.il показывает интерактивный пазл-CAPTCHA (сообщение "בזמן שגלשת... משהו בדפדפן שלך גרם לנו לחשוב שאתה רובוט") вместо результатов поиска. Страница не содержит ни `__NEXT_DATA__`, ни ссылок на объявления.

**Причина:** madlan использует собственную bot-защиту поверх Cloudflare, которая обнаруживает headless Chromium (даже с `--disable-blink-features=AutomationControlled` и скрытием `navigator.webdriver`). Единственный выход — решить CAPTCHA вручную.

**Решение:** madlan недоступен для автоматического парсинга. Использовать yad2 и homeless.co.il. Парсер madlan оставлен в коде на случай если ситуация изменится.

---

### [2026-05-14] homeless.co.il: 403 с requests, URL объявлений — относительные пути

**Проблема:** homeless.co.il возвращает 403 при использовании `requests.get()`. URL объявлений в HTML — относительные пути вида `/rent/viewad,XXXXXX.aspx`, а не полные URL.

**Решение:**
- Добавлен Playwright для homeless.co.il (как для yad2 и madlan)
- `extract_urls_homeless` переписан: ищет `href` атрибуты с `viewad` и строит полный URL
- Аналогично исправлен `extract_urls_madlan` — тоже ищет относительные `/listing/` пути
