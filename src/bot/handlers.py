"""Telegram bot handlers for n8n Updater."""

import asyncio
import functools
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from ..storage import get_storage, Server
from ..version_checker import get_latest_version, compare_versions
from ..ssh_executor import SSHExecutor, get_server_status, UpdateResult
from .keyboards import (
    get_main_menu,
    get_servers_menu,
    get_servers_list_keyboard,
    get_server_details_keyboard,
    get_confirm_delete_keyboard,
    get_auth_type_keyboard,
    get_servers_keyboard,
    get_time_keyboard,
    get_back_keyboard,
    get_confirm_update_keyboard,
    get_settings_keyboard,
    get_interval_keyboard,
    get_skip_keyboard,
    get_cancel_keyboard,
)

if TYPE_CHECKING:
    from ..scheduler import UpdateScheduler

logger = logging.getLogger(__name__)

router = Router()

# Global reference to scheduler (set in main.py)
_scheduler: "UpdateScheduler | None" = None


def set_scheduler(scheduler: "UpdateScheduler"):
    """Set the global scheduler reference."""
    global _scheduler
    _scheduler = scheduler


class AddServerStates(StatesGroup):
    """FSM states for adding a server."""
    waiting_name = State()
    waiting_host = State()
    waiting_port = State()
    waiting_user = State()
    waiting_auth_type = State()
    waiting_password = State()
    waiting_key_path = State()
    waiting_n8n_path = State()


class UpdateStates(StatesGroup):
    """FSM states for update flow."""
    selecting_servers = State()
    selecting_time = State()


class SettingsStates(StatesGroup):
    """FSM states for settings."""
    waiting_interval = State()


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    storage = get_storage()
    admin_id = storage.get_admin_chat_id()
    # If no admin set, anyone can be admin (first user becomes admin)
    if admin_id is None:
        return True
    return user_id == admin_id


def admin_only(handler):
    """Decorator to restrict handler to admin only."""
    @functools.wraps(handler)
    async def wrapper(event: Message | CallbackQuery, **kwargs):
        user_id = event.from_user.id if event.from_user else 0
        if not is_admin(user_id):
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён. Вы не являетесь администратором.")
            else:
                await event.answer("⛔ Доступ запрещён", show_alert=True)
            return
        return await handler(event, **kwargs)
    return wrapper


