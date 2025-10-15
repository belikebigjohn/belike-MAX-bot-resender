import asyncio
import logging
import time
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ===== КОНФИГУРАЦИЯ =====
TELEGRAM_BOT_TOKEN = "" # токен бота из BotFather
TELEGRAM_CHAT_ID = ""  # айди чата, куда бот будет отправлять сообщения
MAX_LOGIN_URL = "https://web.max.ru/" # НЕ ИЗМЕНЯТЬ, бот заходит на указанную ссылку
MAX_USERNAME = ""  #  БЕЗ +7  ТОЛЬКО 9991234567

# ===== настройка логирования =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('max_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class MaxMonitorBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.driver = None
        self.is_monitoring = False
        self.processed_messages = set()
        self.awaiting_verification = False

        self.dp.message.register(self.cmd_start, Command("start"))
        self.dp.message.register(self.cmd_stop, Command("stop"))
        self.dp.message.register(self.cmd_status, Command("status"))
        self.dp.message.register(self.cmd_code, Command("code"))

    def setup_driver(self):
        #настройка chrome браузера
        try:
            service = Service(ChromeDriverManager().install())

            chrome_options = Options()
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            # для отладки убираем headless режим
            # chrome_options.add_argument("--headless")

            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.implicitly_wait(10)

            logger.info("✅ chrome браузер запущен")
            return driver

        except Exception as e:
            logger.error(f"❌ ошибка запуска браузера: {e}")
            return None

    async def login_to_max(self):
        """авторизация в max """
        try:
            logger.info("🔐 открываю страницу max")
            self.driver.get(MAX_LOGIN_URL)
            await asyncio.sleep(3)  # ждем полной загрузки

            # делаем скриншот для отладки
            self.driver.save_screenshot("debug_login_page.png")
            logger.info("📸 скриншот страницы сохранен")

            # ждем полной загрузки страницы
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            logger.info("📱 ищу поле для номера телефона")

            # улучшенный поиск поля для телефона
            phone_selectors = [
                "input[type='tel']",
                "input[type='text']",
                "input[name='phone']",
                "input[placeholder*='телефон']",
                "input[placeholder*='phone']"
            ]

            phone_field = None
            for selector in phone_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            phone_field = element
                            logger.info(f"✅ нашел поле для телефона: {selector}")
                            break
                    if phone_field:
                        break
                except:
                    continue

            if not phone_field:
                logger.error("❌ не нашел поле для телефона")
                return "error"

            # ввод номера телефона
            phone_field.clear()
            phone_field.send_keys(MAX_USERNAME)
            logger.info("✅ номер телефона введен")
            await asyncio.sleep(2)

            # улучшенный поиск кнопки "Войти"
            button_selectors = [
                "//button[contains(text(), 'Войти')]",
                "//button[contains(text(), 'Continue')]",
                "//button[@type='submit']",
                "//button[contains(@class, 'button')]",
                "//button[contains(@class, 'submit')]"
            ]

            login_button = None
            for selector in button_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            login_button = element
                            logger.info(f"✅ нашел кнопку: {selector}")
                            break
                    if login_button:
                        break
                except:
                    continue

            if not login_button:
                # последняя попытка - найти любую кнопку
                all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                for button in all_buttons:
                    if button.is_displayed():
                        login_button = button
                        logger.info("✅ нашел какую-то кнопку")
                        break

            if login_button:
                # нажимаем кнопку через javascript (надежнее)
                self.driver.execute_script("arguments[0].click();", login_button)
                logger.info("✅ кнопка нажата")
            else:
                # если кнопку не нашли - пробуем нажать enter
                phone_field.send_keys(Keys.ENTER)
                logger.info("✅ нажал enter в поле ввода")

            await asyncio.sleep(5)

            # делаем скриншот после нажатия кнопки
            self.driver.save_screenshot("debug_after_login.png")

            # проверяем появились ли поля для sms кода (6 отдельных полей)
            try:
                code_fields = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR,
                                                         "input[type='text'][inputmode='numeric'], "
                                                         "input[type='number'], "
                                                         "input[maxlength='1']"))
                )

                if len(code_fields) >= 4:  # если есть несколько полей для цифр
                    logger.info(f"📋 нашел {len(code_fields)} полей для sms кода")
                    self.awaiting_verification = True

                    await self.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text="🔐 <b>требуется sms код от max!</b>\n\n"
                             "отправьте команду:\n"
                             "<code>/code 123456</code>\n\n"
                             "где 123456 - код из sms"
                    )
                    return "awaiting_code"

            except Exception as e:
                logger.info(f"⚠️ поля для кода не появились: {e}")

            # проверяем успешный вход без кода
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".chat, .message, [class*='chat']"))
                )
                logger.info("✅ вход выполнен без кода")
                return "success"
            except:
                logger.error("❌ не удалось войти")
                return "error"

        except Exception as e:
            logger.error(f"❌ ошибка авторизации: {e}")
            # сохраняем скриншот ошибки
            try:
                self.driver.save_screenshot("debug_error.png")
            except:
                pass
            return "error"

    async def enter_sms_code(self, code):
        """ввод 6-значного кода в отдельные поля"""
        try:
            logger.info(f"🔄 начинаю ввод кода: {code}")

            # проверяем что код состоит из 6 цифр
            if len(code) != 6 or not code.isdigit():
                logger.error("❌ код должен быть 6 цифр")
                return False

            # ищем все 6 полей для ввода цифр
            code_fields = WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR,
                                                     "input[type='text'][inputmode='numeric'], "
                                                     "input[type='number'], "
                                                     "input[maxlength='1']"))
            )

            logger.info(f"✅ нашел {len(code_fields)} полей для ввода")

            # вводим код по одной цифре в каждое поле
            for i, digit in enumerate(code):
                if i < len(code_fields):
                    code_fields[i].clear()
                    code_fields[i].send_keys(digit)
                    await asyncio.sleep(0.3)  # пауза между вводом цифр

            logger.info(f"✅ код {code} введен")

            # ищем кнопку подтверждения
            confirm_buttons = self.driver.find_elements(By.XPATH,
                                                        "//button[contains(text(), 'Подтвердить')] | "
                                                        "//button[contains(text(), 'Confirm')] | "
                                                        "//button[@type='submit']")

            for button in confirm_buttons:
                if button.is_displayed() and button.is_enabled():
                    self.driver.execute_script("arguments[0].click();", button)
                    logger.info("✅ кнопка подтверждения нажата")
                    break

            self.awaiting_verification = False
            await asyncio.sleep(5)

            # проверяем успешный вход
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".chat, .message, [class*='chat']"))
                )
                logger.info("✅ вход выполнен успешно")
                return True
            except:
                logger.warning("⚠️ не удалось подтвердить вход, но продолжаем")
                return True

        except Exception as e:
            logger.error(f"❌ ошибка ввода кода: {e}")
            return False

    async def monitor_messages(self):
        """мониторинг новых сообщений"""
        logger.info("👀 начинаю мониторинг сообщений")

        while self.is_monitoring:
            try:
                # ищем элементы сообщений
                message_selectors = [
                    "[class*='message']",
                    "[class*='msg']",
                    ".message",
                    ".msg",
                    "[role='listitem']"
                ]

                messages = []
                for selector in message_selectors:
                    try:
                        found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if found:
                            messages.extend(found)
                    except:
                        continue

                # обрабатываем новые сообщения
                for message_element in messages[-10:]:  # проверяем последние 10
                    try:
                        message_text = message_element.text.strip()
                        if message_text and len(message_text) > 10:  # фильтруем короткие тексты

                            # создаем уникальный id
                            message_id = hash(message_text[:100])

                            if message_id not in self.processed_messages:
                                # отправляем в телеграм
                                await self.bot.send_message(
                                    chat_id=TELEGRAM_CHAT_ID,
                                    text=f"📩 <b>новое сообщение в max</b>\n\n{message_text}"
                                )
                                logger.info(f"📤 отправил сообщение: {message_text[:50]}...")
                                self.processed_messages.add(message_id)

                    except Exception as e:
                        continue

                # ограничиваем размер истории
                if len(self.processed_messages) > 100:
                    self.processed_messages = set(list(self.processed_messages)[-50:])

                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"❌ ошибка мониторинга: {e}")
                await asyncio.sleep(5)

    # команды бота
    async def cmd_start(self, message: Message):
        """запуск мониторинга"""
        if self.is_monitoring:
            await message.answer("✅ мониторинг уже запущен")
            return

        await message.answer("🔄 запускаю мониторинг max...")

        self.driver = self.setup_driver()
        if not self.driver:
            await message.answer("❌ ошибка запуска браузера")
            return

        result = await self.login_to_max()

        if result == "success":
            self.is_monitoring = True
            asyncio.create_task(self.monitor_messages())
            await message.answer("✅ мониторинг запущен! новые сообщения будут пересылаться сюда")
        elif result == "awaiting_code":
            await message.answer("📋 жду sms код. отправьте /code 123456")
        else:
            await message.answer("❌ ошибка входа. проверьте логи")

    async def cmd_stop(self, message: Message):
        """остановка мониторинга"""
        self.is_monitoring = False
        if self.driver:
            self.driver.quit()
            self.driver = None
        self.processed_messages.clear()
        await message.answer("⏹ мониторинг остановлен")

    async def cmd_status(self, message: Message):
        """статус работы"""
        status = "🟢 активен" if self.is_monitoring else "🔴 остановлен"
        await message.answer(f"{status}\nсообщений: {len(self.processed_messages)}")

    async def cmd_code(self, message: Message):
        """ввод sms кода"""
        if not self.awaiting_verification:
            await message.answer("❌ код подтверждения не требуется")
            return

        code = message.text.replace('/code', '').strip()

        if not code.isdigit() or len(code) != 6:
            await message.answer("❌ неверный формат кода. пример: /code 123456")
            return

        await message.answer(f"🔄 ввожу код {code}...")
        success = await self.enter_sms_code(code)

        if success:
            self.is_monitoring = True
            asyncio.create_task(self.monitor_messages())
            await message.answer("✅ код принят! мониторинг запущен")
        else:
            await message.answer("❌ ошибка ввода кода. попробуйте еще раз")

    async def run(self):
        """запуск бота"""
        logger.info("🤖 запуск телеграм бота")
        await self.dp.start_polling(self.bot)


async def main():
    bot = MaxMonitorBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())