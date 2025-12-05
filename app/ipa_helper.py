import logging
from typing import List, Tuple
from google.genai import types
from app.gemini_client import create_client

logger = logging.getLogger(__name__)

def generate_ipa_variants(word: str) -> List[Tuple[str, str]]:
    """
    Asks Gemini to generate IPA variants for a Russian word.
    Returns a list of tuples: (IPA_tag, description).
    
    Example return:
    [
        ("<phoneme alphabet='ipa' ph='ˈza.mək'>замок</phoneme>", "Дворец / Building"),
        ("<phoneme alphabet='ipa' ph='zɐ.ˈmok'>замок</phoneme>", "Запирающее устройство / Lock")
    ]
    """
    client = create_client()
    if not client:
        logger.warning("Gemini client not initialized. Cannot generate IPA.")
        return []

    prompt = (
        f"Analyze the Russian word '{word}'. "
        "It is a homograph (or suspected homograph). "
        "Provide IPA pronunciations ONLY for meanings that match this EXACT spelling (case-insensitive). "
        "Do NOT list other grammatical forms unless they are spelled EXACTLY the same.\n"
        "CRITICAL RULE: Group meanings by pronunciation. If multiple meanings have the SAME stress/IPA, combine them into ONE line.\n"
        "Format: 'IPA|Description (Meaning 1 / Meaning 2)'\n"
        "Examples:\n"
        "Input: 'мука'\n"
        "mu.ˈka|Мука (Продукт / Flour)\n"
        "ˈmu.kə|Мука (Страдание / Torment)\n"
        "Input: 'коса'\n"
        "kɐ.ˈsa|Коса (Прическа / Орудие / Пляж) \n"
        "Input: 'замок'\n"
        "ˈza.mək|Замок (Дворец / Castle)\n"
        "zɐ.ˈmok|Замок (Устройство / Lock)\n"
        "Strictly follow the format. No extra text."
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
            )
        )
        
        result = []
        if response.text:
            lines = response.text.strip().split('\n')
            for line in lines:
                parts = line.split('|')
                if len(parts) == 2:
                    ipa = parts[0].strip()
                    desc = parts[1].strip()
                    # Construct the SSML tag
                    tag = f"<phoneme alphabet='ipa' ph='{ipa}'>{word}</phoneme>"
                    result.append((tag, desc))
        
        return result

    except Exception as e:
        logger.error(f"Error generating IPA for '{word}': {e}")
        return []
