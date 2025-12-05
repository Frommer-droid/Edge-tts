from google.genai import types
import logging
from typing import List

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты — профессиональный корректор текста для синтеза речи (TTS). Твоя задача: подготовить текст для идеального озвучивания.\n\n"
    "ТВОИ ЗАДАЧИ:\n"
    "1. **Буква «ё»**: Расставь точки над «ё» там, где это нужно по смыслу (все/всё, небо/нёбо).\n"
    "2. **Ударения (Омографы)**: Если в тексте есть слова-омографы (слова, которые пишутся одинаково, но звучат по-разному в зависимости от смысла), замени их на специальный IPA-тег.\n"
    "   Формат тега: <phoneme alphabet='ipa' ph='IPA_TRANSCRIPTION'>WORD</phoneme>\n"
    "   Примеры:\n"
    "   - замок (дворец) -> <phoneme alphabet='ipa' ph='ˈza.mək'>замок</phoneme>\n"
    "   - замок (на двери) -> <phoneme alphabet='ipa' ph='zɐ.ˈmok'>замок</phoneme>\n"
    "   - горе (беда) -> <phoneme alphabet='ipa' ph='ˈgo.rʲe'>горе</phoneme>\n"
    "   - на горе (возвышенность) -> на <phoneme alphabet='ipa' ph='gɐ.ˈrʲe'>горе</phoneme>\n"
    "   - мука (страдание) -> <phoneme alphabet='ipa' ph='ˈmu.kə'>мука</phoneme>\n"
    "   - мука (продукт) -> <phoneme alphabet='ipa' ph='mu.ˈka'>мука</phoneme>\n\n"
    "ПРАВИЛА:\n"
    "1. Не меняй текст, если нет необходимости. Не перефразируй.\n"
    "2. Используй IPA только для слов, где есть неоднозначность ударения (омографы). Обычные слова не трогай.\n"
    "3. Верни ТОЛЬКО обработанный текст.\n"
)

async def fix_text_with_gemini_async(text: str, triggers: List[str] = None, thinking_mode: bool = False) -> str:
    if not text:
        return text
    
    from app.gemini_client import create_client
    
    client = create_client()
    if not client:
        logger.warning("Gemini client not initialized. Skipping AI correction.")
        return text

    # Если есть список триггеров, можно добавить их в промпт для акцента
    triggers_hint = ""
    if triggers:
        triggers_hint = (
            f"\n\nВАЖНО: Обрати особое внимание на эти слова: {', '.join(triggers)}.\n"
            "Если в этом списке слово указано с ударением (например, 'Се́лигман'), "
            "ОБЯЗАТЕЛЬНО используй это ударение при генерации IPA тега для этого слова!"
        )

    prompt = f"{_SYSTEM_PROMPT}{triggers_hint}\n\nВОТ ТЕКСТ ДЛЯ ОБРАБОТКИ:\n{text}"

    try:
        # Configure thinking mode if enabled
        gen_config = types.GenerateContentConfig(
            temperature=0.0,
            top_p=0.1,
            top_k=1,
        )
        
        if thinking_mode:
            try:
                # Try to set thinking config if supported by the SDK version
                if hasattr(types, 'ThinkingConfig'):
                    gen_config.thinking_config = types.ThinkingConfig(include_thoughts=True)
                else:
                    logger.warning("types.ThinkingConfig not found. Thinking mode might not work as expected.")
            except Exception as e:
                logger.warning(f"Could not set thinking_config: {e}")

        resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=gen_config,
        )
        
        result = (resp.text or "").strip()
        
        # Basic validation
        if not result or len(result) < len(text) * 0.5:
            logger.warning("Gemini returned suspiciously short text. Returning original.")
            return text
        
        return result
        
    except Exception as e:
        logger.error(f"Error calling Gemini for text correction: {e}")
        return text
    finally:
        try:
            client.close()
        except Exception:
            pass
