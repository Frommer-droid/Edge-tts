"""Система меток голосов для SRT озвучивания."""

from __future__ import annotations

import re
from typing import List, Tuple, Dict
from pathlib import Path

# Маппинг меток на голоса Edge TTS
VOICE_MARKERS: Dict[str, str] = {
    # Русский
    '[RU_M]': 'ru-RU-DmitryNeural',      # Русский мужчина
    '[RU_F]': 'ru-RU-SvetlanaNeural',    # Русская женщина
    
    # Английский (США)
    '[EN_M]': 'en-US-GuyNeural',          # Английский мужчина
    '[EN_F]': 'en-US-JennyNeural',        # Английская женщина
    
    # Английский (Великобритания)
    '[EN_M_UK]': 'en-GB-RyanNeural',      # Британский мужчина
    '[EN_F_UK]': 'en-GB-SoniaNeural',     # Британская женщина
}

# Описания меток для UI
MARKER_DESCRIPTIONS: Dict[str, str] = {
    '[RU_M]': 'Русский мужчина (Дмитрий)',
    '[RU_F]': 'Русская женщина (Светлана)',
    '[EN_M]': 'Английский мужчина (Гай)',
    '[EN_F]': 'Английская женщина (Дженни)',
    '[EN_M_UK]': 'Британский мужчина (Райан)',
    '[EN_F_UK]': 'Британская женщина (Соня)',
}


def generate_marked_text(texts: List[any], default_marker: str = '[RU_M]') -> str:
    """Генерирует текст с метками голосов.
    
    Args:
        texts: Список текстов реплик или объектов SubtitleEntry
        default_marker: Метка по умолчанию для всех реплик
        
    Returns:
        str: Текст с метками, каждая реплика на новой строке
    """
    if default_marker not in VOICE_MARKERS:
        raise ValueError(f"Неизвестная метка: {default_marker}")
    
    lines = []
    for item in texts:
        # Если передан объект (например SubtitleEntry), берем его текст
        if hasattr(item, 'text'):
            text = item.text
            # Добавляем метаданные (номер и время), если есть
            if hasattr(item, 'number') and hasattr(item, 'start_time'):
                lines.append(f"#{item.number} [{item.start_time}]")
        else:
            text = str(item)
            
        lines.append(f"{default_marker} {text}")
        lines.append("") # Пустая строка для разделения
    
    return "\n".join(lines)


def parse_marked_text(marked_text: str) -> List[Tuple[str, str]]:
    """Парсит текст с метками голосов.
    
    Args:
        marked_text: Текст с метками (каждая реплика на новой строке)
        
    Returns:
        List[Tuple[str, str]]: Список кортежей (метка, текст)
        
    Raises:
        ValueError: Если встречена неизвестная метка
        
    Example:
        >>> text = "[RU_M] Привет!\\n[RU_F] Как дела?"
        >>> parse_marked_text(text)
        [('[RU_M]', 'Привет!'), ('[RU_F]', 'Как дела?')]
    """
    # Паттерн: [МЕТКА] текст
    pattern = r'(\[[\w_]+\])\s+(.*)'
    
    result = []
    for line in marked_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # Пропускаем строки с метаданными (начинаются с #)
        if line.startswith('#'):
            continue
        
        match = re.match(pattern, line)
        if not match:
            # Если нет метки, используем дефолтную
            result.append(('[RU_M]', line))
            continue
        
        marker, text = match.groups()
        
        # Проверяем что метка существует
        if marker not in VOICE_MARKERS:
            raise ValueError(f"Неизвестная метка: {marker}. Доступные метки: {', '.join(VOICE_MARKERS.keys())}")
        
        result.append((marker, text.strip()))
    
    return result


def get_voice_for_marker(marker: str) -> str:
    """Возвращает voice_id для метки.
    
    Args:
        marker: Метка голоса (например, '[RU_M]')
        
    Returns:
        str: Voice ID для Edge TTS
        
    Raises:
        ValueError: Если метка неизвестна
        
    Example:
        >>> get_voice_for_marker('[RU_M]')
        'ru-RU-DmitryNeural'
    """
    if marker not in VOICE_MARKERS:
        raise ValueError(f"Неизвестная метка: {marker}")
    
    return VOICE_MARKERS[marker]


def get_available_markers() -> List[Tuple[str, str]]:
    """Получить список доступных меток с описаниями.
    
    Returns:
        List[Tuple[str, str]]: Список пар (метка, описание)
        
    Example:
        >>> markers = get_available_markers()
        >>> markers[0]
        ('[RU_M]', 'Русский мужчина (Дмитрий)')
    """
    return [(marker, MARKER_DESCRIPTIONS.get(marker, marker)) 
            for marker in VOICE_MARKERS.keys()]


def save_marked_text(marked_text: str, output_path: str) -> None:
    """Сохранить текст с метками в файл.
    
    Args:
        marked_text: Текст с метками
        output_path: Путь для сохранения
    """
    output_file = Path(output_path)
    output_file.write_text(marked_text, encoding='utf-8')


def load_marked_text(file_path: str) -> str:
    """Загрузить текст с метками из файла.
    
    Args:
        file_path: Путь к файлу
        
    Returns:
        str: Текст с метками
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    
    # Попытка определить кодировку
    encodings = ['utf-8', 'utf-8-sig', 'windows-1251']
    
    for encoding in encodings:
        try:
            return file_path.read_text(encoding=encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    
    raise ValueError(f"Не удалось определить кодировку файла: {file_path}")
