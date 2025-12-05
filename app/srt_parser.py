"""Парсер .srt файлов для озвучивания субтитров."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class SubtitleEntry:
    """Запись субтитра."""
    number: int
    start_time: str  # "00:00:01,000"
    end_time: str    # "00:00:03,500"
    text: str
    pause_after: float = 0.0  # в секундах


def time_to_seconds(time_str: str) -> float:
    """Конвертирует время из формата SRT в секунды.
    
    Args:
        time_str: Время в формате "HH:MM:SS,mmm"
        
    Returns:
        float: Время в секундах
        
    Example:
        >>> time_to_seconds("00:00:01,500")
        1.5
    """
    # Формат: HH:MM:SS,mmm
    time_str = time_str.strip()
    h, m, rest = time_str.split(':')
    s, ms = rest.split(',')
    
    total_seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return total_seconds


def parse_srt_file(file_path: str) -> List[SubtitleEntry]:
    """Парсит .srt файл и возвращает список записей.
    
    Args:
        file_path: Путь к .srt файлу
        
    Returns:
        List[SubtitleEntry]: Список субтитров
        
    Raises:
        ValueError: Если формат файла некорректен
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    
    # Попытка определить кодировку
    encodings = ['utf-8', 'utf-8-sig', 'windows-1251', 'cp1252']
    content = None
    
    for encoding in encodings:
        try:
            content = file_path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    
    if content is None:
        raise ValueError(f"Не удалось определить кодировку файла: {file_path}")
    
    # Паттерн для парсинга .srt
    # Формат:
    # <номер>
    # <время начала> --> <время окончания>
    # <текст>
    # <пустая строка>
    pattern = r'(\d+)\s*\n\s*(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)'
    
    matches = re.findall(pattern, content, re.DOTALL)
    
    if not matches:
        raise ValueError("Файл не содержит корректных .srt записей")
    
    entries = []
    for i, (num, start, end, text) in enumerate(matches):
        entry = SubtitleEntry(
            number=int(num),
            start_time=start,
            end_time=end,
            text=text.strip()
        )
        
        # Вычисляем паузу до следующей реплики
        if i < len(matches) - 1:
            next_start = matches[i + 1][1]
            pause = time_to_seconds(next_start) - time_to_seconds(end)
            entry.pause_after = max(0.0, pause)  # Не может быть отрицательной
        
        entries.append(entry)
    
    return entries


def calculate_pause_duration(end_time: str, next_start_time: str) -> float:
    """Вычисляет длительность паузы между репликами в секундах.
    
    Args:
        end_time: Время окончания текущей реплики
        next_start_time: Время начала следующей реплики
        
    Returns:
        float: Длительность паузы в секундах (минимум 0.0)
    """
    pause = time_to_seconds(next_start_time) - time_to_seconds(end_time)
    return max(0.0, pause)


def extract_text_with_timings(entries: List[SubtitleEntry]) -> List[Tuple[str, float]]:
    """Извлекает текст и паузы для генерации аудио.
    
    Args:
        entries: Список субтитров
        
    Returns:
        List[Tuple[str, float]]: Список кортежей (текст, пауза_после)
    """
    return [(entry.text, entry.pause_after) for entry in entries]


def get_srt_stats(entries: List[SubtitleEntry]) -> dict:
    """Получить статистику по субтитрам.
    
    Args:
        entries: Список субтитров
        
    Returns:
        dict: Статистика (количество реплик, общая длительность и т.д.)
    """
    if not entries:
        return {
            'count': 0,
            'duration': 0.0,
            'total_text_length': 0
        }
    
    last_entry = entries[-1]
    total_duration = time_to_seconds(last_entry.end_time)
    total_text_length = sum(len(e.text) for e in entries)
    
    return {
        'count': len(entries),
        'duration': total_duration,
        'total_text_length': total_text_length,
        'avg_text_length': total_text_length / len(entries) if entries else 0
    }
