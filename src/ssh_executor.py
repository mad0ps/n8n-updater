"""SSH executor for running commands on remote servers."""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable, Awaitable

import aiohttp
import paramiko

from .storage import Server
from .version_checker import parse_version

logger = logging.getLogger(__name__)


# Progress callback type: async function(step: int, total: int, message: str)
ProgressCallback = Callable[[int, int, str], Awaitable[None]]


@dataclass
class CommandResult:
    """Result of executing a command on a remote server."""
    
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    
    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts)


@dataclass
class UpdateResult:
    """Result of updating n8n on a server."""
    
    server_name: str
    server_id: Optional[int]
    success: bool
    old_version: Optional[str]
    new_version: Optional[str]
    message: str
    details: str = ""
    # Backup info for rollback
    compose_backup_path: Optional[str] = None
    data_backup_path: Optional[str] = None
    can_rollback: bool = False


@dataclass
class RollbackResult:
    """Result of rolling back n8n."""
    
    server_name: str
    server_id: Optional[int]
    success: bool
    restored_version: Optional[str]
    message: str
    details: str = ""


class SSHExecutor:
    """Execute commands on remote servers via SSH."""
    
    def __init__(self, server: Server):
        self.server = server
        self._client: Optional[paramiko.SSHClient] = None
    
    def _get_client(self) -> paramiko.SSHClient:
        """Get or create SSH client connection."""
        if self._client is not None:
            # Check if connection is still alive
            try:
                transport = self._client.get_transport()
                if transport and transport.is_active():
                    return self._client
            except Exception:
                pass
            
            # Connection dead, close and reconnect
            self._close()
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": self.server.host,
            "port": self.server.port,
            "username": self.server.user,
            "timeout": 30,
            "banner_timeout": 30,
        }
        
        # Choose authentication method
        if self.server.auth_type == "key" and self.server.ssh_key_path:
            # Key-based authentication
            key_path = Path(self.server.ssh_key_path)
            if not key_path.exists():
                raise FileNotFoundError(f"SSH key not found: {self.server.ssh_key_path}")
            
            # Try different key types
            pkey = None
            for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                try:
                    pkey = key_class.from_private_key_file(str(key_path))
                    break
                except paramiko.ssh_exception.SSHException:
                    continue
            
            if pkey is None:
                raise ValueError(f"Could not load SSH key: {self.server.ssh_key_path}")
            
            connect_kwargs["pkey"] = pkey
            
        elif self.server.auth_type == "password" and self.server.ssh_password:
            # Password-based authentication
            connect_kwargs["password"] = self.server.ssh_password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        else:
            raise ValueError(f"Invalid auth configuration for server {self.server.name}")
        
        client.connect(**connect_kwargs)
        
        self._client = client
        return client
    
    def _close(self):
        """Close SSH connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
    
    async def execute(self, command: str, timeout: int = 300) -> CommandResult:
        """
        Execute a command on the remote server.
        
        Args:
            command: Shell command to execute.
            timeout: Command timeout in seconds.
            
        Returns:
            CommandResult with output and exit code.
        """
        def _exec():
            try:
                client = self._get_client()
                stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                
                exit_code = stdout.channel.recv_exit_status()
                stdout_text = stdout.read().decode("utf-8", errors="replace")
                stderr_text = stderr.read().decode("utf-8", errors="replace")
                
                return CommandResult(
                    success=exit_code == 0,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=exit_code
                )
            except Exception as e:
                return CommandResult(
                    success=False,
                    stdout="",
                    stderr=str(e),
                    exit_code=-1
                )
        
        # Run in thread pool to not block async loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _exec)
    
    async def get_current_version(self) -> Optional[str]:
        """
        Get current n8n version running on the server.
        
        Returns:
            Version string like "1.70.0" or None if not found.
        """
        # Try to get version from running container
        command = f"cd {self.server.n8n_path} && docker compose ps --format json n8n 2>/dev/null || docker-compose ps --format json n8n 2>/dev/null"
        result = await self.execute(command)
        
        if not result.success:
            # Try alternative: get image tag from compose file or running container
            command = f"docker ps --filter 'name=n8n' --format '{{{{.Image}}}}' | head -1"
            result = await self.execute(command)
        
        if result.success and result.stdout.strip():
            # Extract version from image name like "n8nio/n8n:1.70.0"
            image = result.stdout.strip().split("\n")[0]
            if ":" in image:
                version = image.split(":")[-1]
                if parse_version(version):
                    return version
        
        # Try getting version from n8n CLI
        command = f"cd {self.server.n8n_path} && docker compose exec -T n8n n8n --version 2>/dev/null || docker-compose exec -T n8n n8n --version 2>/dev/null"
        result = await self.execute(command, timeout=30)
        
        if result.success and result.stdout.strip():
            # Parse version from output like "1.70.0"
            version_match = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
            if version_match:
                return version_match.group(1)
        
        return None
    
    async def check_n8n_running(self) -> bool:
        """Check if n8n container is running and healthy."""
        command = f"cd {self.server.n8n_path} && docker compose ps --status running 2>/dev/null | grep -q n8n || docker-compose ps 2>/dev/null | grep -q 'Up'"
        result = await self.execute(command, timeout=30)
        return result.success
    
    async def check_docker_installed(self) -> bool:
        """Check if Docker is installed and accessible."""
        result = await self.execute("docker --version", timeout=10)
        return result.success
    
    async def check_n8n_path_exists(self) -> bool:
        """Check if n8n path exists on the server."""
        result = await self.execute(f"test -d {self.server.n8n_path}", timeout=10)
        return result.success
    
    async def update_n8n(
        self,
        progress_callback: Optional[ProgressCallback] = None
    ) -> UpdateResult:
        """
        Update n8n to the latest version.

        Args:
            progress_callback: Optional async callback for progress updates.
                              Called with (step, total_steps, message).

        Returns:
            UpdateResult with status and details.
        """
        server_name = self.server.name
        server_id = self.server.id
        details_parts = []
        compose_backup_path = None
        data_backup_path = None
        old_version = None

        # Total steps for progress tracking
        total_steps = 8

        async def report_progress(step: int, message: str):
            """Report progress if callback is provided."""
            if progress_callback:
                try:
                    await progress_callback(step, total_steps, message)
                except Exception as e:
                    logger.warning(f"Progress callback failed: {e}")

        try:
            # Step 1: Get current version
            await report_progress(1, "Получение текущей версии...")
            old_version = await self.get_current_version()
            details_parts.append(f"Old version: {old_version or 'unknown'}")

            # Step 2: Backup data
            await report_progress(2, "Создание бэкапа данных...")
            logger.info(f"[{server_name}] Creating backup of n8n data...")

            # Get real timestamp from server
            timestamp_result = await self.execute("date +%Y%m%d_%H%M%S", timeout=10)
            timestamp = timestamp_result.stdout.strip() if timestamp_result.success else "backup"

            # Find n8n data directory (check common locations)
            find_data_cmd = f"""
            cd {self.server.n8n_path} && \
            if [ -d ".n8n" ]; then echo ".n8n"; \
            elif [ -d "n8n_data" ]; then echo "n8n_data"; \
            elif [ -d "data" ]; then echo "data"; \
            else echo ""; fi
            """
            result = await self.execute(find_data_cmd, timeout=30)
            data_dir = result.stdout.strip() if result.success else ""

            # Create backup directory
            backup_dir = f"{self.server.n8n_path}/backups"
            await self.execute(f"mkdir -p {backup_dir}", timeout=30)

            # Backup n8n data if found
            if data_dir:
                data_backup_path = f"{backup_dir}/n8n_backup_{timestamp}.tar.gz"
                backup_data_cmd = f"cd {self.server.n8n_path} && tar -czf {data_backup_path} {data_dir} 2>/dev/null"
                result = await self.execute(backup_data_cmd, timeout=300)  # 5 min for large backups
                if result.success:
                    details_parts.append(f"Data backup: {data_backup_path}")
                    logger.info(f"[{server_name}] Backup created: {data_backup_path}")
                else:
                    details_parts.append("Data backup failed (continuing anyway)")
                    data_backup_path = None
                    logger.warning(f"[{server_name}] Backup failed: {result.stderr}")
            else:
                details_parts.append("No data dir found, skipping data backup")

            # Step 3: Backup config
            await report_progress(3, "Создание бэкапа конфига...")
            compose_backup_path = f"{backup_dir}/docker-compose.yml.{timestamp}"
            backup_compose_cmd = f"cd {self.server.n8n_path} && cp docker-compose.yml {compose_backup_path}"
            await self.execute(backup_compose_cmd, timeout=30)
            details_parts.append(f"Config backup: {compose_backup_path}")

            # Step 4: Update docker-compose.yml
            await report_progress(4, "Обновление конфигурации...")
            logger.info(f"[{server_name}] Updating image tag to latest...")

            # First, let's see current image line
            check_image_cmd = f"cd {self.server.n8n_path} && grep -E 'image.*n8n' docker-compose.yml"
            result = await self.execute(check_image_cmd, timeout=30)
            current_image = result.stdout.strip()
            logger.info(f"[{server_name}] Current image line: {current_image}")
            details_parts.append(f"Current: {current_image}")

            # Update image to use Docker Hub (not docker.n8n.io which lags behind)
            update_cmd = f"cd {self.server.n8n_path} && sed -i.bak -E 's|docker\\.n8n\\.io/n8nio/n8n(:[^ ]*)?|n8nio/n8n:latest|g' docker-compose.yml"
            await self.execute(update_cmd, timeout=30)

            # Also handle if it was already n8nio/n8n without docker.n8n.io prefix
            update_cmd2 = f"cd {self.server.n8n_path} && sed -i.bak -E 's|([^/])n8nio/n8n(:[^ ]*)?|\\1n8nio/n8n:latest|g' docker-compose.yml"
            await self.execute(update_cmd2, timeout=30)

            # Check what it looks like now
            check_image_cmd = f"cd {self.server.n8n_path} && grep -E 'image.*n8n' docker-compose.yml"
            result = await self.execute(check_image_cmd, timeout=30)
            new_image = result.stdout.strip()
            logger.info(f"[{server_name}] Updated image line: {new_image}")
            details_parts.append(f"Updated to: {new_image}")

            # Step 5: Pull new image (longest step)
            await report_progress(5, "Скачивание образа...")
            logger.info(f"[{server_name}] Pulling new n8n image from Docker Hub...")

            # Force pull from Docker Hub (not docker.n8n.io which lags behind!)
            force_pull_cmd = "docker pull n8nio/n8n:latest 2>&1"
            result = await self.execute(force_pull_cmd, timeout=600)
            if result.success:
                logger.info(f"[{server_name}] Force pull output: {result.stdout[:200]}")
                details_parts.append("Latest image pulled")
            else:
                logger.warning(f"[{server_name}] Force pull failed: {result.stderr}")

            # Now do compose pull
            pull_cmd = f"cd {self.server.n8n_path} && docker compose pull 2>&1 || docker-compose pull 2>&1"
            result = await self.execute(pull_cmd, timeout=600)  # 10 min for pull

            if not result.success:
                return UpdateResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    old_version=old_version,
                    new_version=None,
                    message="Failed to pull new image",
                    details=result.output,
                    compose_backup_path=compose_backup_path,
                    data_backup_path=data_backup_path,
                    can_rollback=bool(compose_backup_path)
                )
            details_parts.append("Image pulled successfully")

            # Step 6: Stop containers
            await report_progress(6, "Остановка контейнеров...")
            logger.info(f"[{server_name}] Stopping n8n...")
            down_cmd = f"cd {self.server.n8n_path} && docker compose down 2>&1 || docker-compose down 2>&1"
            result = await self.execute(down_cmd, timeout=120)

            if not result.success:
                return UpdateResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    old_version=old_version,
                    new_version=None,
                    message="Failed to stop containers",
                    details=result.output,
                    compose_backup_path=compose_backup_path,
                    data_backup_path=data_backup_path,
                    can_rollback=bool(compose_backup_path)
                )
            details_parts.append("Containers stopped")

            # Step 7: Start containers
            await report_progress(7, "Запуск контейнеров...")
            logger.info(f"[{server_name}] Starting n8n...")
            up_cmd = f"cd {self.server.n8n_path} && docker compose up -d 2>&1 || docker-compose up -d 2>&1"
            result = await self.execute(up_cmd, timeout=120)

            if not result.success:
                return UpdateResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    old_version=old_version,
                    new_version=None,
                    message="Failed to start containers",
                    details=result.output,
                    compose_backup_path=compose_backup_path,
                    data_backup_path=data_backup_path,
                    can_rollback=bool(compose_backup_path)
                )
            details_parts.append("Containers started")

            # Wait for container to be ready
            await asyncio.sleep(10)

            # Step 8: Verify and get new version
            await report_progress(8, "Проверка...")
            if not await self.check_n8n_running():
                return UpdateResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    old_version=old_version,
                    new_version=None,
                    message="n8n container failed to start",
                    details="\n".join(details_parts),
                    compose_backup_path=compose_backup_path,
                    data_backup_path=data_backup_path,
                    can_rollback=bool(compose_backup_path)
                )

            # Get new version
            new_version = await self.get_current_version()
            details_parts.append(f"New version: {new_version or 'unknown'}")

            return UpdateResult(
                server_name=server_name,
                server_id=server_id,
                success=True,
                old_version=old_version,
                new_version=new_version,
                message="Update completed successfully",
                details="\n".join(details_parts),
                compose_backup_path=compose_backup_path,
                data_backup_path=data_backup_path,
                can_rollback=False  # Success - no rollback needed
            )

        except Exception as e:
            logger.exception(f"[{server_name}] Update failed with exception")
            return UpdateResult(
                server_name=server_name,
                server_id=server_id,
                success=False,
                old_version=old_version,
                new_version=None,
                message=f"Update failed: {str(e)}",
                details="\n".join(details_parts),
                compose_backup_path=compose_backup_path,
                data_backup_path=data_backup_path,
                can_rollback=bool(compose_backup_path)
            )
        finally:
            self._close()
    
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test SSH connection to the server.
        
        Returns:
            Tuple of (success, message).
        """
        try:
            result = await self.execute("echo 'Connection OK'", timeout=30)
            if result.success:
                return True, "Connection successful"
            else:
                return False, f"Command failed: {result.stderr}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
        finally:
            self._close()
    
    async def rollback_n8n(
        self,
        compose_backup_path: str,
        data_backup_path: Optional[str] = None
    ) -> RollbackResult:
        """
        Rollback n8n to previous version using backup files.
        
        Args:
            compose_backup_path: Path to docker-compose.yml backup
            data_backup_path: Path to data backup tar.gz (optional)
            
        Returns:
            RollbackResult with status and details.
        """
        server_name = self.server.name
        server_id = self.server.id
        details_parts = []
        
        try:
            logger.info(f"[{server_name}] Starting rollback...")
            
            # Stop containers first
            logger.info(f"[{server_name}] Stopping containers...")
            down_cmd = f"cd {self.server.n8n_path} && docker compose down 2>&1 || docker-compose down 2>&1"
            result = await self.execute(down_cmd, timeout=120)
            
            if not result.success:
                return RollbackResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    restored_version=None,
                    message="Failed to stop containers for rollback",
                    details=result.output
                )
            details_parts.append("Containers stopped")
            
            # Restore docker-compose.yml
            logger.info(f"[{server_name}] Restoring docker-compose.yml...")
            restore_compose_cmd = f"cp {compose_backup_path} {self.server.n8n_path}/docker-compose.yml"
            result = await self.execute(restore_compose_cmd, timeout=30)
            
            if not result.success:
                return RollbackResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    restored_version=None,
                    message="Failed to restore docker-compose.yml",
                    details=result.output
                )
            details_parts.append("docker-compose.yml restored")
            
            # Restore data if backup exists
            if data_backup_path:
                logger.info(f"[{server_name}] Restoring data from backup...")
                
                # Check if backup file exists
                check_cmd = f"test -f {data_backup_path}"
                if (await self.execute(check_cmd, timeout=10)).success:
                    # Extract backup (will overwrite existing data)
                    restore_data_cmd = f"cd {self.server.n8n_path} && tar -xzf {data_backup_path}"
                    result = await self.execute(restore_data_cmd, timeout=300)
                    
                    if result.success:
                        details_parts.append("Data restored from backup")
                    else:
                        details_parts.append(f"Data restore failed: {result.stderr}")
                        logger.warning(f"[{server_name}] Data restore failed: {result.stderr}")
                else:
                    details_parts.append("Data backup file not found, skipping")
            
            # Pull the old image (from restored compose file)
            logger.info(f"[{server_name}] Pulling image from restored config...")
            pull_cmd = f"cd {self.server.n8n_path} && docker compose pull 2>&1 || docker-compose pull 2>&1"
            result = await self.execute(pull_cmd, timeout=600)
            
            if result.success:
                details_parts.append("Image pulled")
            else:
                details_parts.append(f"Image pull warning: {result.stderr[:100]}")
            
            # Start containers
            logger.info(f"[{server_name}] Starting containers...")
            up_cmd = f"cd {self.server.n8n_path} && docker compose up -d 2>&1 || docker-compose up -d 2>&1"
            result = await self.execute(up_cmd, timeout=120)
            
            if not result.success:
                return RollbackResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    restored_version=None,
                    message="Failed to start containers after rollback",
                    details="\n".join(details_parts) + f"\n{result.output}"
                )
            details_parts.append("Containers started")
            
            # Wait for container to be ready
            await asyncio.sleep(10)
            
            # Verify n8n is running
            if not await self.check_n8n_running():
                return RollbackResult(
                    server_name=server_name,
                    server_id=server_id,
                    success=False,
                    restored_version=None,
                    message="n8n container failed to start after rollback",
                    details="\n".join(details_parts)
                )
            
            # Get restored version
            restored_version = await self.get_current_version()
            details_parts.append(f"Restored version: {restored_version or 'unknown'}")
            
            logger.info(f"[{server_name}] Rollback completed successfully")
            
            return RollbackResult(
                server_name=server_name,
                server_id=server_id,
                success=True,
                restored_version=restored_version,
                message="Rollback completed successfully",
                details="\n".join(details_parts)
            )
            
        except Exception as e:
            logger.exception(f"[{server_name}] Rollback failed with exception")
            return RollbackResult(
                server_name=server_name,
                server_id=server_id,
                success=False,
                restored_version=None,
                message=f"Rollback failed: {str(e)}",
                details="\n".join(details_parts)
            )
        finally:
            self._close()


