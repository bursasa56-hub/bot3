import asyncio
import logging
import os
import socket
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from checker import poll_loop
from config import BOT_TOKEN, CHECK_INTERVAL, TELEGRAM_PROXY
from database import init_db
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _make_session() -> AiohttpSession:
    session = AiohttpSession(
        proxy=TELEGRAM_PROXY or None,
        timeout=60,
        limit=20,
    )
    session._connector_init["family"] = socket.AF_INET
    return session


async def main() -> None:
    if not BOT_TOKEN:
        logger.error("Укажи BOT_TOKEN в файле .env")
        sys.exit(1)

    await init_db()

    session = _make_session()
    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    timeout = aiohttp.ClientTimeout(total=25)
    if TELEGRAM_PROXY:
        from aiohttp_socks import ProxyConnector

        connector = ProxyConnector.from_url(TELEGRAM_PROXY)
    else:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:
        checker_task = asyncio.create_task(poll_loop(bot, http, CHECK_INTERVAL))
        try:
            if TELEGRAM_PROXY:
                logger.info("Подключаюсь к Telegram через прокси")
            else:
                logger.info("Подключаюсь к Telegram")

            while True:
                try:
                    logger.info("Бот запущен")
                    await dispatcher.start_polling(bot, http=http)
                    break
                except TelegramNetworkError as exc:
                    logger.error("Ошибка сети: %s", exc)
                    if os.getenv("RAILWAY_ENVIRONMENT"):
                        logger.error("Нет доступа к Telegram API, повтор через 15 секунд")
                    else:
                        logger.error(
                            "Нет доступа к api.telegram.org. "
                            "Включи системный VPN или укажи TELEGRAM_PROXY в .env."
                        )
                    await asyncio.sleep(15)
        finally:
            checker_task.cancel()
            try:
                await checker_task
            except asyncio.CancelledError:
                pass
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
