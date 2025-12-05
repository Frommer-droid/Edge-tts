from google.genai import types

import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты — корректор текстов. Твоя ЕДИНСТВЕННАЯ задача: исправить букву «ё» там, где она должна быть.\n\n"
    "ПРАВИЛА:\n"
    "1. Замени «е» на «ё» ТОЛЬКО там, где это необходимо по смыслу.\n"
    "2. Учитывай контекст: все/всё, еще/ещё, нес/нёс, шел/шёл, звезды/звёзды, передохнем/передохнём.\n"
    "3. НЕ МЕНЯЙ ничего кроме е↔ё: ни слова, ни порядок, ни пунктуацию.\n"
    "4. Верни ТОЛЬКО исправленный текст. БЕЗ объяснений, БЕЗ пометок, БЕЗ комментариев.\n\n"
    "ПРИМЕРЫ:\n"
    "Вход: звезды на небе\n"
    "Выход: звёзды на небе\n\n"
    "Вход: еще раз\n"
    "Выход: ещё раз\n\n"
    "Вход: передохнем немного\n"
    "Выход: передохнём немного\n\n"
    "Вход: на дороге\n"
    "Выход: на дороге\n\n"  # Здесь «е» правильная, менять не нужно
    "ТЕПЕРЬ ИСПРАВЬ ЭТОТ ТЕКСТ:\n"
)

async def fix_yo_with_gemini_async(text: str) -> str:
    if not text:
        return text
    
    from app.gemini_client import create_client
    
    client = create_client()
    if not client:
        logger.warning("Gemini client not initialized (no key). Skipping contextual yo-fication.")
        return text

    prompt = f"{_SYSTEM_PROMPT}\n\n{text}"

    try:
        # Using aio (asyncio) interface
        # The client should be created within the current loop context or be loop-agnostic until this call.
        resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",  # Возврат на стабильную модель с большими лимитами
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,      # Детерминированные ответы
                top_p=0.1,            # Минимальная вариативность
                top_k=1,              # Только лучший вариант токена
                max_output_tokens=8192,  # Достаточно для длинных текстов
            ),
        )
        
        result = (resp.text or "").strip()
        
        # Валидация: если Gemini вернул пустоту или что-то странное, возвращаем оригинал
        if not result or len(result) < len(text) * 0.8:  # Если ответ слишком короткий
            logger.warning(f"Gemini вернул подозрительный результат (длина {len(result)} vs {len(text)}), используем оригинал")
            return text
        
        return result
        
    except Exception as e:
        logger.error(f"Error calling Gemini for yo-fication: {e}")
        return text
    finally:
        # Explicitly close the client to release resources (sockets, etc.)
        # This is critical for compiled apps where loop restarts might occur or resources are tighter.
        try:
            client.close()
        except Exception as close_err:
            logger.warning(f"Error closing Gemini client: {close_err}")
