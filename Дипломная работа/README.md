# VK Price Alert Bot 🛍

Бот ВКонтакте для отслеживания цен на **OZON** и **Wildberries**.
Присылает уведомление, когда цена на товар падает.

---

## Быстрый старт

### 1. Создайте группу ВКонтакте

1. Зайдите на vk.com → **Создать сообщество** → Тип: Бизнес или Публичная страница
2. Перейдите в **Управление → Работа с API → Создать ключ**
3. Включите разрешение **«Сообщения сообщества»**
4. Скопируйте токен

### 2. Установите Python и зависимости

```bash
# Требуется Python 3.10+
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Настройте конфиг

Откройте `config.py` и заполните:

```python
VK_TOKEN = "ваш_токен_группы_вк"
ADMIN_ID = 123456789          # ваш VK ID
CHECK_INTERVAL_MINUTES = 30   # проверять каждые 30 минут
PRICE_DROP_THRESHOLD = 5      # уведомлять при снижении на 5%
```

### 4. Включите Long Poll API

В настройках группы ВКонтакте:
**Управление → Работа с API → Long Poll API → Включить**
Версия API: **5.131**
События: ✅ Входящие сообщения

### 5. Запустите бота

```bash
python bot.py
```

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/add <ссылка>` | Добавить товар |
| `/add <ссылка> 2500` | Добавить с целевой ценой |
| `/list` | Список отслеживаемых товаров |
| `/remove 3` | Удалить товар по ID |
| `/check` | Проверить цены прямо сейчас |
| `/help` | Справка |

---

## Поддерживаемые маркетплейсы

| Маркетплейс | Метод | Стабильность |
|-------------|-------|--------------|
| **Wildberries** | Публичный API | ⭐⭐⭐ Отлично |
| **OZON** | Web-scraping | ⭐⭐ Хорошо* |

*OZON может блокировать запросы. При проблемах используйте прокси.

---

## Структура проекта

```
vk_price_bot/
├── bot.py          — главный файл, обработка сообщений
├── parsers.py      — парсеры OZON и Wildberries
├── database.py     — база данных SQLite
├── config.py       — настройки
├── requirements.txt
└── prices.db       — создаётся автоматически
```

---

## Запуск как фоновый сервис (Linux/systemd)

Создайте файл `/etc/systemd/system/vk-price-bot.service`:

```ini
[Unit]
Description=VK Price Alert Bot
After=network.target

[Service]
WorkingDirectory=/путь/к/vk_price_bot
ExecStart=/путь/к/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable vk-price-bot
sudo systemctl start vk-price-bot
sudo systemctl status vk-price-bot
```

---

## Частые вопросы

**OZON не возвращает цену** — OZON защищается от парсинга.
Решения: добавьте задержки, смените User-Agent, используйте прокси.

**Бот не отвечает** — Проверьте, что Long Poll API включён в настройках группы.

**Хочу добавить другой маркетплейс** — создайте класс в `parsers.py`
по образцу `WildberriesParser`, добавьте его в словарь `parsers` в `bot.py`.
