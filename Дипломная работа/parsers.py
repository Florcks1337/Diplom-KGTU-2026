"""
parsers.py — Модуль сбора данных о ценах с маркетплейсов.

Wildberries : публичный REST API (без авторизации, стабильно).
OZON        : Selenium + Chrome headless (обход защиты от ботов).
"""

import re
import json
import time
import random
import logging

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

log = logging.getLogger(__name__)


_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
})


class WildberriesParser:
    """
    Получает данные о товаре через публичный API Wildberries.
    Авторизация не нужна. Цены в API хранятся в единицах 1/100 копейки.
    """

    WB_API = "https://card.wb.ru/cards/v1/detail"

    def get_product_info(self, url: str) -> dict | None:
        """
        Возвращает словарь {name, price, old_price, discount} или None.
        """
        item_id = self._extract_id(url)
        if not item_id:
            log.error("WB: не удалось извлечь ID товара из URL: %s", url)
            return None

        params = {
            "appType": 1,
            "curr":    "rub",
            "dest":    -1257786,   
            "nm":      item_id,
        }

        try:
            resp = _SESSION.get(self.WB_API, params=params, timeout=15)
            resp.raise_for_status()
            return self._parse(resp.json())
        except requests.RequestException as e:
            log.error("WB: ошибка запроса к API: %s", e)
            return None

    def _extract_id(self, url: str) -> str | None:
        """
        Извлекает числовой ID товара из URL Wildberries.
        Поддерживаемые форматы:
          https://www.wildberries.ru/catalog/123456789/detail.aspx
          https://www.wildberries.ru/catalog/123456789/
          https://wb.ru/catalog/123456789/detail.aspx
          https://wildberries.ru/.../название-123456789
        """
        m = re.search(r'/catalog/(\d+)/', url)
        if m:
            return m.group(1)
        m = re.search(r'-(\d{6,})(?:/|$|\?)', url)
        if m:
            return m.group(1)
        m = re.search(r'/(\d{6,})(?:/|$|\?)', url)
        return m.group(1) if m else None

    def _parse(self, data: dict) -> dict | None:
        """Разбирает JSON-ответ API Wildberries."""
        try:
            products = data.get("data", {}).get("products", [])
            if not products:
                log.warning("WB: товар не найден в ответе API")
                return None

            product = products[0]
            name    = product.get("name", "Товар Wildberries")
            sizes   = product.get("sizes", [])

            if not sizes:
                log.warning("WB: нет информации о размерах/цене для товара: %s", name)
                return None

            price_data = sizes[0].get("price", {})
            price     = price_data.get("product", 0) / 100
            old_price = price_data.get("basic",   None)
            if old_price:
                old_price = old_price / 100

            discount = product.get("sale")   
            if price <= 0:
                log.warning("WB: цена товара равна 0: %s", name)
                return None

            log.info("WB: %s — %.0f ₽", name[:50], price)
            return {
                "name":      name,
                "price":     round(price,     2),
                "old_price": round(old_price, 2) if old_price else None,
                "discount":  discount,
            }

        except (KeyError, IndexError, TypeError) as e:
            log.error("WB: ошибка разбора ответа API: %s", e)
            return None



