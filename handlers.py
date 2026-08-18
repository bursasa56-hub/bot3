import logging

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import database as db
from keyboards import (
    BTN_ADD,
    BTN_LIST,
    cancel_keyboard,
    main_reply_keyboard,
    start_inline_keyboard,
    subscriptions_keyboard,
)
from tiktok import escape, get_latest_videos, get_user, parse_username, video_url

logger = logging.getLogger(__name__)
router = Router()


class AddAccount(StatesGroup):
    waiting_username = State()


START_TEXT = (
    "👋 Привет!\n\n"
    "Я бот, который следит за новыми видео в TikTok и сразу присылает "
    "уведомление, когда у выбранного человека выходит ролик.\n\n"
    "Как пользоваться:\n"
    "1. Нажми «Добавить человека»\n"
    "2. Отправь юзернейм аккаунта — например, <code>khaby.lame</code> "
    "или <code>@khaby.lame</code>\n"
    "3. Как только выйдет новое видео, я напишу тебе\n\n"
    "Юзернейм можно просто скинуть в чат в любой момент.\n"
    "Аккаунт должен быть открытым."
)

ASK_USERNAME = (
    "Отправь юзернейм TikTok-аккаунта.\n"
    "Например: <code>khaby.lame</code> или <code>@khaby.lame</code>"
)


def _people_word(count: int) -> str:
    last_two = count % 100
    last = count % 10
    if 11 <= last_two <= 14:
        return "человек"
    if last == 1:
        return "человек"
    if 2 <= last <= 4:
        return "человека"
    return "человек"


def _subscriptions_text(items: list[tuple[str, str | None]]) -> str:
    count = len(items)
    if count == 0:
        return (
            "📋 У тебя пока никого нет.\n\n"
            "Нажми «Добавить человека» и скинь юзернейм из TikTok."
        )

    lines = [f"📋 У тебя добавлено {count} {_people_word(count)}:\n"]
    for index, (username, nickname) in enumerate(items, start=1):
        if nickname and nickname.lower() != username:
            lines.append(f"{index}. {escape(nickname)} (@{username})")
        else:
            lines.append(f"{index}. @{username}")
    return "\n".join(lines)


async def _show_subscriptions(target: Message | CallbackQuery) -> None:
    telegram_id = target.from_user.id
    items = await db.list_subscriptions(telegram_id)
    text = _subscriptions_text(items)
    markup = subscriptions_keyboard(items) if items else start_inline_keyboard()

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


async def _add_account(message: Message, raw_text: str, http: aiohttp.ClientSession) -> bool:
    username = parse_username(raw_text)
    if not username:
        await message.answer(
            "Не похоже на юзернейм TikTok.\n"
            "Пришли так: <code>khaby.lame</code> или <code>@khaby.lame</code>"
        )
        return False

    existing = await db.list_subscriptions(message.from_user.id)
    already = any(item[0] == username for item in existing)
    if already:
        await message.answer(f"@{username} уже есть в твоём списке.")
        return True

    wait = await message.answer(f"Ищу аккаунт @{username}…")
    user = await get_user(http, username)

    if user is None:
        await wait.edit_text(
            f"Не нашёл аккаунт @{username}. Проверь юзернейм и попробуй ещё раз."
        )
        return False

    videos = await get_latest_videos(http, user.username)
    video_ids = [video.video_id for video in videos] if videos else []
    created = await db.add_subscription(
        message.from_user.id,
        user.username,
        user.nickname,
        video_ids,
        user.sec_uid,
    )
    final_username = user.username
    nickname = user.nickname

    if not created:
        await wait.edit_text(f"@{final_username} уже есть в твоём списке.")
        return True

    extra = (
        f" ({escape(nickname)})"
        if nickname and nickname.lower() != final_username
        else ""
    )
    text = (
        f"Готово! Слежу за @{final_username}{extra}.\n"
        "Пришлю сообщение, как только выйдет новый ролик."
    )
    if videos:
        last = videos[0]
        title = escape(last.title) if last.title else "без описания"
        text += (
            f"\n\nСейчас последний ролик:\n"
            f"{title}\n"
            f"{video_url(final_username, last.video_id)}"
        )
    await wait.edit_text(text)
    return True


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(START_TEXT, reply_markup=start_inline_keyboard())
    await message.answer("Выбери действие:", reply_markup=main_reply_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(START_TEXT, reply_markup=start_inline_keyboard())
    await message.answer("Выбери действие:", reply_markup=main_reply_keyboard())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=main_reply_keyboard())


@router.callback_query(F.data == "add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAccount.waiting_username)
    await callback.message.answer(ASK_USERNAME, reply_markup=cancel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "list")
async def cb_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_subscriptions(callback)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Выбери действие:", reply_markup=start_inline_keyboard())
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Добавление отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("copyuser:"))
async def cb_copy_username(callback: CallbackQuery) -> None:
    username = callback.data.split(":", 1)[1]
    await callback.message.answer(
        f"Юзернейм: <code>{escape(username)}</code>\n"
        "Нажми на него — скопируется."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("unsub:"))
async def cb_unsub(callback: CallbackQuery) -> None:
    username = callback.data.split(":", 1)[1].lower()
    removed = await db.remove_subscription(callback.from_user.id, username)
    if removed:
        await callback.answer(f"@{username} удалён")
    else:
        await callback.answer("Этого аккаунта уже нет")
    await _show_subscriptions(callback)


@router.message(F.text == BTN_ADD)
async def btn_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddAccount.waiting_username)
    await message.answer(ASK_USERNAME, reply_markup=cancel_keyboard())


@router.message(F.text == BTN_LIST)
async def btn_list(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_subscriptions(message)


@router.message(AddAccount.waiting_username, F.text)
async def got_username(
    message: Message,
    state: FSMContext,
    http: aiohttp.ClientSession,
) -> None:
    ok = await _add_account(message, message.text, http)
    if ok:
        await state.clear()


@router.message(F.text)
async def any_text(
    message: Message,
    state: FSMContext,
    http: aiohttp.ClientSession,
) -> None:
    if parse_username(message.text):
        await _add_account(message, message.text, http)
        await state.clear()
        return

    await message.answer(
        "Не понял сообщение.\n"
        "Нажми «Добавить человека» или просто скинь юзернейм из TikTok.",
        reply_markup=start_inline_keyboard(),
    )
