"""a0_crosschatapi plugin installation hooks.

Handles dependency installation and verification for python-socketio.
The plugin requires Socket.IO for WebSocket support.
"""

import subprocess
import sys
import os
import json
from datetime import datetime

from helpers.print_style import PrintStyle

# Plugin directory (where this file lives)
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(PLUGIN_DIR, ".dependency_status.json")


def _write_status(status: dict) -> None:
    """Write dependency status to a JSON file for runtime checks."""
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        PrintStyle.warning(f"a0_crosschatapi: could not write status file: {e}")


def _check_socketio_module() -> bool:
    """Check if python-socketio module is importable."""
    try:
        import socketio  # noqa: F401
        return True
    except ImportError:
        return False


def _install_socketio() -> bool:
    """Install python-socketio via pip."""
    PrintStyle.info("[a0_crosschatapi] Installing python-socketio...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "python-socketio>=5.9.0"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            PrintStyle.success("[a0_crosschatapi] python-socketio installed successfully")
            return True
        else:
            PrintStyle.error(
                f"[a0_crosschatapi] Failed to install python-socketio: {result.stderr}"
            )
            return False
    except subprocess.TimeoutExpired:
        PrintStyle.error("[a0_crosschatapi] Installation timed out")
        return False
    except Exception as e:
        PrintStyle.error(f"[a0_crosschatapi] Installation error: {e}")
        return False


def install(**kwargs) -> bool:
    """Install hook called by the plugin system.
    
    Ensures python-socketio is installed and available for the plugin.
    Writes status to .dependency_status.json for runtime verification.
    """
    PrintStyle.info("[a0_crosschatapi] Installing plugin...")

    # Check if socketio is already available
    if _check_socketio_module():
        PrintStyle.success("[a0_crosschatapi] python-socketio already installed")
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
            PrintStyle.success("[a0_crosschatapi] Plugin installation complete")
            return True
        else:
            PrintStyle.error(
                "[a0_crosschatapi] Installation reported success but module not importable"
            )
            _write_status({
                "status": "error",
                "error": "Installation succeeded but socketio not importable",
                "timestamp": datetime.now().isoformat(),
            })
            return False
    else:
        PrintStyle.error("[a0_crosschatapi] Failed to install required dependencies")
        _write_status({
            "status": "error",
            "error": "Failed to install python-socketio",
            "timestamp": datetime.now().isoformat(),
        })
        return False


def pre_update(**kwargs) -> bool:
    """Called before plugin update."""
    PrintStyle.info("[a0_crosschatapi] Preparing for update...")
    return True


def uninstall(**kwargs) -> bool:
    """Called when plugin is uninstalled.
    
    Note: We do NOT uninstall python-socketio as it may be needed by other plugins.
    Just clean up our status file.
    """
    PrintStyle.info("[a0_crosschatapi] Uninstalling plugin...")
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except Exception as e:
        PrintStyle.warning(f"[a0_crosschatapi] Could not remove status file: {e}")
    return True
