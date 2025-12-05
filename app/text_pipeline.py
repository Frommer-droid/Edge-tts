import re
import logging
from app.yo_processor import fix_yo_sure
from app.yo_processor import fix_yo_sure
from app.gemini_corrector import fix_text_with_gemini_async
from app.custom_dictionary import apply_custom_dictionary
from app.gemini_triggers import get_regex

logger = logging.getLogger(__name__)

# Загрузка триггеров из файла
_NEED_CONTEXT_RE = get_regex()

async def prepare_text_for_tts(text: str, gemini_enabled: bool = True, thinking_mode: bool = False) -> str:
    if not text:
        return text

    # 1. Custom dictionary replacements (NEW!)
    try:
        text = apply_custom_dictionary(text)
        logger.info(f"Текст после словаря замен: {text}")
    except Exception as e:
        logger.error(f"Error in custom dictionary: {e}")

    # 2. Unambiguous "yo" cases (yoditor)
    try:
        text = fix_yo_sure(text)
    except Exception as e:
        logger.error(f"Error in yoditor fix_yo_sure: {e}")

    # 3. Contextual processing (only if Gemini enabled and suspicious words are present)
    if not gemini_enabled:
        logger.info("Gemini отключён пользователем. Пропускаем контекстный анализ.")
        return text
    
    # Reload triggers for each generation (allows editing without restart)
    from app.gemini_triggers import reload_triggers, get_hints_for_matches
    _NEED_CONTEXT_RE = reload_triggers()
    
    # Find all matches to pass to Gemini
    matches = _NEED_CONTEXT_RE.findall(text)
    
    if matches:
        # Get hints (original forms with stress) for matched words
        hints = get_hints_for_matches(matches)
        logger.info(f"Contextual ambiguity detected! Matched words: {matches}")
        logger.info(f"Hints for Gemini: {hints}")
        logger.info("Calling Gemini...")
        
        # Замер времени и подсчёт исправлений
        import time
        from app.gemini_stats import get_stats
        
        start = time.perf_counter()
        original_text = text
        
        try:
            # Pass matched triggers (hints) to helper
            text = await fix_text_with_gemini_async(text, triggers=hints, thinking_mode=thinking_mode)
            logger.info(f"Gemini response: {text}")
            
            # Подсчёт исправлений (грубая оценка: ё + phoneme tags)
            corrections_yo = text.count('ё') - original_text.count('ё')
            corrections_ipa = text.count('<phoneme')
            total_corrections = max(0, corrections_yo) + corrections_ipa
            
            # Детальный анализ исправлений
            from app.gemini_stats import CorrectionEntry
            details = _analyze_corrections(original_text, text)
            
            # Замер времени
            elapsed_ms = (time.perf_counter() - start) * 1000
            
            # Обновление статистики
            stats = get_stats()
            logger.info(f"Updating stats with {len(details)} details: {details}")
            stats.increment_call(elapsed_ms, total_corrections, details)
            stats.save()
            
            logger.info(f"Статистика: +{total_corrections} исправлений, {elapsed_ms:.0f}ms")
            
        except Exception as e:
            logger.error(f"Error in Gemini fix_text_with_gemini_async: {e}")
    else:
        logger.info(f"No triggerwords found in text. Skipping Gemini.")

    return text


def _analyze_corrections(original: str, corrected: str) -> list:
    """Сравнить два текста и найти изменения (ё или IPA)."""
    from app.gemini_stats import CorrectionEntry
    import re
    
    details = []
    
    # 1. Подготовка corrected текста: защищаем пробелы внутри тегов, чтобы split() их не разбил
    # Ищем все теги <phoneme ...>...</phoneme>
    # Используем placeholder для пробела
    PLACEHOLDER = "___SPACE___"
    
    def protect_tag(match):
        return match.group(0).replace(" ", PLACEHOLDER)
        
    protected_corrected = re.sub(r'<phoneme[^>]*>.*?</phoneme>', protect_tag, corrected, flags=re.DOTALL)
    
    # 2. Токенизация
    words_orig = original.split()
    words_corr = protected_corrected.split()
    
    # 3. Выравнивание
    # Поскольку Gemini может менять пунктуацию или склеивать что-то, 
    # простого zip() может быть недостаточно, но с защитой тегов это будет намного точнее.
    # Для статистики пока оставим zip/min-length, так как полноценный diff - это оверхед.
    
    limit = min(len(words_orig), len(words_corr))
    
    for i in range(limit):
        w_orig = words_orig[i]
        # Восстанавливаем пробелы в токене
        w_corr = words_corr[i].replace(PLACEHOLDER, " ")
        
        # Очистка от пунктуации для сравнения слов (чтобы "слово." == "слово")
        # Но аккуратно, чтобы не сломать теги
        w_orig_clean = w_orig.strip(".,!?;:()\"'")
        
        # Если это тег, то внутри него слово тоже может быть с пунктуацией, но тег мы не стрипим
        is_ipa = '<phoneme' in w_corr
        
        if not is_ipa:
            w_corr_clean = w_corr.strip(".,!?;:()\"'")
            if w_orig_clean == w_corr_clean:
                continue
        
        # 1. Проверка на IPA тег
        if is_ipa:
            # Пытаемся извлечь само слово из тега для красивого отображения
            # w_corr: <phoneme... ph='...'>замок</phoneme>
            match = re.search(r'>([^<]+)</phoneme>', w_corr)
            clean_word = match.group(1) if match else w_corr
            
            # Если слово внутри тега совпадает с оригиналом (игнорируя ударения/пунктуацию),
            # то это точно наше исправление
            # Но даже если не совпадает (Gemini исправил букву), всё равно логируем
            
            details.append(CorrectionEntry(
                original=w_orig_clean, # Берем очищенное слово (без пунктуации)
                corrected=clean_word + " (IPA)", 
                type='ipa'
            ))
            continue
            
        # 2. Проверка на Ё
        # Сравниваем clean версии
        if 'ё' in w_corr_clean.lower() and 'е' in w_orig_clean.lower():
            if w_corr_clean.lower().replace('ё', 'е') == w_orig_clean.lower():
                details.append(CorrectionEntry(
                    original=w_orig_clean,
                    corrected=w_corr_clean,
                    type='yo'
                ))
                continue
                
    return details