async def get_server_status(server: Server) -> dict:
    """
    Get status information for a server.
    
    Returns:
        Dict with server status info.
    """
    executor = SSHExecutor(server)
    
    try:
        connected, conn_msg = await executor.test_connection()
        
        if not connected:
            return {
                "id": server.id,
                "name": server.name,
                "host": server.host,
                "connected": False,
                "error": conn_msg,
                "version": None,
                "running": False,
                "ui_healthy": False
            }
        
        version = await executor.get_current_version()
        running = await executor.check_n8n_running()
        
        # Check UI health if URL is configured
        ui_healthy = None
        if server.n8n_url:
            ui_healthy, _ = await check_n8n_health(server.n8n_url)
        
        return {
            "id": server.id,
            "name": server.name,
            "host": server.host,
            "connected": True,
            "version": version,
            "running": running,
            "ui_healthy": ui_healthy,
            "error": None
        }
        
    except Exception as e:
        return {
            "id": server.id,
            "name": server.name,
            "host": server.host,
            "connected": False,
            "error": str(e),
            "version": None,
            "running": False,
            "ui_healthy": False
        }
    finally:
        executor._close()


@dataclass
class HealthCheckResult:
    """Result of health check."""
    server_id: int
    server_name: str
    is_healthy: bool
    ssh_ok: bool
    container_running: bool
    ui_accessible: bool
    version: Optional[str]
    error: Optional[str]


