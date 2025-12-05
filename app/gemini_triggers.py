"""Модуль для управления триггерами Gemini AI.

Триггеры — это слова и корни слов, при наличии которых в тексте 
вызывается Gemini для контекстного анализа и исправления омографов.

Файл триггеров: gemini_triggers.txt
Формат: по одному слову/корню на строку
Поддержка wildcards: звезд* → звезда, звезды, звёзд и т.д.
"""

import re
import logging
from pathlib import Path
from typing import List, Pattern

logger = logging.getLogger(__name__)

# Путь к файлу триггеров
TRIGGERS_FILE = Path(__file__).parent.parent / "gemini_triggers.txt"

# Глобальная переменная с текущими триггерами
_triggers: List[str] = []
_trigger_map: dict[str, str] = {} # clean -> original (stressed)
_compiled_regex: Pattern | None = None


def strip_stress(text: str) -> str:
    """Removes combining acute accent (U+0301) from text."""
    return text.replace('\u0301', '')


def load_triggers() -> List[str]:
    """Загружает триггеры из файла.
    
    Returns:
        Список "чистых" триггеров (без ударений) для regex.
        
    Формат файла:
        - По одному триггеру на строку
        - Комментарии начинаются с #
        - Пустые строки игнорируются
        - Поддержка wildcards: звезд* → ловит все формы
        - Поддержка ударений: Се́лигман (сохраняется для подсказки)
    """
    global _trigger_map
    
    if not TRIGGERS_FILE.exists():
        logger.warning(f"Файл триггеров не найден: {TRIGGERS_FILE}")
        return []
    
    triggers = []
    _trigger_map = {}
    
    try:
        with open(TRIGGERS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Пропускаем комментарии и пустые строки
                if not line or line.startswith('#'):
                    continue
                
                # Сохраняем оригинал (возможно с ударением)
                original = line
                # Создаем чистую версию для поиска
                clean = strip_stress(original)
                
                triggers.append(original)
                
                # Сохраняем маппинг: clean (lowercase) -> original
                # Если wildcard, маппинг сложнее, но для подсказки нам нужен сам паттерн
                _trigger_map[clean.lower()] = original
        
        logger.info(f"Загружено {len(triggers)} триггеров из {TRIGGERS_FILE}")
        
    except Exception as e:
        logger.error(f"Ошибка загрузки триггеров: {e}")
        return []
    
    return triggers


def save_triggers(triggers: List[str]) -> bool:
    """Сохраняет триггеры в файл с автосортировкой.
    
    Args:
        triggers: Список триггеров (возможно с ударениями)
        
    Returns:
        True если успешно, False при ошибке
    """
    try:
        # Сортировка по алфавиту
        sorted_triggers = sorted(set(triggers), key=lambda x: x.lower())
        
        with open(TRIGGERS_FILE, 'w', encoding='utf-8') as f:
            f.write("# Триггеры для Gemini - слова и корни слов\n")
            f.write("# Формат: по одному слову/корню на строку\n")
            f.write("# Поддержка wildcards: звезд* → звезда, звезды, звёзд и т.д.\n")
            f.write("# Поддержка ударений: Се́лигман (Gemini учтет это при расстановке IPA)\n")
            f.write("\n")
            
            for trigger in sorted_triggers:
                f.write(f"{trigger}\n")
        
        logger.info(f"Сохранено {len(sorted_triggers)} триггеров в {TRIGGERS_FILE}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения триггеров: {e}")
        return False


def compile_triggers_regex() -> Pattern:
    """Компилирует regex паттерн из списка триггеров.
    
    Returns:
        Скомпилированный regex паттерн
        
    Wildcards конвертируются:
        звезд* → звезд\\w*
    """
    global _triggers, _compiled_regex
    
    # Загружаем триггеры если ещё не загружены
    if not _triggers:
        _triggers = load_triggers()
    
    if not _triggers:
        # Если файла нет, используем дефолтный набор
        logger.warning("Используются дефолтные триггеры")
        _triggers = ["все", "еще", "ещё", "нес", "шел", "шёл", "вел", "вёл", "звезд*"]
    
    # Конвертируем wildcards в regex
    regex_parts = []
    for trigger in _triggers:
        # Trigger might have stress, so strip it for regex
        clean_trigger = strip_stress(trigger)
        
        if '*' in clean_trigger:
            # Заменяем * на \w* (любые буквы)
            pattern = clean_trigger.replace('*', r'\w*')
            regex_parts.append(pattern)
        else:
            # Точное совпадение
            regex_parts.append(re.escape(clean_trigger))
    
    # Объединяем все паттерны через |
    full_pattern = r'\b(' + '|'.join(regex_parts) + r')\b'
    
    _compiled_regex = re.compile(full_pattern, re.IGNORECASE)
    
    logger.info(f"Скомпилирован regex для {len(_triggers)} триггеров")
    
    return _compiled_regex


def reload_triggers() -> Pattern:
    """Перезагружает триггеры из файла и перекомпилирует regex.
    
    Returns:
        Новый скомпилированный regex паттерн
    """
    global _triggers
    
    _triggers = load_triggers()
    return compile_triggers_regex()


def get_triggers() -> List[str]:
    """Возвращает текущий список триггеров (чистых).
    
    Returns:
        Список триггеров
    """
    global _triggers
    
    if not _triggers:
        _triggers = load_triggers()
    
    return _triggers.copy()


def get_regex() -> Pattern:
    """Возвращает текущий скомпилированный regex.
    
    Returns:
        Regex паттерн для проверки триггеров
    """
    global _compiled_regex
    
    if _compiled_regex is None:
        _compiled_regex = compile_triggers_regex()
    
    return _compiled_regex


def get_hints_for_matches(matches: List[str]) -> List[str]:
    """Возвращает оригинальные (возможно с ударениями) формы для найденных совпадений.
    
    Args:
        matches: Список слов, найденных в тексте (без ударений)
        
    Returns:
        Список уникальных подсказок (слов с ударениями из словаря)
    """
    global _trigger_map
    
    hints = set()
    
    for match in matches:
        match_lower = match.lower()
        
        # 1. Прямое совпадение
        if match_lower in _trigger_map:
            hints.add(_trigger_map[match_lower])
            continue
            
        # 2. Поиск по wildcard (если не нашли прямого)
        # Это сложнее, так как map хранит clean -> original.
        # Если match="звездами", а trigger="звезд*", то в map лежит "звезд*" -> "звезд*".
        # Но если trigger="Се́лигман*", то clean="Селигман*", original="Се́лигман*".
        # Нам нужно найти, какой ключ из map матчится с match.
        
        found = False
        for clean_key, original_val in _trigger_map.items():
            if '*' in clean_key:
                # Превращаем ключ в regex
                pattern = '^' + re.escape(clean_key).replace(r'\*', r'.*') + '$'
                if re.match(pattern, match_lower):
                    hints.add(original_val)
                    found = True
                    break # Берем первое совпадение
        
        if not found:
            # Если не нашли в словаре (странно, если regex сработал), возвращаем как есть
            hints.add(match)
            
    return list(hints)
