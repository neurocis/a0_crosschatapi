"""a0_crosschatapi plugin installation hooks.

Handles dependency installation and verification for python-socketio.
The plugin requires Socket.IO for WebSocket support.

Note: This module uses only stdlib imports (no helpers) to avoid import errors
during plugin discovery when webcolors or other optional dependencies are missing.
"""

import subprocess
import sys
import os
import json
from datetime import datetime

# Plugin directory (where this file lives)
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(PLUGIN_DIR, ".dependency_status.json")


def _log(level: str, msg: str) -> None:
    """Simple logging without framework dependencies."""
    timestamp = datetime.now().isoformat()
    print(f"[{timestamp}] [{level}] [a0_crosschatapi] {msg}")


def _write_status(status: dict) -> None:
    """Write dependency status to a JSON file for runtime checks."""
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        _log("WARN", f"Could not write status file: {e}")


def _check_socketio_module() -> bool:
    """Check if python-socketio module is importable."""
    try:
        import socketio  # noqa: F401
        return True
    except ImportError:
        return False


def _install_socketio() -> bool:
    """Install python-socketio via pip."""
    _log("INFO", "Installing python-socketio...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "python-socketio>=5.9.0"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            _log("INFO", "python-socketio installed successfully")
            return True
        else:
            _log("ERROR", f"Failed to install python-socketio: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        _log("ERROR", "Installation timed out")
        return False
    except Exception as e:
        _log("ERROR", f"Installation error: {e}")
        return False


def install(**kwargs) -> bool:
    """Install hook called by the plugin system.
    
    Ensures python-socketio is installed and available for the plugin.
    Writes status to .dependency_status.json for runtime verification.
    """
    _log("INFO", "Installing plugin...")

    # Check if socketio is already available
    if _check_socketio_module():
        _log("INFO", "python-socketio already installed")
        _write_status({
            "status": "ready",
            "installed_at": datetime.now().isoformat(),
            "socketio": True,
        })
        return True

    # Attempt to install
    if _install_socketio():
        # Verify installation
        if _check_socketio_module():
            _write_status({
                "status": "ready",
                "installed_at": datetime.now().isoformat(),
                "socketio": True,
            })
            _log("INFO", "Plugin installation complete")
            return True
        else:
            _log("ERROR", "Installation reported success but module not importable")
            _write_status({
                "status": "error",
                "error": "Installation succeeded but socketio not importable",
                "timestamp": datetime.now().isoformat(),
            })
            return False
    else:
        _log("ERROR", "Failed to install required dependencies")
        _write_status({
            "status": "error",
            "error": "Failed to install python-socketio",
            "timestamp": datetime.now().isoformat(),
        })
        return False


def pre_update(**kwargs) -> bool:
    """Called before plugin update."""
    _log("INFO", "Preparing for update...")
    return True


def uninstall(**kwargs) -> bool:
    """Called when plugin is uninstalled.
    
    Note: We do NOT uninstall python-socketio as it may be needed by other plugins.
    Just clean up our status file.
    """
    _log("INFO", "Uninstalling plugin...")
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except Exception as e:
        _log("WARN", f"Could not remove status file: {e}")
    return True
