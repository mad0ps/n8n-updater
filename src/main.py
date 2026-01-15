"""Main entry point for n8n Updater."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from .storage import init_storage
from .scheduler import UpdateScheduler
from .bot.handlers import router, set_scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Reduce noise from libraries
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


class N8nUpdater:
    """Main application class."""
    
    def __init__(self, bot_token: Optional[str] = None, db_path: Optional[Path] = None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.db_path = db_path
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.scheduler: Optional[UpdateScheduler] = None
        self._shutdown_event = asyncio.Event()
    
    async def start(self):
        """Start the application."""
        logger.info("Starting n8n Updater...")
        
        # Check for bot token
        if not self.bot_token:
            logger.error(
                "Telegram bot token not found!\n"
                "Set TELEGRAM_BOT_TOKEN environment variable or pass --token argument.\n"
                "Get a token from @BotFather in Telegram."
            )
            sys.exit(1)
        
        # Initialize storage
        try:
            storage = init_storage(self.db_path)
            logger.info(f"Database initialized: {storage.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize storage: {e}")
            sys.exit(1)
        
        # Initialize bot
        self.bot = Bot(
            token=self.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
        )
        
        # Initialize dispatcher with FSM storage
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(router)
        
        # Initialize scheduler
        self.scheduler = UpdateScheduler(self.bot)
        set_scheduler(self.scheduler)
        
        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)
        
        # Start scheduler
        await self.scheduler.start()
        
        # Get bot info
        try:
            bot_info = await self.bot.get_me()
            logger.info(f"Bot started: @{bot_info.username}")
        except Exception as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            sys.exit(1)
        
        # Notify admin if configured
        admin_id = storage.get_admin_chat_id()
        if admin_id:
            try:
                await self.bot.send_message(
                    admin_id,
                    "🟢 *n8n Updater запущен*\n\nИспользуй /start для работы.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Could not send startup notification: {e}")
        
        logger.info("n8n Updater started successfully")
        
        # Start polling
        try:
            await self.dp.start_polling(
                self.bot,
                allowed_updates=["message", "callback_query"]
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the application."""
        logger.info("Stopping n8n Updater...")
        
        # Stop scheduler
        if self.scheduler:
            self.scheduler.stop()
        
        # Close bot session
        if self.bot:
            await self.bot.session.close()
        
        logger.info("n8n Updater stopped")
    
    def _handle_shutdown(self):
        """Handle shutdown signals."""
        logger.info("Received shutdown signal")
        self._shutdown_event.set()
        
        # Cancel all tasks
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()


async def main(bot_token: Optional[str] = None, db_path: Optional[Path] = None):
    """Main async entry point."""
    app = N8nUpdater(bot_token, db_path)
    await app.start()


def run():
    """Synchronous entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="n8n Updater - Telegram bot for updating n8n instances"
    )
    parser.add_argument(
        "-t", "--token",
        help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)",
        default=None
    )
    parser.add_argument(
        "-d", "--db",
        help="Path to SQLite database file",
        default=None
    )
    
    args = parser.parse_args()
    
    db_path = Path(args.db) if args.db else None
    
    try:
        asyncio.run(main(args.token, db_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
