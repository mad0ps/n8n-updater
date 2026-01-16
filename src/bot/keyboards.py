"""Inline keyboards for Telegram bot."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..storage import Server


def get_main_menu(has_servers: bool = True) -> InlineKeyboardMarkup:
    """Get main menu keyboard."""
    builder = InlineKeyboardBuilder()
    
    if has_servers:
        builder.row(
            InlineKeyboardButton(text="📊 Статус", callback_data="status"),
        )
        builder.row(
            InlineKeyboardButton(text="🔄 Обновить серверы", callback_data="update_menu"),
        )
        builder.row(
            InlineKeyboardButton(text="📜 История обновлений", callback_data="history"),
        )
    
    builder.row(
        InlineKeyboardButton(text="🖥 Управление серверами", callback_data="servers_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu"),
    )
    
    return builder.as_markup()


def get_servers_menu() -> InlineKeyboardMarkup:
    """Get servers management menu."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="➕ Добавить сервер", callback_data="add_server"),
    )
    builder.row(
        InlineKeyboardButton(text="📋 Список серверов", callback_data="list_servers"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_servers_list_keyboard(servers: list[Server]) -> InlineKeyboardMarkup:
    """Get keyboard with server list for management."""
    builder = InlineKeyboardBuilder()
    
    for server in servers:
        builder.row(
            InlineKeyboardButton(
                text=f"🖥 {server.name} ({server.host})",
                callback_data=f"server_details:{server.id}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="➕ Добавить", callback_data="add_server"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_server_details_keyboard(server_id: int, has_url: bool = False) -> InlineKeyboardMarkup:
    """Get keyboard for server details view."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔗 Проверить подключение", callback_data=f"test_server:{server_id}"),
    )
    
    if has_url:
        builder.row(
            InlineKeyboardButton(text="🩺 Health Check", callback_data=f"health_check:{server_id}"),
        )
    
    builder.row(
        InlineKeyboardButton(text="🌐 Настроить URL", callback_data=f"set_url:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="📜 История сервера", callback_data=f"server_history:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_server:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="list_servers"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_confirm_delete_keyboard(server_id: int) -> InlineKeyboardMarkup:
    """Get confirmation keyboard for server deletion."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"server_details:{server_id}"),
    )
    
    return builder.as_markup()


def get_auth_type_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for selecting auth type."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔑 Пароль", callback_data="auth_type:password"),
    )
    builder.row(
        InlineKeyboardButton(text="🔐 SSH ключ", callback_data="auth_type:key"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    
    return builder.as_markup()


def get_servers_keyboard(
    servers: list[Server],
    action: str,
    selected: set[str] | None = None
) -> InlineKeyboardMarkup:
    """
    Get keyboard for selecting servers.
    
    Args:
        servers: List of server configs.
        action: Action prefix for callback data.
        selected: Set of selected server names (for multi-select).
    """
    builder = InlineKeyboardBuilder()
    selected = selected or set()
    
    for server in servers:
        is_selected = server.name in selected
        prefix = "✅ " if is_selected else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{server.name}",
                callback_data=f"{action}:{server.name}"
            )
        )
    
    # Add "All servers" button
    all_selected = len(selected) == len(servers)
    all_prefix = "✅ " if all_selected else ""
    builder.row(
        InlineKeyboardButton(
            text=f"{all_prefix}Все серверы",
            callback_data=f"{action}:__all__"
        )
    )
    
    # Add action buttons
    if selected:
        builder.row(
            InlineKeyboardButton(text="▶️ Продолжить", callback_data=f"{action}:__confirm__"),
        )
    
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    
    return builder.as_markup()


def get_time_keyboard(servers_key: str) -> InlineKeyboardMarkup:
    """
    Get keyboard for selecting update time.
    
    Args:
        servers_key: Key to identify selected servers in state.
    """
    builder = InlineKeyboardBuilder()
    
    # Immediate update
    builder.row(
        InlineKeyboardButton(text="⚡ Сейчас", callback_data=f"schedule:now:{servers_key}"),
    )
    
    # Time options
    builder.row(
        InlineKeyboardButton(text="⏰ Через 5 мин", callback_data=f"schedule:5m:{servers_key}"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Через 15 мин", callback_data=f"schedule:15m:{servers_key}"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Через 30 мин", callback_data=f"schedule:30m:{servers_key}"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Через 1 час", callback_data=f"schedule:1h:{servers_key}"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Через 3 часа", callback_data=f"schedule:3h:{servers_key}"),
    )
    builder.row(
        InlineKeyboardButton(text="🌙 Ночью (3:00)", callback_data=f"schedule:night:{servers_key}"),
    )
    
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    
    return builder.as_markup()


def get_confirm_update_keyboard(servers_key: str, time_key: str) -> InlineKeyboardMarkup:
    """Get confirmation keyboard before starting update."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=f"confirm_update:{servers_key}:{time_key}"
        ),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    
    return builder.as_markup()


def get_back_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard with back to menu button."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    return builder.as_markup()


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Get settings menu keyboard."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏱ Интервал проверки", callback_data="setting:interval"),
    )
    builder.row(
        InlineKeyboardButton(text="🩺 Мониторинг серверов", callback_data="setting:monitoring"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_monitoring_keyboard(enabled: bool = False) -> InlineKeyboardMarkup:
    """Get monitoring settings keyboard."""
    builder = InlineKeyboardBuilder()
    
    if enabled:
        builder.row(
            InlineKeyboardButton(text="🔴 Выключить мониторинг", callback_data="monitoring:disable"),
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🟢 Включить мониторинг", callback_data="monitoring:enable"),
        )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu"),
    )
    
    return builder.as_markup()


def get_history_keyboard(has_more: bool = False, offset: int = 0) -> InlineKeyboardMarkup:
    """Get keyboard for update history view."""
    builder = InlineKeyboardBuilder()
    
    if has_more:
        builder.row(
            InlineKeyboardButton(text="📜 Показать ещё", callback_data=f"history:more:{offset}"),
        )
    
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_history_detail_keyboard(entry_id: int) -> InlineKeyboardMarkup:
    """Get keyboard for history entry details."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⬅️ К истории", callback_data="history"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_interval_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for selecting check interval."""
    builder = InlineKeyboardBuilder()
    
    for hours in [1, 3, 6, 12, 24]:
        label = f"{hours} ч" if hours < 24 else "24 ч (1 день)"
        builder.add(InlineKeyboardButton(text=label, callback_data=f"set_interval:{hours}"))
    
    builder.adjust(3, 2)
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_menu"),
    )
    
    return builder.as_markup()


def get_skip_keyboard(field: str) -> InlineKeyboardMarkup:
    """Get keyboard with skip option for optional fields."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏭ Пропустить (по умолчанию)", callback_data=f"skip:{field}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    
    return builder.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard with only cancel button."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
    )
    return builder.as_markup()


def get_rollback_keyboard(server_id: int, backup_id: int) -> InlineKeyboardMarkup:
    """Get keyboard for rollback confirmation after failed update."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(
            text="⏪ Откатить к предыдущей версии",
            callback_data=f"rollback_confirm:{server_id}:{backup_id}"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Оставить как есть",
            callback_data=f"rollback_skip:{server_id}:{backup_id}"
        ),
    )
    
    return builder.as_markup()


def get_rollback_result_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard after rollback result."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📊 Статус серверов", callback_data="status"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    
    return builder.as_markup()


def get_status_keyboard() -> InlineKeyboardMarkup:
    """Get keyboard for cached status view."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить данные", callback_data="refresh_all"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu"),
    )
    
    return builder.as_markup()