def _create_driver() -> webdriver.Chrome:
    """
    Создаёт экземпляр Chrome в headless-режиме с отключёнными
    признаками автоматизации (чтобы не срабатывала защита OZON).
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": (
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        }
    )
    return driver



class OzonParser:
    """
    Получает данные о товаре OZON через Selenium + Chrome headless.
    Обходит защиту от ботов (403 на обычные HTTP-запросы).
    Каждый вызов get_product_info() запускает и закрывает браузер.
    """

    PRICE_SELECTORS = [
        "span[class*='tsHeadline500Medium']",
        "div[data-widget='webPrice'] span",
        "span[class*='price-final']",
        "span[class*='_price']",
        "div[class*='price'] span",
        "l-text[style*='bold'] span",
    ]

    OLD_PRICE_SELECTORS = [
        "span[class*='price-old']",
        "span[class*='old-price']",
        "span[class*='_old']",
        "span[style*='line-through']",
        "div[data-widget='webPrice'] span[class*='old']",
    ]

    def get_product_info(self, url: str) -> dict | None:
        """
        Возвращает словарь {name, price, old_price, discount} или None.
        """
        clean_url = url.split("?")[0].rstrip("/") + "/"

        driver = None
        try:
            log.info("OZON: запускаю браузер для %s", clean_url)
            driver = _create_driver()
            driver.get(clean_url)

            wait = WebDriverWait(driver, 20)
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h1")))
            except Exception:
                log.warning("OZON: страница не загрузилась за 20 секунд: %s", clean_url)

            time.sleep(random.uniform(2.5, 4.0))

            return self._extract(driver)

        except Exception as e:
            log.error("OZON Selenium error: %s", e)
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _extract(self, driver: webdriver.Chrome) -> dict | None:
        """Извлекает данные из загруженной страницы."""

        name = "Товар OZON"
        try:
            name = driver.find_element(By.CSS_SELECTOR, "h1").text.strip()
        except Exception:
            log.warning("OZON: не удалось найти h1 на странице")

        price = None
        for sel in self.PRICE_SELECTORS:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text   = el.text.strip()
                    digits = re.sub(r"[^\d]", "", text)
                    if digits and int(digits) > 0:
                        price = float(digits)
                        break
                if price:
                    break
            except Exception:
                continue

        old_price = None
        for sel in self.OLD_PRICE_SELECTORS:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text   = el.text.strip()
                    digits = re.sub(r"[^\d]", "", text)
                    if digits and int(digits) > 0:
                        val = float(digits)
                        if price and val > price:
                            old_price = val
                            break
                if old_price:
                    break
            except Exception:
                continue

        if not price:
            try:
                page_source = driver.page_source
                for match in re.finditer(
                    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                    page_source,
                    re.DOTALL
                ):
                    try:
                        data = json.loads(match.group(1))
                        if data.get("@type") == "Product":
                            offer = data.get("offers", {})
                            p_val = float(offer.get("price", 0))
                            if p_val > 0:
                                price = p_val
                                if not name or name == "Товар OZON":
                                    name = data.get("name", name)
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        if not price:
            log.warning("OZON: цена не найдена, URL: %s", driver.current_url)
            return None

        log.info("OZON: %s — %.0f ₽", name[:50], price)
        return {
            "name":      name,
            "price":     price,
            "old_price": old_price,
            "discount":  None,
        }


class WildberriesSeleniumParser:
    """
    Получает данные о товаре Wildberries через Selenium + Chrome headless.
    Используется как резервный вариант, когда публичный API недоступен
    или возвращает некорректные данные.
    Каждый вызов get_product_info() запускает и закрывает браузер.
    """

    PRICE_SELECTORS = [
        "[class*='priceBlockFinalPrice']",   
        "[class*='priceBlockWalletPrice']",  
        "[class*='priceBlockPrice']",        
    ]

    
    OLD_PRICE_SELECTORS = [
        "[class*='priceBlockOldPrice']",    
    ]

    DISCOUNT_SELECTORS = [
        "[class*='priceBlockDiscount']",
        "[class*='discount']",
    ]

    def get_product_info(self, url: str) -> dict | None:
        """
        Возвращает словарь {name, price, old_price, discount} или None.
        """
        clean_url = url.split("?")[0].rstrip("/")
        if not clean_url.endswith("detail.aspx"):
            clean_url = clean_url + "/detail.aspx"

        driver = None
        try:
            log.info("WB Selenium: запускаю браузер для %s", clean_url)
            driver = _create_driver()
            driver.get(clean_url)

            wait = WebDriverWait(driver, 20)
            try:
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "[class*='priceBlockFinalPrice'], [class*='priceBlock']")
                ))
            except Exception:
                log.warning("WB Selenium: страница не загрузилась за 20 секунд: %s", clean_url)

            time.sleep(random.uniform(2.5, 4.0))

            return self._extract(driver)

        except Exception as e:
            log.error("WB Selenium error: %s", e)
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _extract(self, driver: webdriver.Chrome) -> dict | None:
        """Извлекает данные из загруженной страницы WB."""

        name = "Товар Wildberries"
        for sel in ["[class*='productTitle']", "[class*='productNameContainer']", "h1", "h2[class*='product']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                text = el.text.strip()
                if text and len(text) > 3:
                    name = text
                    break
            except Exception:
                continue

        price = None
        for sel in self.PRICE_SELECTORS:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text   = el.text.strip()
                    digits = re.sub(r"[^\d]", "", text)
                    if digits and int(digits) > 0:
                        price = float(digits)
                        break
                if price:
                    break
            except Exception:
                continue

        old_price = None
        for sel in self.OLD_PRICE_SELECTORS:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text   = el.text.strip()
                    digits = re.sub(r"[^\d]", "", text)
                    if digits and int(digits) > 0:
                        val = float(digits)
                        if price and val > price:
                            old_price = val
                            break
                if old_price:
                    break
            except Exception:
                continue

        discount = None
        for sel in self.DISCOUNT_SELECTORS:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text   = el.text.strip()
                    digits = re.sub(r"[^\d]", "", text)
                    if digits:
                        val = int(digits)
                        if 1 <= val <= 99:  
                            discount = val
                            break
                if discount:
                    break
            except Exception:
                continue

        if not price:
            try:
                page_source = driver.page_source
                for match in re.finditer(
                    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                    page_source,
                    re.DOTALL,
                ):
                    try:
                        data = json.loads(match.group(1))
                        if data.get("@type") == "Product":
                            offer = data.get("offers", {})
                            p_val = float(offer.get("price", 0))
                            if p_val > 0:
                                price = p_val
                                if not name or name == "Товар Wildberries":
                                    name = data.get("name", name)
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        if not price:
            log.warning("WB Selenium: цена не найдена, URL: %s", driver.current_url)
            return None

        log.info("WB Selenium: %s — %.0f ₽", name[:50], price)
        return {
            "name":      name,
            "price":     price,
            "old_price": old_price,
            "discount":  discount,
        }
