from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_ADD = "➕ Добавить человека"
BTN_LIST = "📋 Мои подписки"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_LIST)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери кнопку или скинь юзернейм",
    )


def start_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_ADD, callback_data="add")],
            [InlineKeyboardButton(text=BTN_LIST, callback_data="list")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        ]
    )


def notification_keyboard(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Скопировать юзернейм",
                    copy_text=CopyTextButton(text=username),
                )
            ]
        ]
    )


def subscriptions_keyboard(
    items: list[tuple[str, str | None]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for username, _nickname in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Удалить @{username}",
                    callback_data=f"unsub:{username}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
