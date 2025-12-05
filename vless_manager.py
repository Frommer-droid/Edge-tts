"""VLESS VPN Manager for Edge-TTS Desktop."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from urllib.parse import unquote


class VLESSManager:
    """Manage VLESS VPN connection via xray-core or v2ray-core."""

    def __init__(self, log_func=print, socks_port: int = 10808) -> None:
        self.log = log_func
        self.xray_process: subprocess.Popen | None = None
        self.is_running = False
        self.local_socks_port = socks_port
        self.xray_exe = self._find_xray_executable()
        self.config_file: str | None = None

    def _find_xray_executable(self) -> str | None:
        """Locate xray.exe or v2ray.exe near the app."""
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            internal_dir = os.path.join(exe_dir, "_internal")
        else:
            exe_dir = os.path.dirname(os.path.abspath(__file__))
            internal_dir = exe_dir

        search_dirs = [internal_dir, exe_dir]

        for search_dir in search_dirs:
            xray_path = os.path.join(search_dir, "xray.exe")
            if os.path.exists(xray_path):
                self.log(f"Found xray.exe: {xray_path}")
                return xray_path

            v2ray_path = os.path.join(search_dir, "v2ray.exe")
            if os.path.exists(v2ray_path):
                self.log(f"Found v2ray.exe: {v2ray_path}")
                return v2ray_path

        self.log("WARNING: xray.exe or v2ray.exe not found")
        self.log(f"  Searched in: {', '.join(search_dirs)}")
        self.log("  Download:")
        self.log("  - xray-core: https://github.com/XTLS/Xray-core/releases")
        self.log("  - v2ray-core: https://github.com/v2fly/v2ray-core/releases")
        return None

    def parse_vless_url(self, vless_url: str) -> dict | None:
        """Parse VLESS URL into connection parameters."""
        try:
            vless_url = vless_url.strip()
            if not vless_url.startswith("vless://"):
                self.log("Error: URL must start with vless://")
                return None

            url_content = vless_url[8:]
            if "#" in url_content:
                url_part, name = url_content.rsplit("#", 1)
                name = unquote(name)
            else:
                url_part = url_content
                name = "VLESS Connection"

            if "?" in url_part:
                connection_part, params_part = url_part.split("?", 1)
            else:
                connection_part = url_part
                params_part = ""

            if "@" not in connection_part:
                self.log("Error: missing @ in URL")
                return None

            uuid, server_port = connection_part.split("@", 1)
            if ":" not in server_port:
                self.log("Error: missing port in URL")
                return None

            server, port = server_port.rsplit(":", 1)

            params: dict[str, str] = {}
            if params_part:
                for param in params_part.split("&"):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        params[key] = unquote(value)

            result = {
                "uuid": uuid,
                "server": server,
                "port": int(port),
                "network": params.get("type", "tcp"),
                "security": params.get("security", "none"),
                "flow": params.get("flow", ""),
                "sni": params.get("sni", server),
                "alpn": params.get("alpn", ""),
                "fp": params.get("fp", ""),
                "pbk": params.get("pbk", ""),
                "sid": params.get("sid", ""),
                "spx": params.get("spx", ""),
                "path": params.get("path", "/"),
                "host": params.get("host", ""),
                "serviceName": params.get("serviceName", ""),
                "name": name,
            }

            self.log(f"VLESS URL parsed: {result['name']}")
            self.log(f"  Target: {result['server']}:{result['port']}")
            self.log(f"  Transport: {result['network']}/{result['security']}")
            return result

        except Exception as e:  # pragma: no cover - defensive
            self.log(f"Error parsing VLESS URL: {e}")
            import traceback

            self.log(traceback.format_exc())
            return None

    def generate_xray_config(self, vless_params: dict) -> dict:
        """Build config dict for xray-core/v2ray-core."""
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "port": self.local_socks_port,
                    "listen": "127.0.0.1",
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": vless_params["server"],
                                "port": vless_params["port"],
                                "users": [
                                    {
                                        "id": vless_params["uuid"],
                                        "encryption": "none",
                                        "flow": vless_params.get("flow", ""),
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {"network": vless_params["network"]},
                }
            ],
        }

        security = vless_params.get("security", "none")
        stream_settings = config["outbounds"][0]["streamSettings"]

        if security == "tls":
            stream_settings["security"] = "tls"
            stream_settings["tlsSettings"] = {
                "serverName": vless_params.get("sni", vless_params["server"]),
                "allowInsecure": False,
            }
            alpn = vless_params.get("alpn", "")
            if alpn:
                stream_settings["tlsSettings"]["alpn"] = alpn.split(",")
            fp = vless_params.get("fp", "")
            if fp:
                stream_settings["tlsSettings"]["fingerprint"] = fp

        elif security == "reality":
            stream_settings["security"] = "reality"
            stream_settings["realitySettings"] = {
                "serverName": vless_params.get("sni", vless_params["server"]),
                "fingerprint": vless_params.get("fp", "chrome"),
                "show": False,
            }
            pbk = vless_params.get("pbk", "")
            if pbk:
                stream_settings["realitySettings"]["publicKey"] = pbk
            sid = vless_params.get("sid", "")
            if sid:
                stream_settings["realitySettings"]["shortId"] = sid
            spx = vless_params.get("spx", "")
            if spx:
                stream_settings["realitySettings"]["spiderX"] = spx

        network = vless_params["network"]
        if network == "ws":
            path = vless_params.get("path", "/")
            host = vless_params.get("host", "")
            stream_settings["wsSettings"] = {"path": path}
            if host:
                stream_settings["wsSettings"]["headers"] = {"Host": host}
        elif network == "grpc":
            service_name = vless_params.get("serviceName", "")
            stream_settings["grpcSettings"] = {
                "serviceName": service_name,
                "multiMode": False,
            }

        return config

    def start(self, vless_url: str) -> bool:
        """Start VLESS VPN connection."""
        try:
            if not self.xray_exe or not os.path.exists(self.xray_exe):
                self.log("Error: xray.exe or v2ray.exe not found!")
                self.log("  Download:")
                self.log("   - xray-core: https://github.com/XTLS/Xray-core/releases")
                self.log("   - v2ray-core: https://github.com/v2fly/v2ray-core/releases")
                return False

            self.log("Parsing VLESS URL...")
            vless_params = self.parse_vless_url(vless_url)
            if not vless_params:
                self.log("Error: invalid VLESS URL")
                return False

            if self.is_running:
                self.log("Stopping existing connection...")
                self.stop()

            self.log("Building config...")
            config = self.generate_xray_config(vless_params)

            if getattr(sys, "frozen", False):
                exe_dir = os.path.dirname(sys.executable)
            else:
                exe_dir = os.path.dirname(os.path.abspath(__file__))

            self.config_file = os.path.join(exe_dir, "vless_config.json")
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            self.log(f"Config saved: {self.config_file}")

            exe_name = os.path.basename(self.xray_exe)
            self.log(f"Starting {exe_name}...")

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            self.xray_process = subprocess.Popen(
                [self.xray_exe, "-c", self.config_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )

            self.log("Waiting for process...")
            time.sleep(2)

            if self._check_socks_port():
                self.is_running = True
                self.log("=" * 50)
                self.log("VLESS VPN CONNECTED!")
                self.log(f"SOCKS5: 127.0.0.1:{self.local_socks_port}")
                self.log("=" * 50)
                return True

            self.log("Error: SOCKS5 port is not reachable")
            self.log("  Check xray/v2ray logs and VLESS URL")
            self.stop()
            return False

        except Exception as e:  # pragma: no cover - defensive
            self.log("=" * 50)
            self.log(f"Error starting VLESS: {e}")
            import traceback

            self.log(traceback.format_exc())
            self.log("=" * 50)
            self.stop()
            return False

    def stop(self) -> bool:
        """Stop VLESS VPN."""
        if not self.is_running and not self.xray_process:
            self.log("VPN already stopped")
            return True

        try:
            self.log("Stopping VPN process...")
            if self.xray_process:
                self.xray_process.terminate()
                try:
                    self.xray_process.wait(timeout=3)
                    self.log("Process exited gracefully")
                except subprocess.TimeoutExpired:
                    self.log("Process did not exit, killing...")
                    self.xray_process.kill()
                    self.xray_process.wait()
                    self.log("Process killed")

            if self.config_file and os.path.exists(self.config_file):
                try:
                    os.remove(self.config_file)
                    self.log("Config removed")
                except Exception as e:  # pragma: no cover
                    self.log(f"Failed to remove config: {e}")

            self.is_running = False
            self.xray_process = None
            self.config_file = None
            self.log("VLESS VPN stopped")
            return True

        except Exception as e:  # pragma: no cover
            self.log(f"Error stopping VLESS: {e}")
            return False

    def _check_socks_port(self) -> bool:
        """Check if SOCKS5 port is listening."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(("127.0.0.1", self.local_socks_port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def get_status(self) -> dict:
        """Return connection status."""
        status = {
            "running": self.is_running,
            "port": self.local_socks_port,
            "proxy_url": f"socks5://127.0.0.1:{self.local_socks_port}",
        }
        if self.is_running:
            status["port_accessible"] = self._check_socks_port()
            if not status["port_accessible"]:
                status["warning"] = "Port not reachable"
        return status

    def cleanup(self) -> None:
        """Cleanup helper."""
        self.log("VLESSManager: cleanup...")
        self.stop()
