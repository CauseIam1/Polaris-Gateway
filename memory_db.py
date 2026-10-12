#!/usr/bin/env python3
"""
Polaris Memory Database Manager - SQLite Layer
Handles relational data: user identities, chat histories, and heuristics approval pipeline
"""

import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Database path - /data/state is a compose bind mount (host dir:
# /mnt/containers/freeroam/polaris-gateway/state) so the SQLite store
# survives container recreates. Previously /data (container layer - wiped
# on every recreate).
DEFAULT_DB_PATH = "/data/state/polaris_state.db"

# Thread lock for database operations
DB_LOCK = threading.Lock()


class PolarisMemoryDB:
    """SQLite database manager for Polaris Gateway relational data."""
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        # Ensure the parent directory exists (defensive: bind mount may be
        # missing in non-compose dev environments)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize database schema and seed default data."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # User Profiles & Device Identities
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    ip_last_octet TEXT UNIQUE,
                    profile_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Unified Chat History Across All Interfaces
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    client_source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Index for fast user-based queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_chat_history_user_id 
                ON chat_history(user_id)
            ''')
            
            # Index for timestamp-based queries (recent messages)
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_chat_history_timestamp 
                ON chat_history(timestamp DESC)
            ''')
            
            # Staged Learning Rules & Heuristics Approval Pipeline
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS heuristics (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    rule_category TEXT NOT NULL,
                    rule_content TEXT NOT NULL,
                    confidence_score REAL DEFAULT 0.5,
                    status TEXT DEFAULT 'staged',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Index for status-based filtering
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_heuristics_status 
                ON heuristics(status)
            ''')
            
            # Seed default IP identities
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, display_name, ip_last_octet)
                VALUES 
                    ('rich', 'Rich', '.42'),
                    ('matt', 'Matt', '.98'),
                    ('operator', 'Operator', '.51')
            ''')
            
            conn.commit()
    
    # ========== USER MANAGEMENT ==========
    
    def get_user_by_ip(self, ip_address: str) -> Optional[Dict[str, Any]]:
        """Resolve user identity from IP last octet."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Extract last octet
            last_octet = ip_address.split('.')[-1] if '.' in ip_address else ip_address
            
            cursor.execute('''
                SELECT user_id, display_name, ip_last_octet, profile_json, created_at
                FROM users
                WHERE ip_last_octet = ?
            ''', (f'.{last_octet}',))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
    
    def create_or_update_user(self, user_id: str, display_name: str, 
                             ip_last_octet: Optional[str] = None,
                             profile_json: Optional[str] = None) -> bool:
        """Create new user or update existing profile."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, display_name, ip_last_octet, profile_json)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, display_name, ip_last_octet, profile_json))
                
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[MemoryDB] Error creating/updating user: {e}")
                conn.close()
                return False
    
    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile by user_id."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT user_id, display_name, ip_last_octet, profile_json, created_at
                FROM users
                WHERE user_id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return dict(row)
            return None
    
    def update_user_profile(self, user_id: str, profile_json: str) -> bool:
        """Update user profile JSON."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    UPDATE users
                    SET profile_json = ?
                    WHERE user_id = ?
                ''', (profile_json, user_id))
                
                conn.commit()
                conn.close()
                return cursor.rowcount > 0
            except Exception as e:
                print(f"[MemoryDB] Error updating user profile: {e}")
                conn.close()
                return False
    
    # ========== CHAT HISTORY MANAGEMENT ==========
    
    def add_chat_message(self, user_id: str, sender: str, message: str,
                        client_source: str = 'web') -> bool:
        """Add a message to chat history."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO chat_history (user_id, sender, client_source, message)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, sender, client_source, message))
                
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[MemoryDB] Error adding chat message: {e}")
                conn.close()
                return False
    
    def get_recent_chat_history(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent chat messages for a user."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    SELECT id, user_id, sender, client_source, message, timestamp
                    FROM chat_history
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ''', (user_id, limit))
                
                rows = cursor.fetchall()
                conn.close()
                
                return [{
                    'id': row[0],
                    'user_id': row[1],
                    'sender': row[2],
                    'client_source': row[3],
                    'message': row[4],
                    'timestamp': row[5]
                } for row in rows]
            except Exception as e:
                print(f"[MemoryDB] Error fetching recent chat history: {e}")
                conn.close()
                return []
    
    def get_all_chat_history(self, user_id: str) -> List[Dict[str, Any]]:
        """Get complete chat history for a user."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id, user_id, sender, client_source, message, timestamp
                FROM chat_history
                WHERE user_id = ?
                ORDER BY timestamp ASC
            ''', (user_id,))
            
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
    
    def clear_chat_history(self, user_id: str) -> bool:
        """Clear all chat history for a user."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('DELETE FROM chat_history WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[MemoryDB] Error clearing chat history: {e}")
                conn.close()
                return False
            conn.close()
            print(f"[MemoryDB] Initialized SQLite database at {self.db_path}")
    
    # ========== HEURISTICS MANAGEMENT ==========
    
    def add_heuristic(self, user_id: str, rule_category: str, rule_content: str,
                     confidence_score: float = 0.5, status: str = 'staged') -> bool:
        """Add a new heuristic rule."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO heuristics (user_id, rule_category, rule_content, 
                                           confidence_score, status)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, rule_category, rule_content, confidence_score, status))
                
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[MemoryDB] Error adding heuristic: {e}")
                conn.close()
                return False
    
    def get_heuristics_by_status(self, status: str, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get heuristics filtered by status (staged/approved/rejected)."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if user_id:
                cursor.execute('''
                    SELECT rule_id, user_id, rule_category, rule_content, 
                           confidence_score, status, created_at, updated_at
                    FROM heuristics
                    WHERE status = ? AND user_id = ?
                    ORDER BY created_at DESC
                ''', (status, user_id))
            else:
                cursor.execute('''
                    SELECT rule_id, user_id, rule_category, rule_content, 
                           confidence_score, status, created_at, updated_at
                    FROM heuristics
                    WHERE status = ?
                    ORDER BY created_at DESC
                ''', (status,))
            
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
    
    def get_all_heuristics(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all heuristics, optionally filtered by user."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if user_id:
                cursor.execute('''
                    SELECT rule_id, user_id, rule_category, rule_content, 
                           confidence_score, status, created_at, updated_at
                    FROM heuristics
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT rule_id, user_id, rule_category, rule_content, 
                           confidence_score, status, created_at, updated_at
                    FROM heuristics
                    ORDER BY created_at DESC
                ''')
            
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]
    
    def update_heuristic_status(self, rule_id: int, status: str) -> bool:
        """Update heuristic status (approve/reject)."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    UPDATE heuristics
                    SET status = ?, updated_at = ?
                    WHERE rule_id = ?
                ''', (status, datetime.now().isoformat(), rule_id))
                conn.commit()
                conn.close()
                return cursor.rowcount > 0
            except Exception as e:
                print(f"[MemoryDB] Error updating heuristic status: {e}")
                conn.close()
                return False
    
    def update_heuristic_content(self, rule_id: int, rule_content: str,
                                rule_category: Optional[str] = None,
                                confidence_score: Optional[float] = None) -> bool:
        """Update heuristic content, category, or confidence score."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                if rule_category and confidence_score is not None:
                    cursor.execute('''
                        UPDATE heuristics
                        SET rule_content = ?, rule_category = ?, 
                            confidence_score = ?, updated_at = ?
                        WHERE rule_id = ?
                    ''', (rule_content, rule_category, confidence_score, 
                         datetime.now().isoformat(), rule_id))
                elif rule_category:
                    cursor.execute('''
                        UPDATE heuristics
                        SET rule_content = ?, rule_category = ?, updated_at = ?
                        WHERE rule_id = ?
                    ''', (rule_content, rule_category, datetime.now().isoformat(), rule_id))
                elif confidence_score is not None:
                    cursor.execute('''
                        UPDATE heuristics
                        SET rule_content = ?, confidence_score = ?, updated_at = ?
                        WHERE rule_id = ?
                    ''', (rule_content, confidence_score, datetime.now().isoformat(), rule_id))
                else:
                    cursor.execute('''
                        UPDATE heuristics
                        SET rule_content = ?, updated_at = ?
                        WHERE rule_id = ?
                    ''', (rule_content, datetime.now().isoformat(), rule_id))
                
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[MemoryDB] Error updating heuristic content: {e}")
                conn.close()
                return False
    
    def delete_heuristic(self, rule_id: int) -> bool:
        """Delete a heuristic rule."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('DELETE FROM heuristics WHERE rule_id = ?', (rule_id,))
                conn.commit()
                conn.close()
                return cursor.rowcount > 0
            except Exception as e:
                print(f"[MemoryDB] Error deleting heuristic: {e}")
                conn.close()
                return False
    
    def get_approved_heuristics_dict(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all approved heuristics organized by category."""
        approved = self.get_heuristics_by_status('approved')
        
        categorized = {}
        for heuristic in approved:
            category = heuristic['rule_category']
            if category not in categorized:
                categorized[category] = []
            categorized[category].append({
                'rule_id': heuristic['rule_id'],
                'content': heuristic['rule_content'],
                'confidence': heuristic['confidence_score']
            })
        
        return categorized
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics for health monitoring."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                # Count users
                cursor.execute('SELECT COUNT(*) FROM users')
                total_users = cursor.fetchone()[0]
                
                # Count chat messages
                cursor.execute('SELECT COUNT(*) FROM chat_history')
                total_messages = cursor.fetchone()[0]
                
                # Count heuristics
                cursor.execute('SELECT COUNT(*) FROM heuristics')
                total_heuristics = cursor.fetchone()[0]
                
                # Count approved heuristics
                cursor.execute("SELECT COUNT(*) FROM heuristics WHERE status = 'approved'")
                approved_heuristics = cursor.fetchone()[0]
                
                # Count staged heuristics
                cursor.execute("SELECT COUNT(*) FROM heuristics WHERE status = 'staged'")
                staged_heuristics = cursor.fetchone()[0]
                
                conn.close()
                
                return {
                    'total_users': total_users,
                    'total_messages': total_messages,
                    'total_heuristics': total_heuristics,
                    'approved_heuristics': approved_heuristics,
                    'staged_heuristics': staged_heuristics
                }
            except Exception as e:
                print(f"[MemoryDB] Error getting stats: {e}")
                conn.close()
                return {
                    'total_users': 0,
                    'total_messages': 0,
                    'total_heuristics': 0,
                    'approved_heuristics': 0,
                    'staged_heuristics': 0,
                    'error': str(e)
                }
    
    def list_users(self) -> List[Dict[str, Any]]:
        """List all users in the database."""
        with DB_LOCK:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute('SELECT user_id, display_name, ip_last_octet, created_at FROM users')
                rows = cursor.fetchall()
                conn.close()
                
                return [dict(row) for row in rows]
            except Exception as e:
                print(f"[MemoryDB] Error listing users: {e}")
                conn.close()
                return []
    
    def migrate_from_json(self, chat_history_file: str = '/data/polaris_chat_history.json') -> bool:
        """Migrate existing JSON chat history to SQLite."""
        try:
            if not Path(chat_history_file).exists():
                print(f"[MemoryDB] No JSON history file found at {chat_history_file}")
                return False
            
            with open(chat_history_file, 'r') as f:
                chat_data = json.load(f)
            
            migrated_count = 0
            for user_id, messages in chat_data.items():
                if user_id.lower() in ['rich', 'matt', 'operator']:
                    self.create_or_update_user(
                        user_id=user_id.lower(),
                        display_name=user_id.capitalize(),
                        ip_last_octet=None
                    )
                
                for msg in messages:
                    self.add_chat_message(
                        user_id=user_id.lower(),
                        sender=msg.get('role', 'user'),
                        message=msg.get('content', ''),
                        client_source='migrated_json'
                    )
                    migrated_count += 1
            
            print(f"[MemoryDB] Migrated {migrated_count} messages from JSON to SQLite")
            return True
        except Exception as e:
            print(f"[MemoryDB] Migration error: {e}")
            return False


# Singleton instance for easy import
_memory_db_instance: Optional[PolarisMemoryDB] = None


def get_memory_db() -> PolarisMemoryDB:
    """Get or create the singleton MemoryDB instance."""
    global _memory_db_instance
    if _memory_db_instance is None:
        _memory_db_instance = PolarisMemoryDB()
    return _memory_db_instance