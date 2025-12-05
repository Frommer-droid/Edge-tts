"""Worker thread that performs TTS generation without blocking the UI."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import traceback
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple
from xml.sax.saxutils import escape

import edge_tts
from edge_tts.exceptions import NoAudioReceived
from PySide6.QtCore import QThread, Signal

from app.ssml_client import SSMLCommunicate
from app.text_pipeline import prepare_text_for_tts
from app.srt_audio_generator import generate_srt_audio_from_entries
from app.srt_parser import SubtitleEntry


class TtsWorker(QThread):
    finished = Signal(str)  # Emits the path to the generated audio (last one or list)
    error = Signal(str)  # Emits the error message
    progress = Signal(str) # Emits detailed progress messages (e.g. "Generating chunk 1/5")
    
    # New signals for batch processing
    batch_progress = Signal(int, int, str) # current_index, total_files, current_filename
    file_finished = Signal(str) # Emits path of completed file
    
    # Signal to ensure loop is ready
    ready = Signal()

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        super().__init__()
        self.logger = logger or logging.getLogger(__name__)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_running = asyncio.Event() # Not thread-safe, used inside loop? No, need threading.Event
        import threading
        self._ready_event = threading.Event()

    def run(self) -> None:
        """Run the persistent event loop."""
        try:
            if os.name == 'nt':
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            self.logger.info("Worker loop started.")
            self._ready_event.set()
            self.ready.emit()
            
            self.loop.run_forever()
        except Exception as e:
            self.logger.error(f"Worker loop crashed: {e}")
            self.error.emit(f"Critical worker error: {e}")
        finally:
            if self.loop:
                try:
                    # Cancel all running tasks
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                    
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    self.loop.close()
                except Exception as e:
                    self.logger.error(f"Error closing loop: {e}")
            self.logger.info("Worker loop stopped.")

    def stop(self) -> None:
        """Stop the worker loop and wait for thread termination."""
        self.logger.info("Stopping worker...")
        
        if self.loop and self.loop.is_running():
            # Schedule loop.stop() to run in the event loop thread
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        # Wait for the thread to finish (run() will exit after loop.stop())
        if not self.wait(3000):  # 3 seconds timeout
            self.logger.warning("Worker thread did not stop in time, terminating forcefully")
            self.terminate()  # Force terminate if not stopped
            self.wait(500)
        
        self.logger.info("Worker stopped.")

    def process_request(
        self,
        tasks: List[Tuple[str, Optional[Path]]],
        voice_id: str,
        rate: int,
        temp_prefix: str,
        timeout: int,
        proxy: Optional[str],
        pause_ms: int,
        output_format: str,
        gemini_enabled: bool = True,  # Использовать Gemini для ё-фикации
        use_stress: bool = False,
        thinking_mode: bool = False
    ) -> None:
        """Submit a processing request to the worker loop."""
        if not self._ready_event.is_set() or not self.loop:
            self.logger.error("Worker loop not ready yet.")
            self.error.emit("Worker not ready.")
            return

        # Prepare arguments for the coroutine
        coro = self._process_batch(
            tasks, voice_id, rate, temp_prefix, timeout, proxy, pause_ms, output_format, gemini_enabled, use_stress, thinking_mode
        )
        
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    def process_srt_request(
        self,
        marked_text: str,
        entries: List[SubtitleEntry],
        output_path: str,
        quality: str,
        rate: int,
        voice_id: str = None,
        use_stress: bool = False
    ) -> None:
        """Submit an SRT processing request."""
        if not self._ready_event.is_set() or not self.loop:
            self.logger.error("Worker loop not ready yet.")
            self.error.emit("Worker not ready.")
            return

        coro = self._process_srt_request(marked_text, entries, output_path, quality, rate, voice_id, use_stress)
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _process_srt_request(
        self,
        marked_text: str,
        entries: List[SubtitleEntry],
        output_path: str,
        quality: str,
        rate: int,
        voice_id: str = None,
        use_stress: bool = False
    ) -> None:
        try:
            self.logger.info(f"Starting SRT generation: {output_path}")
            
            # Progress callback wrapper
            def progress_cb(current: int, total: int, msg: str) -> None:
                # We can reuse batch_progress or progress signal
                # Let's use progress for text and batch_progress for percentage?
                # Or just progress(msg) and maybe emit batch_progress for bar?
                # Let's use progress(msg) and maybe calculate percent for batch_progress
                self.progress.emit(msg)
                if total > 0:
                    percent = int((current / total) * 100)
                    # Hack: reuse batch_progress to update main progress bar
                    # batch_progress(current, total, filename) -> updates bar
                    # We can pass current/total directly
                    self.batch_progress.emit(current, total, "SRT Generation")

            await generate_srt_audio_from_entries(
                marked_text=marked_text,
                entries=entries,
                output_path=output_path,
                quality=quality,
                rate=rate,
                progress_callback=progress_cb,
                default_voice=voice_id,
                use_stress=use_stress
            )
            
            self.finished.emit(output_path)
            self.logger.info("SRT generation finished.")
            
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.error(f"SRT generation failed: {e}\n{tb}")
            self.error.emit(f"Ошибка генерации SRT: {e}")

    async def _process_batch(
        self,
        tasks: List[Tuple[str, Optional[Path]]],
        voice_id: str,
        rate: int,
        temp_prefix: str,
        timeout: int,
        proxy: Optional[str],
        pause_ms: int,
        output_format: str,
        gemini_enabled: bool = True,
        use_stress: bool = False,
        thinking_mode: bool = False
    ) -> None:
        # Store params for helper methods
        self.voice_id = voice_id
        self.rate = rate
        self.temp_prefix = temp_prefix
        self.timeout = timeout
        self.proxy = proxy
        self.pause_ms = pause_ms
        self.output_format = output_format
        self.gemini_enabled = gemini_enabled
        self.use_stress = use_stress
        self.thinking_mode = thinking_mode
        
        total_files = len(tasks)
        last_generated_path = ""
        
        try:
            for i, (text, output_path) in enumerate(tasks):
                # Determine destination
                final_destination = output_path or Path(self._temp_file_name())
                filename = final_destination.name
                last_generated_path = str(final_destination)
                
                # Emit batch progress
                self.batch_progress.emit(i + 1, total_files, filename)
                self.logger.info(f"Processing file {i+1}/{total_files}: {filename}")

                # Generate audio for this file
                await self._generate_single_file(text, final_destination)
                
                # Emit file finished
                self.file_finished.emit(str(final_destination))
            
            # Emit finished signal with the last file path
            self.finished.emit(last_generated_path)
            
        except Exception as exc:
            tb = traceback.format_exc()
            self.logger.error("Worker failed: %s\n%s", exc, tb)
            self.error.emit(f"{exc}\n{tb}")

    async def _generate_single_file(self, text: str, final_destination: Path) -> None:
        # 0. Fix "yo" letter (Yoditor + Gemini)
        # Note: prepare_text_for_tts calls Gemini. 
        # Since we are in a persistent loop, we should ensure Gemini client is managed correctly.
        # The previous "on-demand" fix in main_window might conflict if we don't init here?
        # Actually, main_window calls init_client() before calling process_request.
        # But since we are in a DIFFERENT THREAD, the global client in gemini_client.py 
        # might be accessed from this thread.
        # `gemini_client` uses a global variable. It is not thread-local.
        # However, `create_client()` creates a NEW client instance.
        # `yo_gemini_async.py` calls `create_client()`.
        # So as long as `_api_key` is set in `gemini_client` (which is done by main thread),
        # `create_client()` inside this thread will work and create a client bound to THIS loop.
        # AND `yo_gemini_async` now has `finally: client.close()`.
        # So this should be fine!
        
        # So this should be fine!
        
        text = await prepare_text_for_tts(text, self.gemini_enabled, self.thinking_mode)
        # Use repr() to avoid UnicodeEncodeError in Windows console with IPA chars
        self.logger.info(f"Текст после обработки (Gemini+Yoditor): {repr(text)}")
        
        if not text or not text.strip():
            self.logger.warning("Text is empty after processing. Skipping generation.")
            return

        # Apply stress if enabled
        # Note: russtress removed. 'use_stress' now only controls raw_ssml for Gemini phonemes.
        
        rate_str = f"{self.rate:+d}%"

        # 1. Chunk the text
        chunks = self._chunk_text(text, max_chars=5000)
        self.logger.info("Text split into %d chunks", len(chunks))
        
        if len(chunks) == 1:
            # Simple case: just one chunk
            self.progress.emit("Генерация аудио...")
            await self._generate_audio(final_destination, chunks[0], rate_str)
            return

        # 2. Generate audio for each chunk
        temp_files = []
        try:
            for i, chunk in enumerate(chunks):
                self.progress.emit(f"Генерация части {i+1} из {len(chunks)}...")
                temp_file = Path(self._temp_file_name())
                await self._generate_audio(temp_file, chunk, rate_str)
                temp_files.append(temp_file)

            # 3. Merge audio files
            self.progress.emit("Склейка аудиофайлов...")
            # _merge_audio_files is synchronous (subprocess), so we run it in executor to not block loop?
            # It uses subprocess.run, which blocks. 
            # Ideally: await asyncio.to_thread(self._merge_audio_files, temp_files, final_destination)
            # But for now, blocking the worker loop is acceptable as it's the only task.
            self._merge_audio_files(temp_files, final_destination)

        except Exception as e:
            self.logger.error(f"Error generating file {final_destination}: {e}")
            raise e
        finally:
            # 4. Cleanup temp files
            for f in temp_files:
                try:
                    if f.exists():
                        f.unlink()
                except Exception as e:
                    self.logger.warning(f"Failed to delete temp file {f}: {e}")

    def _chunk_text(self, text: str, max_chars: int) -> List[str]:
        """Split text into chunks of max_chars, respecting sentence boundaries."""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        while len(text) > max_chars:
            limit_text = text[:max_chars]
            split_idx = -1
            for char in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                idx = limit_text.rfind(char)
                if idx != -1:
                    split_idx = max(split_idx, idx + 1)
            
            if split_idx == -1:
                split_idx = limit_text.rfind('\n')
            
            if split_idx == -1:
                split_idx = limit_text.rfind(' ')
                
            if split_idx == -1:
                split_idx = max_chars

            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        
        if text:
            chunks.append(text)
            
        return chunks

    def _merge_audio_files(self, files: List[Path], output_path: Path) -> None:
        """Merge audio files using ffmpeg."""
        list_file = output_path.with_suffix('.txt')
        try:
            with open(list_file, 'w', encoding='utf-8') as f:
                for file_path in files:
                    safe_path = str(file_path.absolute()).replace('\\', '/')
                    f.write(f"file '{safe_path}'\n")
            
            cmd = [
                'ffmpeg', '-f', 'concat', '-safe', '0',
                '-i', str(list_file), '-c', 'copy', '-y', str(output_path)
            ]
            
            self.logger.info(f"Running ffmpeg: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self.logger.debug(f"ffmpeg output: {result.stderr}")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"ffmpeg failed: {e.stderr}")
            raise RuntimeError(f"Ошибка склейки аудио (ffmpeg): {e.stderr}")
        finally:
            if list_file.exists():
                list_file.unlink()

    async def _generate_audio(self, destination: Path, text: str, rate_str: str) -> int:
        max_retries = 3
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                return await self._attempt_generate_audio(destination, text, rate_str)
            except Exception as e:
                last_error = e
                self.logger.warning(f"Attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)
        
        self.logger.error(f"All {max_retries} attempts failed. Last error: {last_error}")
        if isinstance(last_error, NoAudioReceived):
             raise RuntimeError(
                f"Сервер не вернул данные после {max_retries} попыток.\n"
                "Возможные причины:\n"
                "1. Проблемы с интернет-соединением или прокси (VLESS).\n"
                "2. Текст содержит недопустимые символы или слишком длинный.\n"
                "3. Временный сбой сервиса Microsoft.\n"
                "Попробуйте изменить голос, отключить/включить прокси или повторить попытку."
            ) from last_error
        raise last_error

    async def _attempt_generate_audio(self, destination: Path, text: str, rate_str: str) -> int:
        # 1) Try with mstts:silence (SSML)
        try:
            ssml = self._build_ssml(text, rate_str, use_silence=True, raw_content=self.use_stress)
            communicator = SSMLCommunicate(
                ssml,
                proxy=self.proxy,
                receive_timeout=self.timeout,
                output_format=self.output_format,
            )
            await communicator.save(str(destination))
            return destination.stat().st_size
        except Exception as exc:
            self.logger.warning("SSML synth with mstts:silence failed: %s", exc)

        # 2) Retry with break-only pauses (SSML)
        try:
            ssml = self._build_ssml(text, rate_str, use_silence=False, raw_content=self.use_stress)
            communicator = SSMLCommunicate(
                ssml,
                proxy=self.proxy,
                receive_timeout=self.timeout,
                output_format=self.output_format,
            )
            await communicator.save(str(destination))
            return destination.stat().st_size
        except Exception as exc:
            self.logger.warning("SSML synth with <break> failed: %s", exc)

        # 3) Fallback to plain text (or raw SSML if stress enabled)
        self.logger.warning("Falling back to edge_tts.Communicate without custom pauses.")
        
        if self.use_stress:
             # Wrap in SSML for raw support
            ssml = (
                f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='ru-RU'>"
                f"<voice name='{self.voice_id}'>"
                f"<prosody rate='{rate_str}' pitch='+0Hz'>"
                f"{text}"
                f"</prosody>"
                f"</voice>"
                f"</speak>"
            )
            communicator = edge_tts.Communicate(
                ssml,
                self.voice_id,
                rate=rate_str,
                proxy=self.proxy,
                receive_timeout=self.timeout,
                raw_ssml=True
            )
        else:
            communicator = edge_tts.Communicate(
                text.strip(),
                self.voice_id,
                rate=rate_str,
                proxy=self.proxy,
                receive_timeout=self.timeout,
            )
        
        await asyncio.wait_for(
            communicator.save(str(destination)),
            timeout=self.timeout,
        )
        return destination.stat().st_size

    def _temp_file_name(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix=self.temp_prefix)
        os.close(fd)
        return path

    def _build_ssml(self, text: str, rate_str: str, use_silence: bool, raw_content: bool = False) -> str:
        text = text.strip()
        if raw_content:
            escaped_text = text # Already stressed, don't escape
        else:
            escaped_text = escape(text)
        
        lang = self._voice_lang()
        pause_value = max(0, int(self.pause_ms))

        body_text = (
            self._inject_breaks(escaped_text, pause_value)
            if pause_value > 0
            else escaped_text
        )

        if self.rate != 0:
            body = f'<prosody rate="{rate_str}">{body_text}</prosody>'
        else:
            body = body_text

        silence_block = ""
        if use_silence and pause_value > 0:
            silence_block = (
                f'    <mstts:silence type="Sentenceboundary-exact" value="{pause_value}ms"/>\n'
            )

        return (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"\n'
            '       xmlns:mstts="http://www.w3.org/2001/mstts" '
            f'xml:lang="{lang}">\n'
            f'  <voice name="{escape(self.voice_id)}">\n'
            f"{silence_block}"
            f"    {body}\n"
            "  </voice>\n"
            "</speak>"
        )

    def _voice_lang(self) -> str:
        parts = self.voice_id.split('-')
        if len(parts) >= 2:
            return '-'.join(parts[:2])
        return 'en-US'

    @staticmethod
    def _inject_breaks(text: str, pause_value: int) -> str:
        break_tag = f'<break time="{pause_value}ms"/>'
        return re.sub(r"([.!?:…]+)(\s+)", rf"\1{break_tag}\2", text)
