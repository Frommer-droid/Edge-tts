"""Генератор озвучки из субтитров .srt."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import List, Tuple, Callable, Optional

import edge_tts
from pydub import AudioSegment

from app.voice_markers import parse_marked_text, get_voice_for_marker
from app.srt_parser import SubtitleEntry


async def generate_audio_fragment(
    text: str,
    voice: str,
    rate: int,
    quality: str,
    output_path: str,
    use_stress: bool = False
) -> None:
    """Генерирует один фрагмент аудио.
    
    Args:
        text: Текст для озвучивания
        voice: Voice ID (например, 'ru-RU-DmitryNeural')
        rate: Скорость речи (-50 до +50)
        quality: Качество аудио
        output_path: Путь для сохранения фрагмента
    """
    # Форматируем rate для Edge TTS
    if rate >= 0:
        rate_str = f"+{rate}%"
    else:
        rate_str = f"{rate}%"
    
    # Генерируем аудио
    try:
        if use_stress:
            # 1. Add stress marks - REMOVED (Russtress)
            # stressed_text = add_stress(text)
            stressed_text = text # Pass text as is (Gemini might have added <phoneme>)
            
            # 2. Construct raw SSML
            # Note: edge-tts expects the text to be passed to Communicate.
            # With our patch, if raw_ssml=True, it sends the text as is.
            # So we must wrap it in <speak>...
            
            ssml = (
                f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='ru-RU'>"
                f"<voice name='{voice}'>"
                f"<prosody rate='{rate_str}' pitch='+0Hz'>"
                f"{stressed_text}"
                f"</prosody>"
                f"</voice>"
                f"</speak>"
            )
            
            communicate = edge_tts.Communicate(ssml, voice, rate=rate_str, raw_ssml=True)
        else:
            communicate = edge_tts.Communicate(text, voice, rate=rate_str)
            
        await communicate.save(output_path)
    except Exception as e:
        if "No audio was received" in str(e):
            raise ValueError(
                f"Ошибка генерации аудио для текста: '{text[:20]}...'. "
                f"Возможно, выбранный голос ({voice}) не поддерживает язык текста."
            )
        raise e


def create_silence(duration_ms: int) -> AudioSegment:
    """Создаёт тишину заданной длительности.
    
    Args:
        duration_ms: Длительность в миллисекундах
        
    Returns:
        AudioSegment: Сегмент тишины
    """
    return AudioSegment.silent(duration=duration_ms)


async def generate_srt_audio(
    marked_text: str,
    timings: List[Tuple[str, float]],  # [(text, pause_after), ...]
    output_path: str,
    quality: str = "audio-24khz-96kbitrate-mono-mp3",
    rate: int = 0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    default_voice: Optional[str] = None,
    use_stress: bool = False
) -> None:
    """Генерирует единый MP3 из текста с метками и таймингами.
    
    Args:
        marked_text: Текст с метками голосов
        timings: Список пар (текст, пауза_после в секундах)
        output_path: Путь для сохранения итогового MP3
        quality: Качество аудио
        rate: Скорость речи (-50 до +50)
        progress_callback: Функция обратного вызова (current, total, status_text)
        
    Raises:
        ValueError: Если количество меток не совпадает с количеством таймингов
    """
    # Парсим текст с метками
    marked_entries = parse_marked_text(marked_text)
    
    # Проверяем соответствие
    if len(marked_entries) != len(timings):
        raise ValueError(
            f"Несоответствие: {len(marked_entries)} реплик с метками, "
            f"но {len(timings)} записей с таймингами"
        )
    
    # Временная директория для фрагментов
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        fragments = []
        
        total = len(marked_entries)
        
        # Генерируем каждую реплику
        for i, ((marker, text), (_, pause_after)) in enumerate(zip(marked_entries, timings), 1):
            if progress_callback:
                progress_callback(i, total, f"Озвучивание реплики {i}/{total}...")
            
            # Получаем голос для метки
            voice = get_voice_for_marker(marker)
            
            # Если передан дефолтный голос и метка [RU_M], используем его
            if default_voice and marker == '[RU_M]':
                voice = default_voice
            
            # Генерируем фрагмент
            fragment_path = temp_path / f"fragment_{i:04d}.mp3"
            await generate_audio_fragment(text, voice, rate, quality, str(fragment_path), use_stress=use_stress)
            
            # Загружаем фрагмент
            audio_fragment = AudioSegment.from_mp3(str(fragment_path))
            fragments.append(audio_fragment)
            
            # Добавляем паузу
            if pause_after > 0:
                silence = create_silence(int(pause_after * 1000))  # секунды -> миллисекунды
                fragments.append(silence)
        
        # Объединяем все фрагменты
        if progress_callback:
            progress_callback(total, total, "Склейка аудио...")
        
        combined = fragments[0]
        for fragment in fragments[1:]:
            combined += fragment
        
        # Сохраняем результат
        if progress_callback:
            progress_callback(total, total, "Сохранение файла...")
        
        # Extract bitrate properly (e.g. "96kbitrate" -> "96k")
        bitrate_str = quality.split('-')[2].replace("kbitrate", "k")
        combined.export(output_path, format="mp3", bitrate=bitrate_str)
        
        if progress_callback:
            progress_callback(total, total, "Готово!")


async def generate_srt_audio_from_entries(
    marked_text: str,
    entries: List[SubtitleEntry],
    output_path: str,
    quality: str = "audio-24khz-96kbitrate-mono-mp3",
    rate: int = 0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    default_voice: Optional[str] = None,
    use_stress: bool = False
) -> None:
    """Генерирует озвучку из SubtitleEntry списка.
    
    Args:
        marked_text: Текст с метками голосов
        entries: Список субтитров с таймингами
        output_path: Путь для сохранения итогового MP3
        quality: Качество аудио
        rate: Скорость речи
        progress_callback: Функция обратного вызова
    """
    # Извлекаем тайминги
    timings = [(entry.text, entry.pause_after) for entry in entries]
    
    # Генерируем аудио
    await generate_srt_audio(
        marked_text=marked_text,
        timings=timings,
        output_path=output_path,
        quality=quality,
        rate=rate,
        progress_callback=progress_callback,
        default_voice=default_voice,
        use_stress=use_stress
    )


# Вспомогательная функция для синхронного вызова
def generate_srt_audio_sync(
    marked_text: str,
    timings: List[Tuple[str, float]],
    output_path: str,
    quality: str = "audio-24khz-96kbitrate-mono-mp3",
    rate: int = 0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> None:
    """Синхронная обёртка для generate_srt_audio."""
    asyncio.run(generate_srt_audio(
        marked_text, timings, output_path, quality, rate, progress_callback
    ))
