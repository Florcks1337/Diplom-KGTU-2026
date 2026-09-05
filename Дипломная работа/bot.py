"""
VK Price Alert Bot — Отслеживание скидок на OZON и Wildberries
Запуск: python bot.py
"""

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import schedule
import time
import threading
import logging
from database import Database
from parsers import OzonParser, WildberriesSeleniumParser
from config import VK_TOKEN, ADMIN_ID, CHECK_INTERVAL_MINUTES, PRICE_DROP_THRESHOLD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

db = Database()
parsers = {
    "ozon": OzonParser(),
    "wildberries": WildberriesSeleniumParser(),
}

# ──────────────────────────────────────────────────────────────
# Клавиатуры
# ──────────────────────────────────────────────────────────────

def kb_main() -> str:
    """Главная клавиатура — всегда видна."""
    kb = VkKeyboard(one_time=False)
    kb.add_button("📋 Мои товары",  color=VkKeyboardColor.PRIMARY)
    kb.add_button("🔍 Проверить",   color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("➕ Добавить товар", color=VkKeyboardColor.SECONDARY)
    kb.add_button("❓ Помощь",         color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_list(items: list[dict]) -> str:
    """Клавиатура со списком товаров — кнопки удаления."""
    kb = VkKeyboard(one_time=False)
    for item in items[:8]:   # VK максимум 10 кнопок, оставляем запас
        label = f"🗑 [{item['id']}] {item['name'][:20]}"
        kb.add_button(label, color=VkKeyboardColor.NEGATIVE)
        kb.add_line()
    kb.add_button("🏠 Главное меню", color=VkKeyboardColor.PRIMARY)
    return kb.get_keyboard()


def kb_cancel() -> str:
    """Клавиатура с кнопкой отмены — используется в режиме ожидания ссылки."""
    kb = VkKeyboard(one_time=True)
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_empty() -> str:
    """Пустая клавиатура — скрыть все кнопки."""
    return VkKeyboard.get_empty_keyboard()


# ──────────────────────────────────────────────────────────────
# Состояния пользователей (ожидание ссылки для /add)
# ──────────────────────────────────────────────────────────────

# user_id -> "awaiting_url" | None
_user_state: dict[int, str | None] = {}


# ──────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────

def detect_marketplace(url: str) -> str | None:
    url = url.lower()
    if "ozon.ru" in url:
        return "ozon"
    if "wildberries.ru" in url or "wb.ru" in url:
        return "wildberries"
    return None


def format_price(price: float) -> str:
    return f"{price:,.0f} ₽".replace(",", " ")


def send_message(vk, user_id: int, text: str, keyboard: str | None = None):
    try:
        kwargs = dict(user_id=user_id, message=text, random_id=0)
        if keyboard is not None:
            kwargs["keyboard"] = keyboard
        vk.messages.send(**kwargs)
    except Exception as e:
        log.error("Ошибка отправки сообщения пользователю %s: %s", user_id, e)


# ──────────────────────────────────────────────────────────────
# Обработка команд от пользователя
# ──────────────────────────────────────────────────────────────

HELP_TEXT = """📦 Бот отслеживания цен на OZON и Wildberries

Просто нажми кнопку «➕ Добавить товар» и вставь ссылку —
бот пришлёт уведомление, когда цена упадёт на {pct}% и более.

Команды:
  /add <ссылка> [макс_цена]  — добавить товар
  /list                       — мои товары
  /remove <ID>                — удалить товар
  /check                      — проверить цены сейчас

Пример с целевой ценой:
  /add https://www.ozon.ru/product/... 3500
""".format(pct=PRICE_DROP_THRESHOLD)


def cmd_add(vk, user_id: int, args: list[str]):
    if not args:
        # Переходим в режим ожидания ссылки
        _user_state[user_id] = "awaiting_url"
        send_message(
            vk, user_id,
            "🔗 Отправь ссылку на товар с OZON или Wildberries.\n\n"
            "Можно сразу указать целевую цену через пробел:\n"
            "https://ozon.ru/product/... 2500",
            keyboard=kb_cancel(),
        )
        return

    url = args[0]
    target_price = None
    if len(args) >= 2:
        try:
            target_price = float(args[1].replace(",", "."))
        except ValueError:
            send_message(vk, user_id, "❌ Цена должна быть числом, например: 3500",
                         keyboard=kb_main())
            return

    marketplace = detect_marketplace(url)
    if not marketplace:
        send_message(vk, user_id,
                     "❌ Поддерживаются только ссылки с ozon.ru и wildberries.ru",
                     keyboard=kb_main())
        return

    send_message(vk, user_id, "⏳ Получаю информацию о товаре...")
    parser = parsers[marketplace]

    try:
        info = parser.get_product_info(url)
    except Exception as e:
        log.error("Ошибка парсинга %s: %s", url, e)
        send_message(vk, user_id,
                     "❌ Не удалось получить данные о товаре. Проверьте ссылку.",
                     keyboard=kb_main())
        return

    if not info:
        send_message(vk, user_id,
                     "❌ Товар не найден. Возможно, ссылка устарела.",
                     keyboard=kb_main())
        return

    item_id = db.add_item(
        user_id=user_id,
        url=url,
        marketplace=marketplace,
        name=info["name"],
        current_price=info["price"],
        target_price=target_price,
    )

    store_emoji = "🟠" if marketplace == "ozon" else "🟣"
    msg = (
        f"✅ Товар добавлен!\n\n"
        f"🛍 {info['name']}\n"
        f"💰 Цена сейчас: {format_price(info['price'])}\n"
        f"{store_emoji} {marketplace.upper()}  •  ID: {item_id}\n"
    )
    if info.get("old_price"):
        msg += f"🏷 Старая цена: {format_price(info['old_price'])}\n"
    if target_price:
        msg += f"🎯 Целевая цена: {format_price(target_price)}\n"
    msg += f"\n🔔 Буду уведомлять каждые {CHECK_INTERVAL_MINUTES} мин."

    send_message(vk, user_id, msg, keyboard=kb_main())


def cmd_list(vk, user_id: int):
    items = db.get_user_items(user_id)
    if not items:
        send_message(
            vk, user_id,
            "📋 Список пуст.\n\nНажми «➕ Добавить товар» и вставь ссылку.",
            keyboard=kb_main(),
        )
        return

    lines = ["📋 Твои товары:\n"]
    for item in items:
        store_emoji = "🟠" if item["marketplace"] == "ozon" else "🟣"
        target = f"\n    🎯 цель: {format_price(item['target_price'])}" if item["target_price"] else ""
        lines.append(
            f"{store_emoji} [{item['id']}] {item['name'][:45]}\n"
            f"    💰 {format_price(item['current_price'])}{target}\n"
        )
    lines.append("Нажми кнопку товара чтобы удалить его.")
    send_message(vk, user_id, "\n".join(lines), keyboard=kb_list(items))


def cmd_remove(vk, user_id: int, args: list[str]):
    if not args or not args[0].isdigit():
        send_message(vk, user_id, "❌ Укажите ID товара. Список: /list",
                     keyboard=kb_main())
        return
    item_id = int(args[0])
    if db.remove_item(user_id, item_id):
        send_message(vk, user_id, f"🗑 Товар #{item_id} удалён.", keyboard=kb_main())
    else:
        send_message(vk, user_id, "❌ Товар не найден.", keyboard=kb_main())


def cmd_check(vk, user_id: int):
    send_message(vk, user_id, "🔍 Проверяю цены, подожди...")
    items = db.get_user_items(user_id)
    if not items:
        send_message(vk, user_id, "📋 Нет добавленных товаров.", keyboard=kb_main())
        return
    results = check_prices_for_items(vk, items, notify=False)
    if not results:
        send_message(vk, user_id, "✅ Изменений нет. Цены не снизились.",
                     keyboard=kb_main())
    else:
        for msg in results:
            send_message(vk, user_id, msg, keyboard=kb_main())


# ──────────────────────────────────────────────────────────────
# Проверка цен
# ──────────────────────────────────────────────────────────────

def check_prices_for_items(vk, items: list[dict], notify: bool = True) -> list[str]:
    alerts = []
    for item in items:
        parser = parsers.get(item["marketplace"])
        if not parser:
            continue
        try:
            info = parser.get_product_info(item["url"])
            if not info:
                continue
            new_price = info["price"]
            old_price = item["current_price"]

            db.update_price(item["id"], new_price)
            db.add_price_history(item["id"], new_price)

            drop_pct = (old_price - new_price) / old_price * 100 if old_price > 0 else 0
            target_hit = item["target_price"] and new_price <= item["target_price"]
            significant_drop = drop_pct >= PRICE_DROP_THRESHOLD

            if new_price < old_price and (target_hit or significant_drop):
                store_emoji = "🟠" if item["marketplace"] == "ozon" else "🟣"
                msg = (
                    f"🔥 Цена упала!\n\n"
                    f"🛍 {item['name']}\n"
                    f"📉 {format_price(old_price)} → {format_price(new_price)} "
                    f"(-{drop_pct:.1f}%)\n"
                    f"{store_emoji} {item['marketplace'].upper()}\n"
                    f"🔗 {item['url']}"
                )
                if target_hit:
                    msg += f"\n\n✅ Достигла целевой цены {format_price(item['target_price'])}!"
                alerts.append(msg)
                if notify:
                    send_message(vk, item["user_id"], msg, keyboard=kb_main())

        except Exception as e:
            log.error("Ошибка проверки товара %s: %s", item["id"], e)
    return alerts


def scheduled_check(vk):
    log.info("Плановая проверка цен...")
    all_items = db.get_all_items()
    check_prices_for_items(vk, all_items, notify=True)
    log.info("Проверка завершена. Товаров: %d", len(all_items))


# ──────────────────────────────────────────────────────────────
# Основной цикл обработки сообщений
# ──────────────────────────────────────────────────────────────

def handle_message(vk, user_id: int, text: str):
    text = text.strip()
    parts = text.split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]

    # ── Режим ожидания ссылки (после нажатия «➕ Добавить товар») ──
    if _user_state.get(user_id) == "awaiting_url":
        if text in ("❌ Отмена", "/cancel", "отмена"):
            _user_state.pop(user_id, None)
            send_message(vk, user_id, "Отменено.", keyboard=kb_main())
            return
        # Пробуем распознать URL
        url_parts = text.split()
        potential_url = url_parts[0]
        if detect_marketplace(potential_url):
            _user_state.pop(user_id, None)
            cmd_add(vk, user_id, url_parts)
            return
        else:
            send_message(
                vk, user_id,
                "❌ Не похоже на ссылку OZON или Wildberries.\nПопробуй ещё раз или нажми «Отмена».",
                keyboard=kb_cancel(),
            )
            return

    # ── Кнопки главного меню ──
    if text == "📋 Мои товары":
        cmd_list(vk, user_id)
        return
    if text == "🔍 Проверить":
        cmd_check(vk, user_id)
        return
    if text == "➕ Добавить товар":
        cmd_add(vk, user_id, [])
        return
    if text in ("❓ Помощь", "/help", "/start", "помощь", "help"):
        send_message(vk, user_id, HELP_TEXT, keyboard=kb_main())
        return
    if text == "🏠 Главное меню":
        send_message(vk, user_id, "Главное меню:", keyboard=kb_main())
        return

    # ── Удаление по нажатию кнопки из kb_list: «🗑 [3] Название...» ──
    if text.startswith("🗑 ["):
        import re
        m = re.match(r"🗑 \[(\d+)\]", text)
        if m:
            cmd_remove(vk, user_id, [m.group(1)])
            return

    # ── Текстовые команды (обратная совместимость) ──
    if cmd == "/add":
        cmd_add(vk, user_id, args)
    elif cmd == "/list":
        cmd_list(vk, user_id)
    elif cmd in ("/remove", "/del", "/delete"):
        cmd_remove(vk, user_id, args)
    elif cmd == "/check":
        cmd_check(vk, user_id)
    else:
        send_message(
            vk, user_id,
            "Привет! 👋 Используй кнопки ниже или напиши /help.",
            keyboard=kb_main(),
        )


def run_scheduler(vk):
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(scheduled_check, vk)
    while True:
        schedule.run_pending()
        time.sleep(30)


def main():
    log.info("Запуск VK Price Bot...")
    session = vk_api.VkApi(token=VK_TOKEN)
    vk = session.get_api()
    longpoll = VkLongPoll(session)

    scheduler_thread = threading.Thread(target=run_scheduler, args=(vk,), daemon=True)
    scheduler_thread.start()
    log.info("Планировщик запущен (каждые %d минут)", CHECK_INTERVAL_MINUTES)

    log.info("Бот готов. Ожидаю сообщения...")
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            try:
                handle_message(vk, event.user_id, event.text)
            except Exception as e:
                log.error("Ошибка обработки сообщения: %s", e)


if __name__ == "__main__":
    main()
