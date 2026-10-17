#!/usr/bin/env python3
"""
VELVET Remote Access via Tailscale
Secure VPN-based remote access using Tailscale
"""

import subprocess
import threading
import time
import shutil
from typing import Optional

# VELVET server port
VELVET_PORT = 8765


class TailscaleManager:
    """Manages Tailscale connection for remote access"""

    def __init__(self):
        self.status = "unknown"  # unknown, not_installed, not_running, running
        self.ipv4: Optional[str] = None
        self.ipv6: Optional[str] = None
        self.hostname: Optional[str] = None
        self.dns_name: Optional[str] = None
        self._serve_last_check = 0.0
        self._serve_https_ready = False
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Check if Tailscale CLI is available"""
        return shutil.which("tailscale") is not None

    def get_status(self) -> dict:
        """Get current Tailscale status"""
        with self._lock:
            self._update_status()
            self._check_serve_https()
            return {
                "status": self.status,
                "ip": self.ipv4,
                "ipv6": self.ipv6,
                "hostname": self.hostname,
                "url": f"http://{self.ipv4}:{VELVET_PORT}" if self.ipv4 else None,
                "secure_url": f"https://{self.dns_name}" if self._serve_https_ready and self.dns_name else None,
                "https_ready": self._serve_https_ready,
                "https_setup_command": f"tailscale serve --bg {VELVET_PORT}",
                "https_note": "Tailnet-only HTTPS enables browser Service Workers and PWA features.",
                "port": VELVET_PORT
            }

    def _update_status(self):
        """Update Tailscale status by running CLI commands"""
        if not self.is_available():
            self.status = "not_installed"
            self.dns_name = None
            self._serve_https_ready = False
            return

        try:
            # Check if logged in and running
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                self.status = "not_running"
                return

            import json
            data = json.loads(result.stdout)

            # Check if we have a Tailscale IP
            if "Self" in data:
                self.hostname = data["Self"].get("HostName", "unknown")
                dns = str(data["Self"].get("DNSName") or "").rstrip(".").lower()
                self.dns_name = dns if dns.endswith(".ts.net") else None
                # Try to find IPv4
                for addr in data["Self"].get("DNSName", "").split():
                    if "." in addr and not addr.startswith("."):
                        self.ipv4 = addr.replace(":443", "")
                        break

                # Also check via 'tailscale ip -4'
                ip_result = subprocess.run(
                    ["tailscale", "ip", "-4"],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if ip_result.returncode == 0:
                    self.ipv4 = ip_result.stdout.strip()

                # Check IPv6
                ip6_result = subprocess.run(
                    ["tailscale", "ip", "-6"],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                if ip6_result.returncode == 0:
                    self.ipv6 = ip6_result.stdout.strip()

                self.status = "running"
            else:
                self.status = "not_running"

        except subprocess.TimeoutExpired:
            self.status = "not_running"
        except FileNotFoundError:
            self.status = "not_installed"
        except Exception:
            self.status = "not_running"

    def _check_serve_https(self) -> None:
        """Advertise HTTPS only when Tailscale Serve actually proxies VELVET.

        Do not automatically modify the user's VPN configuration or turn on
        Funnel/public internet exposure. HTTPS is opt-in through Tailscale CLI.
        """
        if self.status != "running" or not self.dns_name:
            self._serve_https_ready = False
            return
        now = time.monotonic()
        if now - self._serve_last_check < 30:
            return
        self._serve_last_check = now
        self._serve_https_ready = False
        try:
            result = subprocess.run(
                ["tailscale", "serve", "status"], capture_output=True, text=True, timeout=3,
            )
            description = result.stdout.lower()
            self._serve_https_ready = (
                result.returncode == 0
                and f"https://{self.dns_name}" in description
                and (f"127.0.0.1:{VELVET_PORT}" in description
                     or f"localhost:{VELVET_PORT}" in description)
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def open_browser(self) -> bool:
        """Open browser with the VELVET URL"""
        import webbrowser
        if self.ipv4:
            url = f"https://{self.dns_name}" if self._serve_https_ready and self.dns_name else f"http://{self.ipv4}:{VELVET_PORT}"
            webbrowser.open(url)
            return True
        return False


# Global instance
_tailscale_manager: Optional[TailscaleManager] = None
_manager_lock = threading.Lock()


def get_tailscale_manager() -> TailscaleManager:
    """Get or create Tailscale manager instance"""
    global _tailscale_manager
    with _manager_lock:
        if _tailscale_manager is None:
            _tailscale_manager = TailscaleManager()
        return _tailscale_manager


# Convenience functions
def get_remote_status() -> dict:
    """Get remote access status"""
    return get_tailscale_manager().get_status()


def is_tailscale_available() -> bool:
    """Check if Tailscale is available"""
    return get_tailscale_manager().is_available()


def open_remote_access() -> bool:
    """Open browser with remote access URL"""
    return get_tailscale_manager().open_browser()


def get_connection_info() -> dict:
    """Get detailed connection information for the user"""
    manager = get_tailscale_manager()
    status = manager.get_status()

    info = {
        "tunnel_type": "tailscale",
        "status": status["status"],
        "url": status["url"],
        "port": VELVET_PORT,
        "instructions": []
    }

    if status["status"] == "not_installed":
        info["instructions"] = [
            "1. Download Tailscale from https://tailscale.com/download",
            "2. Install and sign in with your Tailscale account",
            "3. Start Tailscale and note your IP address",
            "4. Share your Tailscale IP with family/friends"
        ]
    elif status["status"] == "not_running":
        info["instructions"] = [
            "1. Start Tailscale application",
            "2. Make sure you're logged in",
            "3. Click 'Connect' to establish the VPN",
            "4. Your Tailscale IP will appear here"
        ]
    elif status["status"] == "running":
        info["instructions"] = [
            f"Share this URL with others on your Tailnet: {status['url']}",
            "Others can access by opening this URL in their browser",
            "Make sure they have Tailscale installed and are connected"
        ]

    return info
