import asyncio
import logging
import time

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import URLInputFile

import database as db
from config import TELEGRAM_PROXY
from keyboards import notification_keyboard
from tiktok import escape, fetch_videos_fast, video_url

logger = logging.getLogger(__name__)

KEEP_VIDEO_IDS = 20


def _format_notification(username: str, title: str, nickname: str | None) -> str:
    safe_user = escape(username)
    who = (
        f"{escape(nickname)} (<code>{safe_user}</code>)"
        if nickname
        else f"<code>{safe_user}</code>"
    )
    caption = escape(title.strip()) if title else "Новый ролик"
    if len(caption) > 400:
        caption = caption[:397] + "..."
    return (
        f"🎬 Новое видео в TikTok\n\n"
        f"👤 {who}\n"
        f"📝 {caption}\n\n"
        f"🔗 {video_url(username, '__ID__')}"
    )


async def _notify(
    bot: Bot,
    telegram_id: int,
    username: str,
    video_id: str,
    title: str,
    nickname: str | None,
    cover: str | None,
) -> None:
    text = _format_notification(username, title, nickname).replace(
        "__ID__",
        video_id,
    )
    markup = notification_keyboard(username)
    try:
        if cover:
            await bot.send_photo(
                telegram_id,
                photo=URLInputFile(cover),
                caption=text,
                reply_markup=markup,
            )
        else:
            await bot.send_message(telegram_id, text, reply_markup=markup)
    except TelegramForbiddenError:
        logger.info("User %s blocked the bot", telegram_id)
    except TelegramBadRequest as exc:
        logger.warning("Failed to notify %s with photo: %s", telegram_id, exc)
        try:
            await bot.send_message(telegram_id, text, reply_markup=markup)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.info("Could not notify user %s", telegram_id)


async def check_accounts(bot: Bot, http: aiohttp.ClientSession) -> None:
    accounts = await db.get_tracked_accounts()
    if not accounts:
        return

    for username, known_ids, sec_uid in accounts:
        try:
            videos = await asyncio.to_thread(
                fetch_videos_fast,
                username,
                sec_uid,
                TELEGRAM_PROXY or None,
            )
            if not videos:
                logger.warning("No video list for @%s", username)
                continue
            videos.sort(key=lambda item: item.create_time, reverse=True)

            known = set(known_ids)
            if not known_ids:
                newest = videos[0] if videos else None
                recent = bool(
                    newest
                    and newest.create_time
                    and newest.create_time >= int(time.time()) - 30 * 60
                )
                await db.update_account_videos(
                    username,
                    [video.video_id for video in videos[:KEEP_VIDEO_IDS]] or ["__init__"],
                    newest.nickname if newest else None,
                )
                if recent and newest:
                    subscribers = await db.get_subscribers(username)
                    for telegram_id in subscribers:
                        await _notify(
                            bot,
                            telegram_id,
                            username,
                            newest.video_id,
                            newest.title,
                            newest.nickname,
                            newest.cover,
                        )
                continue

            fresh = [video for video in videos if video.video_id not in known]
            fresh.sort(key=lambda item: item.create_time)

            if fresh:
                logger.info("New videos for @%s: %s", username, [item.video_id for item in fresh])
                subscribers = await db.get_subscribers(username)
                for video in fresh:
                    for telegram_id in subscribers:
                        await _notify(
                            bot,
                            telegram_id,
                            username,
                            video.video_id,
                            video.title,
                            video.nickname,
                            video.cover,
                        )
                        await asyncio.sleep(0.05)

            merged = [video.video_id for video in videos] + [
                video_id for video_id in known_ids if video_id != "__init__"
            ]
            unique_ids: list[str] = []
            for video_id in merged:
                if video_id not in unique_ids:
                    unique_ids.append(video_id)
            await db.update_account_videos(
                username,
                unique_ids[:KEEP_VIDEO_IDS],
                videos[0].nickname if videos else None,
            )
        except Exception:
            logger.exception("Failed to check @%s", username)

        await asyncio.sleep(0.05)


async def poll_loop(bot: Bot, http: aiohttp.ClientSession, interval: int) -> None:
    while True:
        started = time.monotonic()
        try:
            await check_accounts(bot, http)
        except Exception:
            logger.exception("Video check cycle failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.5, interval - elapsed))