async def check_n8n_health(url: str, timeout: int = 10) -> tuple[bool, Optional[str]]:
    """
    Check if n8n UI is accessible via HTTP.
    
    Args:
        url: n8n instance URL (e.g., https://n8n.example.com)
        timeout: Request timeout in seconds
        
    Returns:
        Tuple of (is_healthy, error_message)
    """
    try:
        # Normalize URL
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        
        # Try the healthz endpoint first, then fall back to root
        endpoints = ["/healthz", "/rest/health", "/"]
        
        async with aiohttp.ClientSession() as session:
            for endpoint in endpoints:
                try:
                    check_url = url.rstrip("/") + endpoint
                    async with session.get(
                        check_url,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        ssl=False  # Allow self-signed certs
                    ) as response:
                        if response.status < 500:
                            return True, None
                except aiohttp.ClientError:
                    continue
            
            return False, "All health endpoints failed"
            
    except asyncio.TimeoutError:
        return False, "Connection timeout"
    except aiohttp.ClientError as e:
        return False, f"Connection error: {str(e)}"
    except Exception as e:
        return False, f"Health check failed: {str(e)}"


async def perform_full_health_check(server: Server) -> HealthCheckResult:
    """
    Perform a comprehensive health check on a server.
    
    Checks:
    1. SSH connectivity
    2. Docker container running
    3. n8n UI accessible (if URL configured)
    
    Returns:
        HealthCheckResult with all check statuses
    """
    result = HealthCheckResult(
        server_id=server.id,
        server_name=server.name,
        is_healthy=False,
        ssh_ok=False,
        container_running=False,
        ui_accessible=False,
        version=None,
        error=None
    )
    
    executor = SSHExecutor(server)
    
    try:
        # Check SSH
        connected, ssh_error = await executor.test_connection()
        result.ssh_ok = connected
        
        if not connected:
            result.error = f"SSH: {ssh_error}"
            return result
        
        # Check container
        result.container_running = await executor.check_n8n_running()
        
        if not result.container_running:
            result.error = "n8n container is not running"
            return result
        
        # Get version
        result.version = await executor.get_current_version()
        
        # Check UI if URL configured
        if server.n8n_url:
            result.ui_accessible, ui_error = await check_n8n_health(server.n8n_url)
            if not result.ui_accessible:
                result.error = f"UI: {ui_error}"
                result.is_healthy = False
                return result
        else:
            # No URL configured, skip UI check
            result.ui_accessible = None
        
        # All checks passed
        result.is_healthy = True
        return result
        
    except Exception as e:
        result.error = str(e)
        return result
    finally:
        executor._close()
