"""
Polaris Gateway - Gotify Notification Service

Asynchronous status reporting and notification delivery for the Polaris ecosystem.
Ported from Java trading-bot GotifyService implementation.

Configuration via environment variables:
    GOTIFY_URL: Base URL for Gotify server (e.g., http://gotify:80 or https://gotify.example.com)
    GOTIFY_TOKEN: App token for authentication
    GOTIFY_ENABLED: Enable/disable notifications (default: false)
"""

import os
import json
import logging
import threading
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class GotifyMessage:
    """Represents a Gotify notification message."""
    title: str
    message: str
    priority: int = 5
    extras: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "title": self.title,
            "message": self.message,
            "priority": self.priority
        }
        if self.extras:
            result["extras"] = self.extras
        return result


class GotifyService:
    """
    Gotify notification service for asynchronous status reporting.
    
    Provides methods for sending immediate notifications and scheduling
    periodic status digests.
    """
    
    def __init__(
        self,
        gotify_url: Optional[str] = None,
        gotify_token: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        """
        Initialize the Gotify service.
        
        Args:
            gotify_url: Base URL for Gotify server (env: GOTIFY_URL)
            gotify_token: App token for authentication (env: GOTIFY_TOKEN)
            enabled: Enable/disable notifications (env: GOTIFY_ENABLED)
        """
        # Load configuration from environment or parameters
        self.gotify_url = gotify_url or os.environ.get("GOTIFY_URL", "")
        self.gotify_token = gotify_token or os.environ.get("GOTIFY_TOKEN", "")
        self.enabled = enabled if enabled is not None else os.environ.get("GOTIFY_ENABLED", "false").lower() == "true"
        
        # Validate configuration
        self._is_configured = bool(self.gotify_url and self.gotify_token and self.enabled)
        
        # Session for connection pooling
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-Gotify-Key": self.gotify_token
        })
        
        # Scheduler state
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_scheduler = threading.Event()
        self._digest_interval_seconds = 3600  # Default: 1 hour
        self._pending_digests: List[Dict] = []
        self._lock = threading.Lock()
        
        if self._is_configured:
            logger.info(f"Gotify service initialized: {self.gotify_url}")
        else:
            logger.warning("Gotify service disabled - missing URL, token, or enabled flag")
    
    @property
    def is_configured(self) -> bool:
        """Check if the service is properly configured."""
        return self._is_configured
    
    @property
    def is_enabled(self) -> bool:
        """Check if the service is enabled."""
        return self.enabled
    
    def send_notification(
        self,
        title: str,
        message: str,
        priority: int = 5,
        extras: Optional[Dict[str, Any]] = None
    ) -> Dict:
        """
        Send an immediate notification to Gotify.
        
        Args:
            title: Notification title
            message: Notification body
            priority: Message priority (1-10, default 5)
            extras: Optional additional data (e.g., click URL, action buttons)
        
        Returns:
            Dict with success status and response details
        """
        if not self._is_configured:
            return {
                "success": False,
                "error": "Gotify service not configured",
                "configured": False
            }
        
        try:
            msg = GotifyMessage(
                title=title,
                message=message,
                priority=priority,
                extras=extras
            )
            
            endpoint = f"{self.gotify_url.rstrip('/')}/message"
            response = self._session.post(endpoint, json=msg.to_dict(), timeout=10)
            
            if response.status_code in [200, 201]:
                logger.info(f"Gotify notification sent: {title}")
                return {
                    "success": True,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "title": title,
                    "status_code": response.status_code
                }
            else:
                logger.error(f"Gotify API error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"Gotify API error: {response.status_code}",
                    "response": response.text
                }
                
        except requests.exceptions.Timeout:
            logger.error("Gotify request timeout")
            return {"success": False, "error": "Request timeout"}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Gotify connection error: {str(e)}")
            return {"success": False, "error": f"Connection error: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error sending notification: {str(e)}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}
    
    def send_status_digest(self, digest_data: Dict[str, Any]) -> Dict:
        """
        Send a status digest notification.
        
        Args:
            digest_data: Dictionary containing status information to include in digest
        
        Returns:
            Dict with success status
        """
        if not self._is_configured:
            return {"success": False, "error": "Gotify service not configured"}
        
        try:
            timestamp = digest_data.get("timestamp", datetime.utcnow().isoformat() + "Z")
            status = digest_data.get("status", "unknown")
            details = digest_data.get("details", {})
            
            message_lines = [f"Status: {status}"]
            for key, value in details.items():
                message_lines.append(f"• {key}: {value}")
            
            message = "\n".join(message_lines)
            
            return self.send_notification(
                title="📊 Polaris Status Digest",
                message=message,
                priority=3,
                extras={
                    "client::display": {
                        "contentType": "text/markdown"
                    }
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to send status digest: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def start_digest_scheduler(
        self,
        interval_seconds: int = 3600,
        digest_callback: Optional[Callable] = None
    ):
        """
        Start background scheduler for periodic status digests.
        
        Args:
            interval_seconds: Time between digests (default: 3600 = 1 hour)
            digest_callback: Optional function to call for generating digest data
        """
        if not self._is_configured:
            logger.warning("Cannot start scheduler - Gotify not configured")
            return
        
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            logger.warning("Digest scheduler already running")
            return
        
        self._digest_interval_seconds = interval_seconds
        self._stop_scheduler.clear()
        
        def scheduler_loop():
            """Background thread loop for scheduled digests."""
            logger.info(f"Gotify digest scheduler started (interval: {interval_seconds}s)")
            
            while not self._stop_scheduler.is_set():
                try:
                    if self._stop_scheduler.wait(timeout=interval_seconds):
                        break
                    
                    if digest_callback:
                        try:
                            digest_data = digest_callback()
                            if digest_data:
                                self.send_status_digest(digest_data)
                        except Exception as e:
                            logger.error(f"Digest callback failed: {str(e)}")
                    else:
                        self.send_status_digest({
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                            "status": "running",
                            "details": {"uptime": "active"}
                        })
                        
                except Exception as e:
                    logger.error(f"Scheduler loop error: {str(e)}")
            
            logger.info("Gotify digest scheduler stopped")
        
        self._scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._scheduler_thread.start()
    
    def stop_digest_scheduler(self):
        """Stop the background digest scheduler."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            logger.info("Stopping Gotify digest scheduler...")
            self._stop_scheduler.set()
            self._scheduler_thread.join(timeout=5)
            logger.info("Gotify digest scheduler stopped")
    
    def queue_digest(self, digest_data: Dict[str, Any]):
        """
        Queue a digest for sending (thread-safe).
        
        Args:
            digest_data: Dictionary containing digest information
        """
        with self._lock:
            self._pending_digests.append({
                "data": digest_data,
                "queued_at": datetime.utcnow().isoformat() + "Z"
            })
            logger.info(f"Digest queued (queue size: {len(self._pending_digests)})")
    
    def process_queued_digests(self) -> int:
        """
        Process and send all queued digests.
        
        Returns:
            Number of digests processed
        """
        with self._lock:
            digests_to_send = self._pending_digests.copy()
            self._pending_digests.clear()
        
        processed = 0
        for item in digests_to_send:
            result = self.send_status_digest(item["data"])
            if result.get("success"):
                processed += 1
            else:
                logger.warning(f"Failed to send queued digest: {result.get('error')}")
        
        return processed
    
    def health_check(self) -> Dict:
        """
        Perform a health check on the Gotify service.
        
        Returns:
            Dict with health status and configuration details
        """
        health = {
            "configured": self._is_configured,
            "enabled": self.enabled,
            "url_configured": bool(self.gotify_url),
            "token_configured": bool(self.gotify_token),
            "scheduler_running": self._scheduler_thread and self._scheduler_thread.is_alive()
        }
        
        if not self._is_configured:
            health["status"] = "not_configured"
            return health
        
        try:
            response = self._session.get(
                f"{self.gotify_url.rstrip('/')}/health",
                timeout=5
            )
            health["server_reachable"] = response.status_code == 200
            health["status"] = "healthy" if health["server_reachable"] else "unreachable"
        except Exception as e:
            health["server_reachable"] = False
            health["status"] = "error"
            health["error"] = str(e)
        
        return health
    
    def shutdown(self):
        """Gracefully shutdown the service."""
        self.stop_digest_scheduler()
        self._session.close()
        logger.info("Gotify service shutdown complete")


# Tool schemas for LLM function calling
GOTIFY_TOOL_SCHEMAS = [
    {
        "name": "send_gotify_notification",
        "description": "Send a push notification via Gotify to all connected devices. Use for status updates, alerts, trading notifications, or system events. Does NOT require SSH or remote-device interaction.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Notification title (e.g., 'Trading Alert', 'System Update')"
                },
                "message": {
                    "type": "string",
                    "description": "Notification body with the actual message content"
                },
                "priority": {
                    "type": "integer",
                    "description": "Message priority (1-10, default: 5). Use 8-10 for urgent alerts, 1-3 for low-priority updates",
                    "default": 5
                },
                "extras": {
                    "type": "object",
                    "description": "Optional additional data (e.g., click URL, icon, custom metadata)"
                }
            },
            "required": ["title", "message"]
        }
    }
]


def get_gotify_tool_schemas_json() -> str:
    """Return Gotify tool schemas as JSON string for LLM system prompt."""
    return json.dumps(GOTIFY_TOOL_SCHEMAS, indent=2)


def send_gotify_notification(
    title: str,
    message: str,
    priority: int = 5,
    extras: Optional[Dict[str, Any]] = None
) -> Dict:
    """
    Send a one-off Gotify notification without initializing the service class.
    
    Args:
        title: Notification title
        message: Notification body
        priority: Message priority (1-10)
        extras: Optional additional data
    
    Returns:
        Dict with success status
    """
    service = GotifyService()
    return service.send_notification(title, message, priority, extras)


# Export all tool functions
__all__ = [
    "send_gotify_notification",
    "GotifyService",
    "GotifyMessage",
    "GOTIFY_TOOL_SCHEMAS",
    "get_gotify_tool_schemas_json"
]
