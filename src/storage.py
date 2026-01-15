"""SQLite storage for dynamic configuration."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
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
            n8n_path=row["n8n_path"]
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers(id)
                );
            """)
        logger.info(f"Database initialized at {self.db_path}")
    
    # ============= Server Management =============
    
    def add_server(self, server: Server) -> int:
        """Add a new server. Returns server ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO servers (name, host, port, user, auth_type, ssh_key_path, ssh_password, n8n_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                server.name,
                server.host,
                server.port,
                server.user,
                server.auth_type,
                server.ssh_key_path,
                server.ssh_password,
                server.n8n_path
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
                    auth_type = ?, ssh_key_path = ?, ssh_password = ?, n8n_path = ?
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
                server.id
            ))
            return True
    
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
        message: str
    ):
        """Record an update attempt."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO update_history 
                (server_id, server_name, old_version, new_version, success, message)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (server_id, server_name, old_version, new_version, int(success), message))
    
    def get_update_history(self, limit: int = 20) -> list[dict]:
        """Get recent update history."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM update_history 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]


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
