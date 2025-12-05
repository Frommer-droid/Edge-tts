"""Статистика вызовов Gemini AI."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List

STATS_FILE = Path("gemini_stats.json")


@dataclass
class CorrectionEntry:
    """Запись об исправлении."""
    original: str
    corrected: str
    type: str  # 'yo' или 'ipa'
    count: int = 1


@dataclass
class GeminiStats:
    """Статистика использования Gemini AI."""
    
    total_calls: int = 0           # Всего вызовов за всё время
    session_calls: int = 0         # Вызовов за текущую сессию
    total_corrections: int = 0     # Всего исправлений (добавлено ё)
    total_time_ms: float = 0.0     # Общее время обработки (мс)
    max_time_ms: float = 0.0       # Максимальное время одного вызова
    
    # Детальная статистика: "original->corrected" -> CorrectionEntry
    detailed_corrections: Dict[str, CorrectionEntry] = None

    def __post_init__(self):
        if self.detailed_corrections is None:
            self.detailed_corrections = {}
        # Convert dicts back to dataclasses if loaded from JSON
        elif self.detailed_corrections and isinstance(next(iter(self.detailed_corrections.values())), dict):
            self.detailed_corrections = {
                k: CorrectionEntry(**v) for k, v in self.detailed_corrections.items()
            }
    
    @property
    def avg_time_ms(self) -> float:
        """Среднее время обработки одного вызова."""
        return self.total_time_ms / self.total_calls if self.total_calls > 0 else 0.0
    
    def increment_call(self, time_ms: float, corrections: int = 0, details: List[CorrectionEntry] = None) -> None:
        """Увеличить счётчики после вызова Gemini.
        
        Args:
            time_ms: Время обработки в миллисекундах
            corrections: Количество исправлений (добавленных букв ё)
            details: Список детальных исправлений
        """
        self.total_calls += 1
        self.session_calls += 1
        self.total_corrections += corrections
        self.total_time_ms += time_ms
        self.max_time_ms = max(self.max_time_ms, time_ms)
        
        if details:
            for entry in details:
                key = f"{entry.original}->{entry.corrected}"
                if key in self.detailed_corrections:
                    self.detailed_corrections[key].count += 1
                else:
                    self.detailed_corrections[key] = entry
        
        # Debug print
        print(f"Stats updated: {self.total_calls} calls, {len(self.detailed_corrections)} details")
    
    def save(self) -> None:
        """Сохранить статистику в JSON (без session_calls)."""
        data = asdict(self)
        data.pop('session_calls')  # Сессионные данные не сохраняем
        
        try:
            STATS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            print(f"Failed to save stats: {e}")
    
    @classmethod
    def load(cls) -> GeminiStats:
        """Загрузить статистику из JSON."""
        if not STATS_FILE.exists():
            return cls()
        
        try:
            data = json.loads(STATS_FILE.read_text(encoding='utf-8'))
            # session_calls не загружается, остаётся 0
            return cls(**data)
        except Exception as e:
            print(f"Failed to load stats: {e}")
            return cls()


# Глобальный экземпляр
_stats: Optional[GeminiStats] = None


def get_stats() -> GeminiStats:
    """Получить глобальный экземпляр статистики."""
    global _stats
    if _stats is None:
        _stats = GeminiStats.load()
    return _stats


def reset_stats() -> None:
    """Сбросить статистику (для тестирования)."""
    global _stats
    _stats = GeminiStats()
    _stats.save()
