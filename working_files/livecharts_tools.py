"""
Polaris Gateway - LiveCharts Management Toolkit

Provides tools to read, modify, and manage the LiveCharts dashboard
running on port 7001.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional


LIVECHARTS_DIR = os.environ.get("LIVECHARTS_DIR", "/livecharts")
LIVECHARTS_HTML = os.path.join(LIVECHARTS_DIR, "LiveCharts.html")
LIVECHARTS_NGINX_CONF = os.path.join(LIVECHARTS_DIR, "nginx.conf")


def read_livecharts_html() -> Dict:
    """Read the LiveCharts.html file content."""
    try:
        if not os.path.exists(LIVECHARTS_HTML):
            return {
                "success": False,
                "error": f"LiveCharts.html not found at {LIVECHARTS_HTML}",
                "message": "Ensure LiveCharts directory is mounted correctly"
            }
        
        with open(LIVECHARTS_HTML, 'r') as f:
            content = f.read()
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": LIVECHARTS_HTML,
            "size_bytes": len(content),
            "line_count": content.count('\n') + 1,
            "content": content
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to read LiveCharts.html: {str(e)}"}


def update_livecharts_html(content: str, backup: bool = True) -> Dict:
    """Update the LiveCharts.html file with new content."""
    try:
        if not os.path.exists(LIVECHARTS_DIR):
            return {
                "success": False,
                "error": f"LiveCharts directory not found at {LIVECHARTS_DIR}"
            }
        
        # Create backup if requested
        if backup and os.path.exists(LIVECHARTS_HTML):
            backup_file = f"{LIVECHARTS_HTML}.backup.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            with open(LIVECHARTS_HTML, 'r') as f:
                original = f.read()
            with open(backup_file, 'w') as f:
                f.write(original)
        
        # Write new content
        with open(LIVECHARTS_HTML, 'w') as f:
            f.write(content)
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": LIVECHARTS_HTML,
            "size_bytes": len(content),
            "line_count": content.count('\n') + 1,
            "backup_created": backup,
            "message": "LiveCharts.html updated successfully"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to update LiveCharts.html: {str(e)}"}


def search_in_livecharts(pattern: str) -> Dict:
    """Search for a pattern in LiveCharts.html."""
    try:
        if not os.path.exists(LIVECHARTS_HTML):
            return {"success": False, "error": "LiveCharts.html not found"}
        
        with open(LIVECHARTS_HTML, 'r') as f:
            lines = f.readlines()
        
        matches = []
        for i, line in enumerate(lines, 1):
            if pattern.lower() in line.lower():
                matches.append({
                    "line_number": i,
                    "content": line.strip()[:200]  # Truncate long lines
                })
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "pattern": pattern,
            "matches_found": len(matches),
            "matches": matches[:50]  # Limit to first 50 matches
        }
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}"}


def read_nginx_conf() -> Dict:
    """Read the LiveCharts nginx.conf file."""
    try:
        if not os.path.exists(LIVECHARTS_NGINX_CONF):
            return {
                "success": False,
                "error": f"nginx.conf not found at {LIVECHARTS_NGINX_CONF}"
            }
        
        with open(LIVECHARTS_NGINX_CONF, 'r') as f:
            content = f.read()
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": LIVECHARTS_NGINX_CONF,
            "content": content
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to read nginx.conf: {str(e)}"}


def get_livecharts_info() -> Dict:
    """Get information about the LiveCharts setup."""
    try:
        html_exists = os.path.exists(LIVECHARTS_HTML)
        nginx_exists = os.path.exists(LIVECHARTS_NGINX_CONF)
        
        html_size = os.path.getsize(LIVECHARTS_HTML) if html_exists else 0
        nginx_size = os.path.getsize(LIVECHARTS_NGINX_CONF) if nginx_exists else 0
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "livecharts_dir": LIVECHARTS_DIR,
            "html_file": {
                "exists": html_exists,
                "path": LIVECHARTS_HTML,
                "size_bytes": html_size
            },
            "nginx_conf": {
                "exists": nginx_exists,
                "path": LIVECHARTS_NGINX_CONF,
                "size_bytes": nginx_size
            },
            "status": "active" if html_exists else "missing"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get LiveCharts info: {str(e)}"}