# ============= Command Handlers =============

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command."""
    user_id = message.from_user.id if message.from_user else 0
    user_name = message.from_user.full_name if message.from_user else "Unknown"
    
    storage = get_storage()
    admin_id = storage.get_admin_chat_id()
    
    # First user becomes admin
    if admin_id is None:
        storage.set_admin_chat_id(user_id)
        logger.info(f"Admin set to user {user_id} ({user_name})")
        
        await message.answer(
            f"👋 Привет, {user_name}!\n\n"
            "Ты первый пользователь — теперь ты администратор этого бота! 🎉\n\n"
            "Давай начнём с добавления твоего первого сервера.",
            reply_markup=get_servers_menu()
        )
        return
    
    if user_id == admin_id:
        server_count = storage.server_count()
        has_servers = server_count > 0
        
        if has_servers:
            await message.answer(
                f"👋 С возвращением, {user_name}!\n\n"
                f"У тебя {server_count} сервер(ов) под управлением.\n\n"
                "Что будем делать?",
                reply_markup=get_main_menu(has_servers=True)
            )
        else:
            await message.answer(
                f"👋 Привет, {user_name}!\n\n"
                "У тебя пока нет серверов. Давай добавим первый!",
                reply_markup=get_servers_menu()
            )
    else:
        await message.answer(
            "⛔ Этот бот уже настроен и принадлежит другому пользователю."
        )


@router.message(Command("status"))
@admin_only
async def cmd_status(message: Message):
    """Handle /status command - show server statuses."""
    await show_status(message)


@router.message(Command("check"))
@admin_only
async def cmd_check(message: Message):
    """Handle /check command - check for updates."""
    await check_updates(message)


@router.message(Command("update"))
@admin_only
async def cmd_update(message: Message, state: FSMContext):
    """Handle /update command - start update flow."""
    await start_update_flow(message, state)


@router.message(Command("servers"))
@admin_only
async def cmd_servers(message: Message):
    """Handle /servers command - manage servers."""
    await message.answer(
        "🖥 *Управление серверами*",
        parse_mode="Markdown",
        reply_markup=get_servers_menu()
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    await message.answer(
        "📖 *Доступные команды:*\n\n"
        "/start - Главное меню\n"
        "/status - Статус серверов\n"
        "/check - Проверить обновления\n"
        "/update - Обновить серверы\n"
        "/servers - Управление серверами\n"
        "/help - Эта справка\n",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )


# ============= Main Menu Callbacks =============

@router.callback_query(F.data == "menu")
@admin_only
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    """Return to main menu."""
    await state.clear()
    storage = get_storage()
    has_servers = storage.server_count() > 0
    
    await callback.message.edit_text(
        "🏠 *Главное меню*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=get_main_menu(has_servers=has_servers)
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel current operation."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Операция отменена.",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "status")
@admin_only
async def cb_status(callback: CallbackQuery):
    """Show server statuses."""
    await callback.answer()
    await show_status(callback.message, edit=True)


@router.callback_query(F.data == "check")
@admin_only
async def cb_check(callback: CallbackQuery):
    """Check for updates."""
    await callback.answer()
    await check_updates(callback.message, edit=True)


@router.callback_query(F.data == "update_menu")
@admin_only
async def cb_update_menu(callback: CallbackQuery, state: FSMContext):
    """Start update flow."""
    await callback.answer()
    await start_update_flow(callback.message, state, edit=True)


# ============= Server Management Callbacks =============

@router.callback_query(F.data == "servers_menu")
@admin_only
async def cb_servers_menu(callback: CallbackQuery, state: FSMContext):
    """Show servers management menu."""
    await state.clear()
    await callback.message.edit_text(
        "🖥 *Управление серверами*\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=get_servers_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "list_servers")
@admin_only
async def cb_list_servers(callback: CallbackQuery):
    """List all servers."""
    storage = get_storage()
    servers = storage.get_all_servers()
    
    if not servers:
        await callback.message.edit_text(
            "📋 *Список серверов*\n\nУ тебя пока нет серверов.",
            parse_mode="Markdown",
            reply_markup=get_servers_menu()
        )
    else:
        await callback.message.edit_text(
            f"📋 *Список серверов* ({len(servers)})\n\nВыбери сервер для управления:",
            parse_mode="Markdown",
            reply_markup=get_servers_list_keyboard(servers)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("server_details:"))
@admin_only
async def cb_server_details(callback: CallbackQuery):
    """Show server details."""
    server_id = int(callback.data.split(":")[1])
    storage = get_storage()
    server = storage.get_server(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    auth_info = "🔑 Пароль" if server.auth_type == "password" else f"🔐 SSH ключ"
    
    await callback.message.edit_text(
        f"🖥 *{server.name}*\n\n"
        f"**Хост:** `{server.host}:{server.port}`\n"
        f"**Пользователь:** `{server.user}`\n"
        f"**Авторизация:** {auth_info}\n"
        f"**Путь n8n:** `{server.n8n_path}`",
        parse_mode="Markdown",
        reply_markup=get_server_details_keyboard(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("test_server:"))
@admin_only
async def cb_test_server(callback: CallbackQuery):
    """Test server connection."""
    server_id = int(callback.data.split(":")[1])
    storage = get_storage()
    server = storage.get_server(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.answer("Проверяю подключение...")
    
    await callback.message.edit_text(
        f"🔄 Проверяю подключение к {server.name}...",
        parse_mode="Markdown"
    )
    
    executor = SSHExecutor(server)
    success, message = await executor.test_connection()
    
    if success:
        # Also check n8n
        version = await executor.get_current_version()
        running = await executor.check_n8n_running()
        
        status_emoji = "🟢" if running else "🔴"
        version_text = f"v{version}" if version else "неизвестна"
        
        await callback.message.edit_text(
            f"✅ *Подключение успешно!*\n\n"
            f"**Сервер:** {server.name}\n"
            f"**Статус n8n:** {status_emoji} {'Работает' if running else 'Не запущен'}\n"
            f"**Версия:** {version_text}",
            parse_mode="Markdown",
            reply_markup=get_server_details_keyboard(server_id)
        )
    else:
        await callback.message.edit_text(
            f"❌ *Ошибка подключения*\n\n"
            f"**Сервер:** {server.name}\n"
            f"**Ошибка:** {message}",
            parse_mode="Markdown",
            reply_markup=get_server_details_keyboard(server_id)
        )


@router.callback_query(F.data.startswith("delete_server:"))
@admin_only
async def cb_delete_server(callback: CallbackQuery):
    """Confirm server deletion."""
    server_id = int(callback.data.split(":")[1])
    storage = get_storage()
    server = storage.get_server(server_id)
    
    if not server:
        await callback.answer("Сервер не найден", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"⚠️ *Удаление сервера*\n\n"
        f"Ты уверен, что хочешь удалить **{server.name}**?\n\n"
        f"Это действие нельзя отменить.",
        parse_mode="Markdown",
        reply_markup=get_confirm_delete_keyboard(server_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete:"))
@admin_only
async def cb_confirm_delete(callback: CallbackQuery):
    """Execute server deletion."""
    server_id = int(callback.data.split(":")[1])
    storage = get_storage()
    server = storage.get_server(server_id)
    
    if server:
        storage.delete_server(server_id)
        await callback.message.edit_text(
            f"✅ Сервер **{server.name}** удалён.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
    else:
        await callback.answer("Сервер не найден", show_alert=True)


# ============= Add Server Flow =============

@router.callback_query(F.data == "add_server")
@admin_only
async def cb_add_server(callback: CallbackQuery, state: FSMContext):
    """Start add server flow."""
    await state.set_state(AddServerStates.waiting_name)
    await callback.message.edit_text(
        "➕ *Добавление сервера*\n\n"
        "Шаг 1/6: Введи имя сервера (например: Production, Staging):",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await callback.answer()


@router.message(AddServerStates.waiting_name)
async def process_server_name(message: Message, state: FSMContext):
    """Process server name input."""
    name = message.text.strip()
    
    if len(name) < 2:
        await message.answer("Имя должно быть не менее 2 символов. Попробуй ещё раз:")
        return
    
    storage = get_storage()
    if storage.get_server_by_name(name):
        await message.answer("Сервер с таким именем уже существует. Введи другое имя:")
        return
    
    await state.update_data(name=name)
    await state.set_state(AddServerStates.waiting_host)
    
    await message.answer(
        f"✅ Имя: **{name}**\n\n"
        "Шаг 2/6: Введи IP-адрес или hostname сервера:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )


@router.message(AddServerStates.waiting_host)
async def process_server_host(message: Message, state: FSMContext):
    """Process server host input."""
    host = message.text.strip()
    
    if not host:
        await message.answer("Введи корректный IP-адрес или hostname:")
        return
    
    await state.update_data(host=host)
    await state.set_state(AddServerStates.waiting_port)
    
    await message.answer(
        f"✅ Хост: **{host}**\n\n"
        "Шаг 3/6: Введи SSH порт (или пропусти для порта 22):",
        parse_mode="Markdown",
        reply_markup=get_skip_keyboard("port")
    )


@router.callback_query(F.data == "skip:port", AddServerStates.waiting_port)
async def skip_port(callback: CallbackQuery, state: FSMContext):
    """Skip port, use default."""
    await state.update_data(port=22)
    await state.set_state(AddServerStates.waiting_user)
    
    await callback.message.edit_text(
        "✅ Порт: **22** (по умолчанию)\n\n"
        "Шаг 4/6: Введи имя пользователя SSH (или пропусти для root):",
        parse_mode="Markdown",
        reply_markup=get_skip_keyboard("user")
    )
    await callback.answer()


@router.message(AddServerStates.waiting_port)
async def process_server_port(message: Message, state: FSMContext):
    """Process server port input."""
    try:
        port = int(message.text.strip())
        if port < 1 or port > 65535:
            raise ValueError()
    except ValueError:
        await message.answer("Введи корректный номер порта (1-65535):")
        return
    
    await state.update_data(port=port)
    await state.set_state(AddServerStates.waiting_user)
    
    await message.answer(
        f"✅ Порт: **{port}**\n\n"
        "Шаг 4/6: Введи имя пользователя SSH (или пропусти для root):",
        parse_mode="Markdown",
        reply_markup=get_skip_keyboard("user")
    )


@router.callback_query(F.data == "skip:user", AddServerStates.waiting_user)
async def skip_user(callback: CallbackQuery, state: FSMContext):
    """Skip user, use default."""
    await state.update_data(user="root")
    await state.set_state(AddServerStates.waiting_auth_type)
    
    await callback.message.edit_text(
        "✅ Пользователь: **root** (по умолчанию)\n\n"
        "Шаг 5/6: Выбери способ авторизации:",
        parse_mode="Markdown",
        reply_markup=get_auth_type_keyboard()
    )
    await callback.answer()


@router.message(AddServerStates.waiting_user)
async def process_server_user(message: Message, state: FSMContext):
    """Process server user input."""
    user = message.text.strip()
    
    if not user:
        await message.answer("Введи имя пользователя:")
        return
    
    await state.update_data(user=user)
    await state.set_state(AddServerStates.waiting_auth_type)
    
    await message.answer(
        f"✅ Пользователь: **{user}**\n\n"
        "Шаг 5/6: Выбери способ авторизации:",
        parse_mode="Markdown",
        reply_markup=get_auth_type_keyboard()
    )


@router.callback_query(F.data.startswith("auth_type:"), AddServerStates.waiting_auth_type)
async def process_auth_type(callback: CallbackQuery, state: FSMContext):
    """Process auth type selection."""
    auth_type = callback.data.split(":")[1]
    await state.update_data(auth_type=auth_type)
    
    if auth_type == "password":
        await state.set_state(AddServerStates.waiting_password)
        await callback.message.edit_text(
            "✅ Авторизация: **пароль**\n\n"
            "Введи пароль SSH:",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
    else:
        await state.set_state(AddServerStates.waiting_key_path)
        await callback.message.edit_text(
            "✅ Авторизация: **SSH ключ**\n\n"
            "Введи путь к SSH ключу на сервере где запущен этот бот\n"
            "(например: `/root/.ssh/id_rsa`):",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
    await callback.answer()


@router.message(AddServerStates.waiting_password)
async def process_server_password(message: Message, state: FSMContext):
    """Process server password input."""
    password = message.text.strip()
    
    # Delete the message with password for security
    try:
        await message.delete()
    except Exception:
        pass
    
    if not password:
        await message.answer("Введи пароль:")
        return
    
    await state.update_data(ssh_password=password, ssh_key_path=None)
    await state.set_state(AddServerStates.waiting_n8n_path)
    
    await message.answer(
        "✅ Пароль сохранён\n\n"
        "Шаг 6/6: Введи путь к папке с docker-compose n8n\n"
        "(или пропусти для `/opt/n8n-docker-caddy`):",
        parse_mode="Markdown",
        reply_markup=get_skip_keyboard("n8n_path")
    )


@router.message(AddServerStates.waiting_key_path)
async def process_key_path(message: Message, state: FSMContext):
    """Process SSH key path input."""
    key_path = message.text.strip()
    
    if not key_path.startswith("/"):
        await message.answer("Путь должен быть абсолютным (начинаться с /):")
        return
    
    await state.update_data(ssh_key_path=key_path, ssh_password=None)
    await state.set_state(AddServerStates.waiting_n8n_path)
    
    await message.answer(
        f"✅ SSH ключ: `{key_path}`\n\n"
        "Шаг 6/6: Введи путь к папке с docker-compose n8n\n"
        "(или пропусти для `/opt/n8n-docker-caddy`):",
        parse_mode="Markdown",
        reply_markup=get_skip_keyboard("n8n_path")
    )


@router.callback_query(F.data == "skip:n8n_path", AddServerStates.waiting_n8n_path)
async def skip_n8n_path(callback: CallbackQuery, state: FSMContext):
    """Skip n8n path, use default."""
    await state.update_data(n8n_path="/opt/n8n-docker-caddy")
    await finish_add_server(callback.message, state, edit=True)
    await callback.answer()


@router.message(AddServerStates.waiting_n8n_path)
async def process_n8n_path(message: Message, state: FSMContext):
    """Process n8n path input."""
    n8n_path = message.text.strip()
    
    if not n8n_path.startswith("/"):
        await message.answer("Путь должен быть абсолютным (начинаться с /):")
        return
    
    await state.update_data(n8n_path=n8n_path)
    await finish_add_server(message, state)


async def finish_add_server(message: Message, state: FSMContext, edit: bool = False):
    """Finish adding server and save to database."""
    data = await state.get_data()
    await state.clear()
    
    server = Server(
        id=None,
        name=data["name"],
        host=data["host"],
        port=data.get("port", 22),
        user=data.get("user", "root"),
        auth_type=data["auth_type"],
        ssh_key_path=data.get("ssh_key_path"),
        ssh_password=data.get("ssh_password"),
        n8n_path=data.get("n8n_path", "/opt/n8n-docker-caddy")
    )
    
    storage = get_storage()
    server_id = storage.add_server(server)
    server.id = server_id
    
    # Test connection
    text = f"🔄 Сервер **{server.name}** добавлен. Проверяю подключение..."
    if edit:
        await message.edit_text(text, parse_mode="Markdown")
    else:
        message = await message.answer(text, parse_mode="Markdown")
    
    executor = SSHExecutor(server)
    success, conn_message = await executor.test_connection()
    
    if success:
        version = await executor.get_current_version()
        running = await executor.check_n8n_running()
        
        status = "🟢 Работает" if running else "🔴 Не запущен"
        version_text = f"v{version}" if version else "неизвестна"
        
        await message.edit_text(
            f"✅ *Сервер добавлен успешно!*\n\n"
            f"**Имя:** {server.name}\n"
            f"**Хост:** {server.host}:{server.port}\n"
            f"**Статус n8n:** {status}\n"
            f"**Версия:** {version_text}",
            parse_mode="Markdown",
            reply_markup=get_main_menu(has_servers=True)
        )
    else:
        await message.edit_text(
            f"⚠️ *Сервер добавлен, но подключение не удалось*\n\n"
            f"**Имя:** {server.name}\n"
            f"**Ошибка:** {conn_message}\n\n"
            "Проверь настройки и попробуй подключиться позже.",
            parse_mode="Markdown",
            reply_markup=get_server_details_keyboard(server_id)
        )


# ============= Settings =============

@router.callback_query(F.data == "settings_menu")
@admin_only
async def cb_settings_menu(callback: CallbackQuery):
    """Show settings menu."""
    storage = get_storage()
    interval = storage.get_check_interval()
    
    await callback.message.edit_text(
        f"⚙️ *Настройки*\n\n"
        f"**Интервал проверки обновлений:** {interval} ч",
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "setting:interval")
@admin_only
async def cb_setting_interval(callback: CallbackQuery):
    """Show interval selection."""
    await callback.message.edit_text(
        "⏱ *Интервал проверки обновлений*\n\n"
        "Как часто проверять наличие новых версий n8n?",
        parse_mode="Markdown",
        reply_markup=get_interval_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_interval:"))
@admin_only
async def cb_set_interval(callback: CallbackQuery):
    """Set check interval."""
    hours = int(callback.data.split(":")[1])
    storage = get_storage()
    storage.set_check_interval(hours)
    
    # Update scheduler if running
    if _scheduler:
        await _scheduler.update_check_interval(hours)
    
    await callback.message.edit_text(
        f"✅ Интервал проверки установлен: **{hours} ч**",
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    await callback.answer()


# ============= Update Flow =============

@router.callback_query(F.data.startswith("select_server:"))
@admin_only
async def cb_select_server(callback: CallbackQuery, state: FSMContext):
    """Handle server selection."""
    storage = get_storage()
    servers = storage.get_all_servers()
    server_name = callback.data.split(":", 1)[1]
    
    # Get current selection
    data = await state.get_data()
    selected: set[str] = set(data.get("selected_servers", []))
    
    if server_name == "__all__":
        # Toggle all servers
        all_names = {s.name for s in servers}
        if selected == all_names:
            selected.clear()
        else:
            selected = all_names
    elif server_name == "__confirm__":
        # Proceed to time selection
        if not selected:
            await callback.answer("Выбери хотя бы один сервер", show_alert=True)
            return
        
        await state.update_data(selected_servers=list(selected))
        await state.set_state(UpdateStates.selecting_time)
        
        servers_text = ", ".join(sorted(selected))
        await callback.message.edit_text(
            f"📅 *Выбор времени обновления*\n\n"
            f"Серверы: {servers_text}\n\n"
            "Когда выполнить обновление?",
            parse_mode="Markdown",
            reply_markup=get_time_keyboard("selected")
        )
        await callback.answer()
        return
    else:
        # Toggle single server
        if server_name in selected:
            selected.discard(server_name)
        else:
            selected.add(server_name)
    
    await state.update_data(selected_servers=list(selected))
    
    await callback.message.edit_reply_markup(
        reply_markup=get_servers_keyboard(servers, "select_server", selected)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("schedule:"))
@admin_only
async def cb_schedule(callback: CallbackQuery, state: FSMContext):
    """Handle time selection for update."""
    parts = callback.data.split(":")
    time_key = parts[1]
    
    data = await state.get_data()
    selected_servers = data.get("selected_servers", [])
    
    if not selected_servers:
        await callback.answer("Ошибка: серверы не выбраны", show_alert=True)
        return
    
    # Calculate schedule time
    now = datetime.now()
    schedule_time = None
    time_description = ""
    
    if time_key == "now":
        schedule_time = now
        time_description = "сейчас"
    elif time_key == "5m":
        schedule_time = now + timedelta(minutes=5)
        time_description = f"через 5 минут ({schedule_time.strftime('%H:%M')})"
    elif time_key == "15m":
        schedule_time = now + timedelta(minutes=15)
        time_description = f"через 15 минут ({schedule_time.strftime('%H:%M')})"
    elif time_key == "30m":
        schedule_time = now + timedelta(minutes=30)
        time_description = f"через 30 минут ({schedule_time.strftime('%H:%M')})"
    elif time_key == "1h":
        schedule_time = now + timedelta(hours=1)
        time_description = f"через 1 час ({schedule_time.strftime('%H:%M')})"
    elif time_key == "3h":
        schedule_time = now + timedelta(hours=3)
        time_description = f"через 3 часа ({schedule_time.strftime('%H:%M')})"
    elif time_key == "night":
        # Schedule for 3:00 AM
        schedule_time = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if schedule_time <= now:
            schedule_time += timedelta(days=1)
        time_description = f"ночью в 3:00 ({schedule_time.strftime('%d.%m %H:%M')})"
    
    await state.update_data(schedule_time=schedule_time.isoformat(), time_description=time_description)
    
    servers_text = ", ".join(sorted(selected_servers))
    
    if time_key == "now":
        # Execute immediately
        await callback.answer()
        await state.clear()
        await execute_updates(callback.message, selected_servers, edit=True)
    else:
        # Schedule for later - confirm
        await callback.message.edit_text(
            f"⏰ *Подтверждение обновления*\n\n"
            f"Серверы: {servers_text}\n"
            f"Время: {time_description}\n\n"
            "Подтвердить?",
            parse_mode="Markdown",
            reply_markup=get_confirm_update_keyboard("selected", time_key)
        )
        await callback.answer()


@router.callback_query(F.data.startswith("confirm_update:"))
@admin_only
async def cb_confirm_update(callback: CallbackQuery, state: FSMContext):
    """Confirm scheduled update."""
    data = await state.get_data()
    selected_servers = data.get("selected_servers", [])
    schedule_time_str = data.get("schedule_time")
    time_description = data.get("time_description", "")
    
    if not selected_servers or not schedule_time_str:
        await callback.answer("Ошибка: данные утеряны", show_alert=True)
        await state.clear()
        return
    
    schedule_time = datetime.fromisoformat(schedule_time_str)
    
    # Schedule the update
    if _scheduler:
        job_id = await _scheduler.schedule_update(
            server_names=selected_servers,
            run_time=schedule_time,
            chat_id=callback.message.chat.id
        )
        
        await state.clear()
        
        servers_text = ", ".join(sorted(selected_servers))
        await callback.message.edit_text(
            f"✅ *Обновление запланировано*\n\n"
            f"Серверы: {servers_text}\n"
            f"Время: {time_description}\n"
            f"ID задачи: `{job_id}`\n\n"
            "Я уведомлю тебя о результате.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка: планировщик не инициализирован",
            reply_markup=get_back_keyboard()
        )
    
    await callback.answer()


# ============= Helper Functions =============

async def show_status(message: Message, edit: bool = False):
    """Show status of all servers."""
    storage = get_storage()
    servers = storage.get_all_servers()
    
    if not servers:
        text = "📊 *Статус серверов*\n\nУ тебя пока нет серверов."
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        else:
            await message.answer(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        return
    
    # Show loading message
    loading_text = "🔄 Проверяю статус серверов..."
    if edit:
        await message.edit_text(loading_text)
    else:
        message = await message.answer(loading_text)
    
    # Get status for all servers in parallel
    tasks = [get_server_status(server) for server in servers]
    statuses = await asyncio.gather(*tasks)
    
    # Get latest version
    latest = await get_latest_version()
    latest_str = str(latest) if latest else "неизвестно"
    
    # Format status message
    lines = [f"📊 *Статус серверов*\n\nПоследняя версия n8n: `{latest_str}`\n"]
    
    for status in statuses:
        if status["connected"]:
            version = status["version"] or "?"
            running = "🟢" if status["running"] else "🔴"
            
            # Check if update needed
            update_badge = ""
            if latest and status["version"]:
                cmp = compare_versions(status["version"], str(latest))
                if cmp < 0:
                    update_badge = " ⬆️"
                elif cmp == 0:
                    update_badge = " ✅"
            
            lines.append(
                f"{running} *{status['name']}*\n"
                f"   └ {status['host']} • v{version}{update_badge}"
            )
        else:
            lines.append(
                f"🔴 *{status['name']}*\n"
                f"   └ {status['host']} • ❌ {status['error']}"
            )
    
    await message.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )


async def check_updates(message: Message, edit: bool = False):
    """Check for available updates."""
    storage = get_storage()
    servers = storage.get_all_servers()
    
    if not servers:
        text = "🔍 *Проверка обновлений*\n\nУ тебя пока нет серверов."
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        else:
            await message.answer(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        return
    
    loading_text = "🔍 Проверяю обновления..."
    if edit:
        await message.edit_text(loading_text)
    else:
        message = await message.answer(loading_text)
    
    # Get latest version
    latest = await get_latest_version()
    
    if not latest:
        await message.edit_text(
            "❌ Не удалось получить информацию о последней версии n8n",
            reply_markup=get_back_keyboard()
        )
        return
    
    # Get current versions from all servers
    tasks = [get_server_status(server) for server in servers]
    statuses = await asyncio.gather(*tasks)
    
    updates_available = []
    up_to_date = []
    errors = []
    
    for status in statuses:
        if not status["connected"]:
            errors.append(status)
        elif status["version"]:
            cmp = compare_versions(status["version"], str(latest))
            if cmp < 0:
                updates_available.append(status)
            else:
                up_to_date.append(status)
        else:
            errors.append(status)
    
    # Format message
    lines = [f"🔍 *Проверка обновлений*\n\nПоследняя версия: `{latest}`\n"]
    
    if updates_available:
        lines.append("⬆️ *Доступны обновления:*")
        for s in updates_available:
            lines.append(f"   • {s['name']}: v{s['version']} → v{latest}")
        lines.append("")
    
    if up_to_date:
        lines.append("✅ *Актуальны:*")
        for s in up_to_date:
            lines.append(f"   • {s['name']}: v{s['version']}")
        lines.append("")
    
    if errors:
        lines.append("❌ *Ошибки:*")
        for s in errors:
            lines.append(f"   • {s['name']}: {s.get('error', 'нет версии')}")
    
    await message.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )


async def start_update_flow(message: Message, state: FSMContext, edit: bool = False):
    """Start the update server selection flow."""
    storage = get_storage()
    servers = storage.get_all_servers()
    
    if not servers:
        text = "🔄 *Обновление серверов*\n\nУ тебя пока нет серверов."
        if edit:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        else:
            await message.answer(text, parse_mode="Markdown", reply_markup=get_servers_menu())
        return
    
    await state.clear()
    await state.set_state(UpdateStates.selecting_servers)
    
    text = (
        "🔄 *Обновление серверов*\n\n"
        "Выбери серверы для обновления:"
    )
    
    if edit:
        await message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_servers_keyboard(servers, "select_server")
        )
    else:
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_servers_keyboard(servers, "select_server")
        )


async def execute_updates(message: Message, server_names: list[str], edit: bool = False):
    """Execute updates on selected servers."""
    storage = get_storage()
    servers = [s for s in storage.get_all_servers() if s.name in server_names]
    
    if not servers:
        text = "❌ Ошибка: серверы не найдены"
        if edit:
            await message.edit_text(text, reply_markup=get_back_keyboard())
        else:
            await message.answer(text, reply_markup=get_back_keyboard())
        return
    
    # Show progress
    text = f"🔄 *Обновление {len(servers)} сервера(ов)*\n\nЭто может занять несколько минут..."
    if edit:
        await message.edit_text(text, parse_mode="Markdown")
    else:
        message = await message.answer(text, parse_mode="Markdown")
    
    # Execute updates
    results: list[UpdateResult] = []
    for server in servers:
        await message.edit_text(
            f"🔄 *Обновление серверов*\n\n"
            f"Текущий: {server.name}...\n"
            f"Завершено: {len(results)}/{len(servers)}",
            parse_mode="Markdown"
        )
        
        executor = SSHExecutor(server)
        result = await executor.update_n8n()
        results.append(result)
        
        # Save to history
        storage.add_update_history(
            server_id=server.id,
            server_name=server.name,
            old_version=result.old_version or "",
            new_version=result.new_version or "",
            success=result.success,
            message=result.message
        )
    
    # Format results
    lines = ["🏁 *Результаты обновления*\n"]
    
    success_count = sum(1 for r in results if r.success)
    lines.append(f"Успешно: {success_count}/{len(results)}\n")
    
    for result in results:
        if result.success:
            version_change = ""
            if result.old_version and result.new_version:
                if result.old_version != result.new_version:
                    version_change = f" (v{result.old_version} → v{result.new_version})"
                else:
                    version_change = f" (v{result.new_version})"
            lines.append(f"✅ *{result.server_name}*{version_change}")
        else:
            lines.append(f"❌ *{result.server_name}*: {result.message}")
    
    await message.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=get_back_keyboard()
    )
    
    return results
