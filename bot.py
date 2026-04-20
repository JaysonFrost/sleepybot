import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import Chat, Message, Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


CONFIG_PATH = Path("bot_config.json")


@dataclass
class RuntimeConfig:
    owner_user_id: int | None = None
    target_chat_id: int | None = None
    monitored_chats: dict[int, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=lambda: ["alert"])
    case_sensitive: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeConfig":
        monitored_source = data.get("monitored_chats", {})
        monitored_chats: dict[int, str] = {}
        for chat_id, title in monitored_source.items():
            monitored_chats[int(chat_id)] = str(title)

        return cls(
            owner_user_id=data.get("owner_user_id"),
            target_chat_id=data.get("target_chat_id"),
            monitored_chats=monitored_chats,
            keywords=[str(k).strip() for k in data.get("keywords", []) if str(k).strip()],
            case_sensitive=bool(data.get("case_sensitive", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_user_id": self.owner_user_id,
            "target_chat_id": self.target_chat_id,
            "monitored_chats": {str(k): v for k, v in self.monitored_chats.items()},
            "keywords": self.keywords,
            "case_sensitive": self.case_sensitive,
        }


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("keyword-monitor-bot")


def load_token() -> str:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Не найден BOT_TOKEN. Добавьте токен в .env")
    return token


def load_config() -> RuntimeConfig:
    if not CONFIG_PATH.exists():
        return RuntimeConfig()

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    config = RuntimeConfig.from_dict(data)
    if not config.keywords:
        config.keywords = ["alert"]
    return config


def save_config(config: RuntimeConfig) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)


def is_owner(update: Update, config: RuntimeConfig) -> bool:
    user = update.effective_user
    return bool(user and config.owner_user_id and user.id == config.owner_user_id)


def extract_forwarded_chat(message: Message) -> Chat | None:
    if message.forward_from_chat:
        return message.forward_from_chat

    origin = getattr(message, "forward_origin", None)
    if origin and getattr(origin, "chat", None):
        return origin.chat

    return None


def find_matched_keywords(text: str, keywords: list[str], case_sensitive: bool) -> list[str]:
    if case_sensitive:
        normalized_text = text
        pairs = [(k, k) for k in keywords]
    else:
        normalized_text = text.lower()
        pairs = [(k, k.lower()) for k in keywords]

    return [original for original, needle in pairs if needle in normalized_text]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    config: RuntimeConfig = context.application.bot_data["config"]

    if config.owner_user_id is None:
        config.owner_user_id = user.id
        save_config(config)
        await message.reply_text(
            "✅ Вы стали владельцем бота. Теперь только вы можете менять настройки."
        )

    await message.reply_text(
        "Команды:\n"
        "/bind_target — привязать чат/канал для уведомлений (перешлите сообщение)\n"
        "/bind_watch — добавить чат для мониторинга (перешлите сообщение)\n"
        "/unbind_watch — удалить чат из мониторинга (перешлите сообщение)\n"
        "/keywords слово1,слово2 — задать ключевые слова\n"
        "/list — показать текущие настройки\n"
        "/chatid — показать ID текущего чата"
    )


async def chat_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return

    await message.reply_text(f"chat_id: {chat.id}\ntitle: {chat.title or '-'}\ntype: {chat.type}")


async def bind_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return

    if not is_owner(update, config):
        await message.reply_text("Только владелец бота может менять настройки.")
        return

    context.user_data["pending_action"] = "bind_target"
    await message.reply_text(
        "Перешлите сюда любое сообщение из чата/канала, куда бот должен отправлять уведомления."
    )


async def bind_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return

    if not is_owner(update, config):
        await message.reply_text("Только владелец бота может менять настройки.")
        return

    context.user_data["pending_action"] = "bind_watch"
    await message.reply_text(
        "Перешлите сюда любое сообщение из чата/канала, который нужно мониторить."
    )


async def unbind_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return

    if not is_owner(update, config):
        await message.reply_text("Только владелец бота может менять настройки.")
        return

    context.user_data["pending_action"] = "unbind_watch"
    await message.reply_text(
        "Перешлите сообщение из чата/канала, который нужно убрать из мониторинга."
    )


async def set_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return

    if not is_owner(update, config):
        await message.reply_text("Только владелец бота может менять настройки.")
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Пример: /keywords скидка,распродажа,alert")
        return

    keywords = [item.strip() for item in parts[1].split(",") if item.strip()]
    if not keywords:
        await message.reply_text("Список ключевых слов пуст.")
        return

    config.keywords = keywords
    save_config(config)
    await message.reply_text(f"✅ Ключевые слова обновлены: {', '.join(config.keywords)}")


async def list_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return

    if not is_owner(update, config):
        await message.reply_text("Только владелец бота может смотреть настройки.")
        return

    monitored = (", ".join(f"{title} ({chat_id})" for chat_id, title in config.monitored_chats.items())
                 if config.monitored_chats else "нет")

    await message.reply_text(
        "Текущие настройки:\n"
        f"owner_user_id: {config.owner_user_id}\n"
        f"target_chat_id: {config.target_chat_id}\n"
        f"monitoring: {monitored}\n"
        f"keywords: {', '.join(config.keywords)}\n"
        f"case_sensitive: {config.case_sensitive}"
    )


async def process_pending_bind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending_action = context.user_data.get("pending_action")
    if not pending_action:
        return False

    config: RuntimeConfig = context.application.bot_data["config"]
    message = update.effective_message
    if not message:
        return False

    if not is_owner(update, config):
        context.user_data.pop("pending_action", None)
        await message.reply_text("Настройка отменена: недостаточно прав.")
        return True

    forwarded_chat = extract_forwarded_chat(message)
    if not forwarded_chat:
        await message.reply_text("Не вижу пересланный чат. Перешлите сообщение из нужного чата/канала.")
        return True

    chat_id = forwarded_chat.id
    title = forwarded_chat.title or forwarded_chat.full_name or "Без названия"

    if pending_action == "bind_target":
        config.target_chat_id = chat_id
        context.user_data.pop("pending_action", None)
        save_config(config)
        await message.reply_text(f"✅ Канал/чат для уведомлений установлен: {title} ({chat_id})")
        return True

    if pending_action == "bind_watch":
        config.monitored_chats[chat_id] = title
        context.user_data.pop("pending_action", None)
        save_config(config)
        await message.reply_text(f"✅ Чат добавлен в мониторинг: {title} ({chat_id})")
        return True

    if pending_action == "unbind_watch":
        removed = config.monitored_chats.pop(chat_id, None)
        context.user_data.pop("pending_action", None)
        save_config(config)
        if removed:
            await message.reply_text(f"✅ Чат удален из мониторинга: {title} ({chat_id})")
        else:
            await message.reply_text(f"Чат {title} ({chat_id}) не был в мониторинге.")
        return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    if await process_pending_bind(update, context):
        return

    config: RuntimeConfig = context.application.bot_data["config"]
    chat_id = update.effective_chat.id

    if chat_id not in config.monitored_chats:
        return

    if config.target_chat_id is None:
        return

    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    if not text:
        return

    matches = find_matched_keywords(text, config.keywords, config.case_sensitive)
    if not matches:
        return

    chat_title = update.effective_chat.title or update.effective_chat.full_name or "Личный чат"
    msg_link = message.link if update.effective_chat.type in {ChatType.SUPERGROUP, ChatType.CHANNEL} else None

    lines = [
        "🔔 Найдено ключевое слово",
        f"Чат: {chat_title} ({chat_id})",
        f"Совпадения: {', '.join(matches)}",
        f"Текст: {text[:3000]}",
    ]
    if msg_link:
        lines.append(f"Ссылка: {msg_link}")

    await context.bot.send_message(
        chat_id=config.target_chat_id,
        text="\n".join(lines),
        disable_web_page_preview=True,
    )


async def run() -> None:
    token = load_token()
    config = load_config()

    application = Application.builder().token(token).build()
    application.bot_data["config"] = config

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chatid", chat_info))
    application.add_handler(CommandHandler("bind_target", bind_target))
    application.add_handler(CommandHandler("bind_watch", bind_watch))
    application.add_handler(CommandHandler("unbind_watch", unbind_watch))
    application.add_handler(CommandHandler("keywords", set_keywords))
    application.add_handler(CommandHandler("list", list_settings))
    application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    logger.info("Запуск бота")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка бота")
