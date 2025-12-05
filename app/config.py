"""Configuration loader for the Edge-TTS desktop app."""

from __future__ import annotations

import os
import sys
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Determine .env path
if getattr(sys, 'frozen', False):
    # If frozen (exe), .env is expected in _internal folder
    base_path = Path(sys.executable).parent
    env_path = base_path / "_internal" / ".env"
else:
    # If running from source, .env is in project root
    env_path = Path(__file__).resolve().parent.parent / ".env"

load_dotenv(dotenv_path=env_path)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Keep `value` between `minimum` and `maximum`."""
    return max(minimum, min(maximum, value))


@dataclass
class AppConfig:
    default_voice: str
    default_rate: int
    default_volume: int
    output_dir: Path
    temp_prefix: str
    log_path: Path
    request_timeout: int
    vless_enabled: bool
    vless_port: int
    vless_default_url: str
    gemini_api_key: str
    gemini_enabled: bool  # Использовать Gemini для ё-фикации
    thinking_mode: bool   # Включить режим размышления (Gemini 2.5)
    base_path: Path = None # Путь к папке приложения

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build configuration from environment variables and local settings JSON."""
        # 0. Determine base path
        if getattr(sys, 'frozen', False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent.parent

        # 1. Load defaults from Environment / .env
        default_voice = os.getenv("TTS_DEFAULT_VOICE", "ru-RU-DmitryNeural")
        default_rate = _clamp(int(os.getenv("TTS_DEFAULT_RATE", "0")), -50, 50)
        default_volume = _clamp(int(os.getenv("TTS_VOLUME", "100")), 0, 100)

        output_dir_env = os.getenv("TTS_OUTPUT_DIR")
        output_dir = (
            Path(output_dir_env).expanduser()
            if output_dir_env
            else Path(tempfile.gettempdir())
        )

        temp_prefix = os.getenv("TTS_TEMP_PREFIX", "edge_tts_")

        log_env = os.getenv("TTS_LOG_PATH")
        if log_env:
            log_path = Path(log_env).expanduser()
            if not log_path.is_absolute():
                log_path = Path.cwd() / log_path
        else:
            log_path = Path.cwd() / "logs" / "edge_tts_app.log"

        request_timeout = _clamp(int(os.getenv("TTS_REQUEST_TIMEOUT", "60")), 10, 300)

        vless_enabled = os.getenv("VLESS_ENABLED", "false").lower() in {"1", "true", "yes"}
        vless_port = _clamp(int(os.getenv("VLESS_PORT", "10809")), 1, 65535)
        vless_default_url = os.getenv("VLESS_URL", "")
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        gemini_enabled = True  # По умолчанию включён
        thinking_mode = False  # По умолчанию выключен

        # 2. Override from edge_tts_settings.json if it exists
        try:
            settings_path = base_path / "edge_tts_settings.json"
            
            if settings_path.exists():
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    # Override Voice
                    if "voice_id" in data:
                        default_voice = data["voice_id"]
                    
                    # Override Rate
                    if "rate" in data:
                        default_rate = _clamp(int(data["rate"]), -50, 50)
                        
                    # Override Volume (if we decide to save it later)
                    if "volume" in data:
                        default_volume = _clamp(int(data["volume"]), 0, 100)
                        
                    # Override Timeout (hidden setting)
                    if "request_timeout" in data:
                        request_timeout = _clamp(int(data["request_timeout"]), 10, 300)
                        
                    # Override VLESS URL
                    if "vless_url" in data:
                        vless_default_url = data["vless_url"]
                        
                    # Override Gemini Key
                    if "gemini_api_key" in data:
                        gemini_api_key = data["gemini_api_key"]
                    
                    # Override Gemini Enabled
                    if "gemini_enabled" in data:
                        gemini_enabled = bool(data["gemini_enabled"])

                    # Override Thinking Mode
                    if "thinking_mode" in data:
                        thinking_mode = bool(data["thinking_mode"])

        except Exception:
            # If JSON loading fails, just stick to env defaults
            pass

        return cls(
            default_voice=default_voice,
            default_rate=default_rate,
            default_volume=default_volume,
            output_dir=output_dir,
            temp_prefix=temp_prefix,
            log_path=log_path,
            request_timeout=request_timeout,
            vless_enabled=vless_enabled,
            vless_port=vless_port,
            vless_default_url=vless_default_url,
            gemini_api_key=gemini_api_key,
            gemini_enabled=gemini_enabled,
            thinking_mode=thinking_mode,
            base_path=base_path,
        )

    def ensure_paths(self) -> None:
        """Create the configured output and log directories if they do not exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def get_stats(self) -> dict:
        """Return dummy stats for now (or implement real stats storage)."""
        return {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "saved_requests": 0,
            "last_request_time": None
        }

    def reset_stats(self) -> None:
        """Reset stats (placeholder)."""
        pass


def load_config() -> AppConfig:
    """Helper function to load configuration."""
    return AppConfig.from_env()
