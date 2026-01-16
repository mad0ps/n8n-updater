"""SQLite storage for dynamic configuration."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path("/app/data/n8n_updater.db")
LOCAL_DB_PATH = Path("data/n8n_updater.db")


@dataclass
class Server:
    """Server configuration."""
    
    id: Optional[int]
    name: str
    host: str
    port: int
    user: str
    auth_type: str  # "key" or "password"
    ssh_key_path: Optional[str]  # For key auth
    ssh_password: Optional[str]  # For password auth (encrypted in future)
    n8n_path: str
    n8n_url: Optional[str] = None  # URL for health checks (e.g., https://n8n.example.com)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Server":
        return cls(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            port=row["port"],
            user=row["user"],
            auth_type=row["auth_type"],
            ssh_key_path=row["ssh_key_path"],
            ssh_password=row["ssh_password"],
            n8n_path=row["n8n_path"],
            n8n_url=row["n8n_url"] if "n8n_url" in row.keys() else None
        )


@dataclass
class Settings:
    """Application settings."""
    
    admin_chat_id: Optional[int]
    check_interval_hours: int
    timezone: str
    last_known_version: Optional[str]


class Storage:
    """SQLite-based storage for configuration and state."""
    
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            # Try Docker path first, then local
            if DEFAULT_DB_PATH.parent.exists():
                db_path = DEFAULT_DB_PATH
            else:
                db_path = LOCAL_DB_PATH
        
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER DEFAULT 22,
                    user TEXT DEFAULT 'root',
                    auth_type TEXT DEFAULT 'password',
                    ssh_key_path TEXT,
                    ssh_password TEXT,
                    n8n_path TEXT DEFAULT '/opt/n8n-docker-caddy',
                    n8n_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                
                CREATE TABLE IF NOT EXISTS update_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER,
                    server_name TEXT,
                    old_version TEXT,
                    new_version TEXT,
                    success INTEGER,
                    message TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers(id)
                );
                
                CREATE TABLE IF NOT EXISTS server_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    server_name TEXT NOT NULL,
                    is_healthy INTEGER DEFAULT 1,
                    ssh_ok INTEGER DEFAULT 0,
                    container_running INTEGER DEFAULT 0,
                    ui_accessible INTEGER DEFAULT 0,
                    version TEXT,
                    last_check TIMESTAMP,
                    last_healthy TIMESTAMP,
                    error_message TEXT,
                    consecutive_failures INTEGER DEFAULT 0,
                    notified INTEGER DEFAULT 0,
                    FOREIGN KEY (server_id) REFERENCES servers(id),
                    UNIQUE(server_id)
                );
                
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    server_name TEXT NOT NULL,
                    compose_backup_path TEXT,
                    data_backup_path TEXT,
                    old_version TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used INTEGER DEFAULT 0,
                    FOREIGN KEY (server_id) REFERENCES servers(id)
                );
            """)
            
            # Add n8n_url column if it doesn't exist (for existing databases)
            try:
                conn.execute("ALTER TABLE servers ADD COLUMN n8n_url TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add details column to update_history if it doesn't exist
            try:
                conn.execute("ALTER TABLE update_history ADD COLUMN details TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add detailed health check columns to server_health if they don't exist
            for col in ["ssh_ok INTEGER DEFAULT 0", "container_running INTEGER DEFAULT 0", 
                        "ui_accessible INTEGER DEFAULT 0", "version TEXT"]:
                col_name = col.split()[0]
                try:
                    conn.execute(f"ALTER TABLE server_health ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                
        logger.info(f"Database initialized at {self.db_path}")
    
    # ============= Server Management =============
    
    def add_server(self, server: Server) -> int:
        """Add a new server. Returns server ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO servers (name, host, port, user, auth_type, ssh_key_path, ssh_password, n8n_path, n8n_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                server.name,
                server.host,
                server.port,
                server.user,
                server.auth_type,
                server.ssh_key_path,
                server.ssh_password,
                server.n8n_path,
                server.n8n_url
            ))
            return cursor.lastrowid
    
    def get_server(self, server_id: int) -> Optional[Server]:
        """Get server by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM servers WHERE id = ?", (server_id,)
            ).fetchone()
            return Server.from_row(row) if row else None
    
    def get_server_by_name(self, name: str) -> Optional[Server]:
        """Get server by name."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM servers WHERE name = ?", (name,)
            ).fetchone()
            return Server.from_row(row) if row else None
    
    def get_all_servers(self) -> list[Server]:
        """Get all servers."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM servers ORDER BY name").fetchall()
            return [Server.from_row(row) for row in rows]
    
    def update_server(self, server: Server) -> bool:
        """Update server configuration."""
        if server.id is None:
            return False
        
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE servers 
                SET name = ?, host = ?, port = ?, user = ?, 
                    auth_type = ?, ssh_key_path = ?, ssh_password = ?, n8n_path = ?, n8n_url = ?
                WHERE id = ?
            """, (
                server.name,
                server.host,
                server.port,
                server.user,
                server.auth_type,
                server.ssh_key_path,
                server.ssh_password,
                server.n8n_path,
                server.n8n_url,
                server.id
            ))
            return True
    
    def update_server_url(self, server_id: int, n8n_url: str) -> bool:
        """Update only the n8n URL for a server."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE servers SET n8n_url = ? WHERE id = ?",
                (n8n_url, server_id)
            )
            return cursor.rowcount > 0
    
    def delete_server(self, server_id: int) -> bool:
        """Delete server by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            return cursor.rowcount > 0
    
    def server_count(self) -> int:
        """Get number of servers."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM servers").fetchone()
            return row[0]
    
    # ============= Settings Management =============
    
    def get_setting(self, key: str, default: str = None) -> Optional[str]:
        """Get a setting value."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default
    
    def set_setting(self, key: str, value: str):
        """Set a setting value."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, value))
    
    def get_admin_chat_id(self) -> Optional[int]:
        """Get admin chat ID."""
        value = self.get_setting("admin_chat_id")
        return int(value) if value else None
    
    def set_admin_chat_id(self, chat_id: int):
        """Set admin chat ID."""
        self.set_setting("admin_chat_id", str(chat_id))
    
    def get_check_interval(self) -> int:
        """Get check interval in hours."""
        value = self.get_setting("check_interval_hours", "6")
        return int(value)
    
    def set_check_interval(self, hours: int):
        """Set check interval in hours."""
        self.set_setting("check_interval_hours", str(hours))
    
    def get_last_known_version(self) -> Optional[str]:
        """Get last known n8n version."""
        return self.get_setting("last_known_version")
    
    def set_last_known_version(self, version: str):
        """Set last known n8n version."""
        self.set_setting("last_known_version", version)
    
    # ============= Update History =============
    
    def add_update_history(
        self,
        server_id: int,
        server_name: str,
        old_version: str,
        new_version: str,
        success: bool,
        message: str,
        details: str = ""
    ):
        """Record an update attempt."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO update_history 
                (server_id, server_name, old_version, new_version, success, message, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (server_id, server_name, old_version, new_version, int(success), message, details))
    
    def get_update_history(self, limit: int = 20, server_id: int = None) -> list[dict]:
        """Get recent update history."""
        with self._get_connection() as conn:
            if server_id:
                rows = conn.execute("""
                    SELECT * FROM update_history 
                    WHERE server_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (server_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM update_history 
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(row) for row in rows]
    
    def get_update_history_entry(self, entry_id: int) -> Optional[dict]:
        """Get a specific update history entry."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM update_history WHERE id = ?", (entry_id,)
            ).fetchone()
            return dict(row) if row else None
    
    # ============= Server Health Monitoring =============
    
    def update_server_health(
        self,
        server_id: int,
        server_name: str,
        is_healthy: bool,
        error_message: str = None,
        ssh_ok: bool = False,
        container_running: bool = False,
        ui_accessible: bool = False,
        version: str = None
    ):
        """Update server health status with detailed check results."""
        with self._get_connection() as conn:
            now = datetime.now().isoformat()
            
            # Get current state
            row = conn.execute(
                "SELECT * FROM server_health WHERE server_id = ?", (server_id,)
            ).fetchone()
            
            if row:
                if is_healthy:
                    conn.execute("""
                        UPDATE server_health 
                        SET is_healthy = 1, ssh_ok = ?, container_running = ?, 
                            ui_accessible = ?, version = ?, last_check = ?, last_healthy = ?,
                            error_message = NULL, consecutive_failures = 0, notified = 0
                        WHERE server_id = ?
                    """, (int(ssh_ok), int(container_running), int(ui_accessible), version, now, now, server_id))
                else:
                    consecutive = row["consecutive_failures"] + 1
                    conn.execute("""
                        UPDATE server_health 
                        SET is_healthy = 0, ssh_ok = ?, container_running = ?,
                            ui_accessible = ?, version = ?, last_check = ?, error_message = ?,
                            consecutive_failures = ?
                        WHERE server_id = ?
                    """, (int(ssh_ok), int(container_running), int(ui_accessible), version, now, error_message, consecutive, server_id))
            else:
                # Insert new record
                last_healthy = now if is_healthy else None
                conn.execute("""
                    INSERT INTO server_health 
                    (server_id, server_name, is_healthy, ssh_ok, container_running, ui_accessible, 
                     version, last_check, last_healthy, error_message, consecutive_failures)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (server_id, server_name, int(is_healthy), int(ssh_ok), int(container_running), 
                      int(ui_accessible), version, now, last_healthy, error_message, 0 if is_healthy else 1))
    
    def get_server_health(self, server_id: int) -> Optional[dict]:
        """Get health status for a server."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM server_health WHERE server_id = ?", (server_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def get_all_server_health(self) -> list[dict]:
        """Get health status for all servers."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM server_health ORDER BY server_name"
            ).fetchall()
            return [dict(row) for row in rows]
    
    def get_unhealthy_servers_for_notification(self, min_failures: int = 2) -> list[dict]:
        """Get unhealthy servers that need notification."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM server_health 
                WHERE is_healthy = 0 
                AND consecutive_failures >= ? 
                AND notified = 0
            """, (min_failures,)).fetchall()
            return [dict(row) for row in rows]
    
    def mark_server_notified(self, server_id: int):
        """Mark server as notified about failure."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE server_health SET notified = 1 WHERE server_id = ?",
                (server_id,)
            )
    
    # ============= Backup Management =============
    
    def save_backup_info(
        self,
        server_id: int,
        server_name: str,
        compose_backup_path: str,
        data_backup_path: Optional[str],
        old_version: str
    ) -> int:
        """Save backup information. Returns backup ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO backups 
                (server_id, server_name, compose_backup_path, data_backup_path, old_version)
                VALUES (?, ?, ?, ?, ?)
            """, (server_id, server_name, compose_backup_path, data_backup_path, old_version))
            return cursor.lastrowid
    
    def get_backup(self, backup_id: int) -> Optional[dict]:
        """Get backup by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM backups WHERE id = ?", (backup_id,)
            ).fetchone()
            return dict(row) if row else None
    
    def get_last_backup(self, server_id: int) -> Optional[dict]:
        """Get the most recent unused backup for a server."""
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT * FROM backups 
                WHERE server_id = ? AND used = 0
                ORDER BY created_at DESC 
                LIMIT 1
            """, (server_id,)).fetchone()
            return dict(row) if row else None
    
    def mark_backup_used(self, backup_id: int):
        """Mark backup as used (after rollback)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE backups SET used = 1 WHERE id = ?",
                (backup_id,)
            )
    
    def delete_old_backups(self, server_id: int, keep_count: int = 3):
        """Delete old backups, keeping only the most recent ones."""
        with self._get_connection() as conn:
            conn.execute("""
                DELETE FROM backups 
                WHERE server_id = ? AND id NOT IN (
                    SELECT id FROM backups 
                    WHERE server_id = ? 
                    ORDER BY created_at DESC 
                    LIMIT ?
                )
            """, (server_id, server_id, keep_count))


# Global storage instance
_storage: Optional[Storage] = None


def get_storage() -> Storage:
    """Get global storage instance."""
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage


def init_storage(db_path: Optional[Path] = None) -> Storage:
    """Initialize global storage instance."""
    global _storage
    _storage = Storage(db_path)
    return _storage
