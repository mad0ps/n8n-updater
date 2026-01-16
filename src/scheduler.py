"""Scheduler for periodic version checks and scheduled updates."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot

from .storage import get_storage, Server
from .version_checker import get_latest_version, compare_versions, get_release_changelog
from .ssh_executor import SSHExecutor, get_server_status, UpdateResult, perform_full_health_check

logger = logging.getLogger(__name__)


class UpdateScheduler:
    """Manages scheduled version checks and updates."""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self._version_check_job_id = "version_check"
        self._monitoring_job_id = "server_monitoring"
    
    async def start(self):
        """Start the scheduler."""
        storage = get_storage()
        interval = storage.get_check_interval()
        
        # Schedule periodic version check
        self.scheduler.add_job(
            self._check_for_updates,
            IntervalTrigger(hours=interval),
            id=self._version_check_job_id,
            name="Periodic n8n version check",
            replace_existing=True
        )
        
        self.scheduler.start()
        logger.info(f"Scheduler started. Checking for updates every {interval} hours.")
        
        # Start monitoring if enabled
        if storage.get_setting("monitoring_enabled", "0") == "1":
            await self.start_monitoring()
        
        # Run initial check after a short delay
        asyncio.create_task(self._delayed_initial_check())
    
    async def _delayed_initial_check(self):
        """Run initial check after bot is ready."""
        await asyncio.sleep(5)
        await self._check_for_updates()
    
    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    
    async def update_check_interval(self, hours: int):
        """Update the version check interval."""
        # Remove old job
        try:
            self.scheduler.remove_job(self._version_check_job_id)
        except Exception:
            pass
        
        # Add new job with updated interval
        self.scheduler.add_job(
            self._check_for_updates,
            IntervalTrigger(hours=hours),
            id=self._version_check_job_id,
            name="Periodic n8n version check",
            replace_existing=True
        )
        
        logger.info(f"Check interval updated to {hours} hours")
    
    async def _check_for_updates(self):
        """Check for new n8n versions and notify if found."""
        logger.info("Checking for n8n updates...")
        
        storage = get_storage()
        admin_id = storage.get_admin_chat_id()
        
        if not admin_id:
            logger.warning("No admin configured, skipping update check")
            return
        
        servers = storage.get_all_servers()
        if not servers:
            logger.info("No servers configured, skipping update check")
            return
        
        try:
            latest = await get_latest_version()
            
            if not latest:
                logger.warning("Could not fetch latest version")
                return
            
            latest_str = str(latest)
            last_known = storage.get_last_known_version()
            
            # Check if this is a new version we haven't seen
            if last_known and last_known == latest_str:
                logger.info(f"No new version. Current latest: {latest_str}")
                return
            
            # Get server statuses
            tasks = [get_server_status(server) for server in servers]
            statuses = await asyncio.gather(*tasks)
            
            # Find servers needing updates
            servers_needing_update = []
            for status in statuses:
                if status["connected"] and status["version"]:
                    cmp = compare_versions(status["version"], latest_str)
                    if cmp < 0:
                        servers_needing_update.append(status)
            
            # Update last known version
            storage.set_last_known_version(latest_str)
            
            # If we have a new version and servers need updating, notify
            if last_known is None:
                # First run - just store the version
                logger.info(f"Initial version check complete. Latest: {latest_str}")
                if servers_needing_update:
                    logger.info(f"{len(servers_needing_update)} server(s) can be updated")
            elif servers_needing_update:
                # New version detected!
                await self._send_update_notification(admin_id, latest_str, servers_needing_update)
            else:
                logger.info(f"All servers are up to date with version {latest_str}")
                
        except Exception as e:
            logger.exception(f"Error checking for updates: {e}")
    
    async def _send_update_notification(self, chat_id: int, latest_version: str, servers: list[dict]):
        """Send notification about available updates with changelog."""
        
        # Try to get changelog from GitHub
        release_info = await get_release_changelog(latest_version)
        
        lines = [
            f"🆕 *Доступна новая версия n8n!*\n",
            f"Версия: `{latest_version}`"
        ]
        
        # Add changelog if available
        if release_info and release_info.changelog:
            lines.append(f"\n📋 *Что нового:*\n{release_info.changelog}")
            lines.append(f"\n🔗 [Подробнее на GitHub]({release_info.url})")
        
        lines.append("\n*Серверы, требующие обновления:*")
        for server in servers:
            lines.append(f"   • {server['name']}: v{server['version']}")
        
        lines.append("\nИспользуй /update для обновления.")
        
        try:
            from .bot.keyboards import get_main_menu
            await self.bot.send_message(
                chat_id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=get_main_menu(has_servers=True),
                disable_web_page_preview=True
            )
            logger.info(f"Update notification sent for version {latest_version}")
        except Exception as e:
            logger.error(f"Failed to send update notification: {e}")
    
    async def schedule_update(
        self,
        server_names: list[str],
        run_time: datetime,
        chat_id: int
    ) -> str:
        """
        Schedule an update for specified servers.
        
        Args:
            server_names: List of server names to update.
            run_time: When to run the update.
            chat_id: Telegram chat ID to notify about results.
            
        Returns:
            Job ID for the scheduled update.
        """
        job_id = f"update_{uuid.uuid4().hex[:8]}"
        
        self.scheduler.add_job(
            self._execute_scheduled_update,
            DateTrigger(run_date=run_time),
            id=job_id,
            name=f"Update {', '.join(server_names)}",
            kwargs={
                "server_names": server_names,
                "chat_id": chat_id,
                "job_id": job_id
            }
        )
        
        logger.info(f"Scheduled update {job_id} for {server_names} at {run_time}")
        return job_id
    
    async def _execute_scheduled_update(
        self,
        server_names: list[str],
        chat_id: int,
        job_id: str
    ):
        """Execute a scheduled update."""
        storage = get_storage()
        
        logger.info(f"Executing scheduled update {job_id} for {server_names}")
        
        # Notify start
        try:
            await self.bot.send_message(
                chat_id,
                f"🔄 *Запуск запланированного обновления*\n\n"
                f"Серверы: {', '.join(server_names)}\n"
                f"ID: `{job_id}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send start notification: {e}")
        
        # Get server configs
        all_servers = storage.get_all_servers()
        servers = [s for s in all_servers if s.name in server_names]
        
        # Execute updates
        results: list[UpdateResult] = []
        for server in servers:
            executor = SSHExecutor(server)
            result = await executor.update_n8n()
            results.append(result)
            logger.info(f"Update result for {server.name}: {result.success}")
            
            # Save to history
            storage.add_update_history(
                server_id=server.id,
                server_name=server.name,
                old_version=result.old_version or "",
                new_version=result.new_version or "",
                success=result.success,
                message=result.message
            )
        
        # Send results
        await self._send_update_results(chat_id, results)
    
    async def _send_update_results(self, chat_id: int, results: list[UpdateResult]):
        """Send update results notification."""
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
                if result.details:
                    # Truncate long details
                    details = result.details[:200] + "..." if len(result.details) > 200 else result.details
                    lines.append(f"   └ {details}")
        
        try:
            from .bot.keyboards import get_main_menu
            await self.bot.send_message(
                chat_id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=get_main_menu(has_servers=True)
            )
        except Exception as e:
            logger.error(f"Failed to send update results: {e}")
    
    def cancel_update(self, job_id: str) -> bool:
        """
        Cancel a scheduled update.
        
        Returns:
            True if job was found and cancelled.
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Cancelled scheduled update {job_id}")
            return True
        except Exception:
            return False
    
    def get_scheduled_updates(self) -> list[dict]:
        """Get list of scheduled updates."""
        jobs = []
        for job in self.scheduler.get_jobs():
            if job.id.startswith("update_"):
                jobs.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time
                })
        return jobs
    
    async def force_check(self):
        """Force an immediate version check."""
        await self._check_for_updates()
    
    # ============= Server Monitoring =============
    
    async def start_monitoring(self):
        """Start periodic server health monitoring."""
        try:
            self.scheduler.remove_job(self._monitoring_job_id)
        except Exception:
            pass
        
        self.scheduler.add_job(
            self._check_servers_health,
            IntervalTrigger(minutes=5),
            id=self._monitoring_job_id,
            name="Server health monitoring",
            replace_existing=True
        )
        
        logger.info("Server monitoring started (every 5 minutes)")
        
        # Run initial check
        asyncio.create_task(self._delayed_health_check())
    
    async def _delayed_health_check(self):
        """Run initial health check after a short delay."""
        await asyncio.sleep(10)
        await self._check_servers_health()
    
    def stop_monitoring(self):
        """Stop server health monitoring."""
        try:
            self.scheduler.remove_job(self._monitoring_job_id)
            logger.info("Server monitoring stopped")
        except Exception:
            pass
    
    async def _check_servers_health(self):
        """Check health of all servers and notify on issues."""
        logger.info("Running server health check...")
        
        storage = get_storage()
        admin_id = storage.get_admin_chat_id()
        
        if not admin_id:
            logger.warning("No admin configured, skipping health check")
            return
        
        servers = storage.get_all_servers()
        if not servers:
            logger.info("No servers configured, skipping health check")
            return
        
        # Check each server
        for server in servers:
            try:
                result = await perform_full_health_check(server)
                
                # Update health status in database
                storage.update_server_health(
                    server_id=server.id,
                    server_name=server.name,
                    is_healthy=result.is_healthy,
                    error_message=result.error
                )
                
                if result.is_healthy:
                    logger.debug(f"Server {server.name}: healthy")
                else:
                    logger.warning(f"Server {server.name}: unhealthy - {result.error}")
                    
            except Exception as e:
                logger.exception(f"Error checking health for {server.name}: {e}")
                storage.update_server_health(
                    server_id=server.id,
                    server_name=server.name,
                    is_healthy=False,
                    error_message=str(e)
                )
        
        # Check for servers that need notification (failed 2+ times and not yet notified)
        unhealthy_servers = storage.get_unhealthy_servers_for_notification(min_failures=2)
        
        if unhealthy_servers:
            await self._send_health_alert(admin_id, unhealthy_servers)
            
            # Mark as notified
            for server in unhealthy_servers:
                storage.mark_server_notified(server["server_id"])
    
    async def _send_health_alert(self, chat_id: int, unhealthy_servers: list[dict]):
        """Send health alert notification."""
        lines = ["🚨 *Проблемы с серверами!*\n"]
        
        for server in unhealthy_servers:
            error = server.get("error_message", "Неизвестная ошибка")
            failures = server.get("consecutive_failures", 0)
            lines.append(
                f"🔴 *{server['server_name']}*\n"
                f"   └ {error}\n"
                f"   └ Неудачных проверок: {failures}"
            )
        
        lines.append("\nИспользуй /status для деталей.")
        
        try:
            from .bot.keyboards import get_main_menu
            await self.bot.send_message(
                chat_id,
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=get_main_menu(has_servers=True)
            )
            logger.info(f"Health alert sent for {len(unhealthy_servers)} server(s)")
        except Exception as e:
            logger.error(f"Failed to send health alert: {e}")