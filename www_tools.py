"""
Polaris Gateway - WWW Folder Management Toolkit

Provides tools to read, list, and manage web assets in the playground www folder
served by polaris-playground on port 8090.
"""

import os
import json
import shutil
from datetime import datetime
from typing import Dict, List, Optional


WWW_DIR = os.environ.get("WWW_DIR", "/playground/www")


def list_www_directory(path: str = "") -> Dict:
    """List contents of a directory in the www folder."""
    try:
        full_path = os.path.join(WWW_DIR, path) if path else WWW_DIR
        
        if not os.path.exists(full_path):
            return {"success": False, "error": f"Path not found: {full_path}"}
        
        if not os.path.isdir(full_path):
            return {"success": False, "error": f"Path is not a directory: {full_path}"}
        
        items = []
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            stat = os.stat(item_path)
            items.append({
                "name": item,
                "type": "directory" if os.path.isdir(item_path) else "file",
                "size_bytes": stat.st_size if os.path.isfile(item_path) else 0,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        
        items.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "path": full_path,
            "relative_path": path or "/",
            "item_count": len(items),
            "items": items
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to list directory: {str(e)}"}


def read_www_file(file_path: str) -> Dict:
    """Read a file from the www folder."""
    try:
        full_path = os.path.join(WWW_DIR, file_path)
        
        if not os.path.exists(full_path):
            return {"success": False, "error": f"File not found: {full_path}"}
        
        if not os.path.isfile(full_path):
            return {"success": False, "error": f"Path is not a file: {full_path}"}
        
        file_size = os.path.getsize(full_path)
        if file_size > 1024 * 1024:
            return {"success": False, "error": f"File too large ({file_size} bytes). Max 1MB."}
        
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        return {


def write_www_file(file_path: str, content: str, backup: bool = True) -> Dict:
    """Write content to a file in the www folder."""
    try:
        full_path = os.path.join(WWW_DIR, file_path)
        dir_path = os.path.dirname(full_path)
        
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        
        if backup and os.path.exists(full_path):
            backup_file = f"{full_path}.backup.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            with open(full_path, 'r') as f:
                original = f.read()
            with open(backup_file, 'w') as f:
                f.write(original)
        
        with open(full_path, 'w') as f:
            f.write(content)
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": full_path,
            "relative_path": file_path,
            "size_bytes": len(content),
            "line_count": content.count('\n') + 1,
            "backup_created": backup,
            "message": "File written successfully"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to write file: {str(e)}"}


def delete_www_file(file_path: str) -> Dict:
    """Delete a file from the www folder."""
    try:
        full_path = os.path.join(WWW_DIR, file_path)
        
        if not os.path.exists(full_path):
            return {"success": False, "error": f"File not found: {full_path}"}
        
        if not os.path.isfile(full_path):
            return {"success": False, "error": f"Path is not a file: {full_path}"}
        
        os.remove(full_path)
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": full_path,
            "relative_path": file_path,
            "message": "File deleted successfully"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to delete file: {str(e)}"}


def search_in_www(pattern: str, file_extension: str = ".html") -> Dict:
    """Search for a pattern in www folder files."""
    try:
        matches = []
        
        for root, dirs, files in os.walk(WWW_DIR):
            for file in files:
                if file_extension and not file.endswith(file_extension):
                    continue
                
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                    
                    for i, line in enumerate(lines, 1):
                        if pattern.lower() in line.lower():
                            matches.append({
                                "file": os.path.relpath(file_path, WWW_DIR),
                                "line_number": i,
                                "content": line.strip()[:200]
                            })
                except Exception:
                    continue
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "pattern": pattern,
            "file_extension": file_extension,
            "matches_found": len(matches),
            "matches": matches[:100]
        }
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}"}


def get_www_info() -> Dict:
    """Get information about the www folder setup."""
    try:
        if not os.path.exists(WWW_DIR):
            return {"success": False, "error": f"WWW directory not found at {WWW_DIR}"}
        
        total_files = 0
        total_dirs = 0
        total_size = 0
        
        for root, dirs, files in os.walk(WWW_DIR):
            total_dirs += len(dirs)
            for file in files:
                total_files += 1
                file_path = os.path.join(root, file)
                try:
                    total_size += os.path.getsize(file_path)
                except Exception:
                    pass
        
        return {
            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "www_dir": WWW_DIR,
            "total_files": total_files,
            "total_directories": total_dirs,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "status": "active"
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to get www info: {str(e)}"}

            "success": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file": full_path,
            "relative_path": file_path,
            "size_bytes": file_size,
            "line_count": content.count('\n') + 1,
            "content": content
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to read file: {str(e)}"}
