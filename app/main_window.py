"""Main window for the Edge-TTS desktop app with live logs and process list."""

from __future__ import annotations

import base64
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from .config import AppConfig
from .logger import get_logger
from .tts_worker import TtsWorker
from .version import __version__
from .voices import VOICE_CHOICES, VoiceOption
from vless_manager import VLESSManager
from app.srt_parser import parse_srt_file
from app.voice_markers import generate_marked_text, parse_marked_text
from app.ipa_helper import generate_ipa_variants
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import QMenu
from PySide6.QtCore import QObject, Signal

class LogSignaler(QObject):
    """Helper class to emit signals from logging handler."""
    new_record = Signal(str)

class QtLogHandler(logging.Handler):
    """Logging handler that emits a signal for each log record."""
    def __init__(self):
        super().__init__()
        self.signaler = LogSignaler()
        self.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        self.signaler.new_record.emit(msg)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.colors = {
            "bg_main": "#17212B",
            "bg_panel": "#0E1621",
            "accent": "#3AE2CE",
            "text": "#FFFFFF",
            "btn_primary": "#4B82E5",
            "btn_warning": "#BF8255",
            "btn_action": "#6AF1E2",
        }

        self._log_buffer: list[str] = []
        self.logger = get_logger("edge_tts_app", self.config.log_path)
        
        # Setup global logging redirection to UI
        self.log_handler = QtLogHandler()
        self.log_handler.signaler.new_record.connect(self._append_log_direct)
        logging.getLogger().addHandler(self.log_handler)
        # Also ensure app logger has it if propagation is off
        logging.getLogger("app").addHandler(self.log_handler)

        self.worker: Optional[TtsWorker] = None
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(self.config.default_volume / 100.0)

        self.current_audio_path: Optional[str] = None
        self.vless_manager = VLESSManager(log_func=self._info, socks_port=self.config.vless_port)
        self.vless_proxy: Optional[str] = None
        
        # State for worker signal handling
        self._current_play_after = False
        self._current_show_saved = False
        
        # Determine settings path
        if getattr(sys, 'frozen', False):
            # If frozen (exe), store settings next to the executable
            base_path = Path(sys.executable).parent
        else:
            # If running from source, store in project root
            base_path = Path(__file__).resolve().parent.parent
            
        self.settings_path = base_path / "edge_tts_settings.json"
        
        # Custom dictionary path
        self.dictionary_path = base_path / "custom_dictionary.txt"
        
        # Batch mode state
        self.batch_files: List[Path] = []
        self.batch_output_dir: Optional[Path] = None
        self.batch_output_history: List[str] = []

        self.setWindowTitle(f"Edge-TTS Desktop v{__version__}")
        
        # Set window icon
        icon_path = Path(__file__).resolve().parent.parent / "logo.ico"
        if hasattr(sys, "_MEIPASS"):
            icon_path = Path(sys._MEIPASS) / "logo.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
            
        self.resize(900, 700)
        self._build_ui()
        self._flush_log_buffer()
        self._apply_styles()
        self._load_settings()

        self._info("Приложение запущено")
        self.statusBar().showMessage("Готов")

        if self.config.vless_enabled and self.config.vless_default_url:
            self.vless_url_input.setText(self.config.vless_default_url)
            self._on_vless_connect()
        
        # Initialize Gemini Client
        self._init_gemini_client()
        
        # Initialize Custom Dictionary
        self._init_custom_dictionary()
        
        # Initialize persistent worker
        self.worker = TtsWorker(logger=self.logger)
        self.worker.finished.connect(self._on_worker_finished_signal)
        self.worker.error.connect(self._on_worker_error)
        self.worker.progress.connect(self._on_worker_detail_progress)
        self.worker.batch_progress.connect(self._on_batch_progress)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.start()

        # Restore Thinking Mode state
        if self.config.thinking_mode:
            for b in [getattr(self, 'single_thinking_btn', None), 
                      getattr(self, 'batch_thinking_btn', None), 
                      getattr(self, 'srt_thinking_btn', None)]:
                if b:
                    b.setChecked(True)
                    self._update_thinking_btn_style(b)

    def _init_gemini_client(self) -> None:
        """Initialize Gemini client with current key and proxy."""
        from app.gemini_client import init_client
        
        # Try to get API key from config first, then from UI if available
        api_key = getattr(self.config, 'gemini_api_key', None)
        if not api_key and hasattr(self, 'gemini_key_input'):
            api_key = self.gemini_key_input.text().strip()
        
        if not api_key:
            self._info("Gemini API ключ не установлен")
            return

        # Determine proxy URL if VLESS is running
        proxy_url = None
        if self.vless_manager.is_running:
            # Assuming VLESS manager provides SOCKS5 on local port
            status = self.vless_manager.get_status()
            if status.get("running"):
                proxy_url = status.get("proxy_url") # e.g. socks5://127.0.0.1:10809
        
        try:
            init_client(api_key, http_proxy=proxy_url)
            self._info(f"Gemini клиент инициализирован (ключ: {api_key[:10]}...)")
        except Exception as e:
            self._error(f"Ошибка инициализации Gemini: {e}")

    def _on_gemini_key_changed(self, text: str) -> None:
        """Handle Gemini API key change."""
        self.config.gemini_api_key = text
        self._init_gemini_client()
    
    def _on_gemini_toggle(self, state: int) -> None:
        """Handle Gemini enable/disable toggle."""
        from PySide6.QtCore import Qt
        self.config.gemini_enabled = (state == Qt.Checked)
        status = "включён" if self.config.gemini_enabled else "выключен"
        status = "включён" if self.config.gemini_enabled else "выключен"
        self._info(f"Gemini {status}")

    def _toggle_thinking_btn(self, btn: QPushButton) -> None:
        """Toggle Thinking Mode button state."""
        is_checked = btn.isChecked()
        self.config.thinking_mode = is_checked
        
        # Sync all thinking buttons
        for b in [getattr(self, 'single_thinking_btn', None), 
                  getattr(self, 'batch_thinking_btn', None), 
                  getattr(self, 'srt_thinking_btn', None)]:
            if b and b != btn:
                b.setChecked(is_checked)
                self._update_thinking_btn_style(b)
        
        self._update_thinking_btn_style(btn)
        status = "ON" if is_checked else "OFF"
        self._info(f"Thinking Mode: {status}")

    def _update_thinking_btn_style(self, btn: QPushButton) -> None:
        """Update style and text of Thinking Mode button."""
        if btn.isChecked():
            btn.setText("Thinking: ON")
            btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 11pt;")
        else:
            btn.setText("Thinking: OFF")
            btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")

    def _update_stats_display(self) -> None:
        """Обновить отображение статистики Gemini."""
        from app.gemini_stats import get_stats
        
        stats = get_stats()
        
        text = (
            f"Вызовов: <b>{stats.total_calls}</b> (сессия: <b>{stats.session_calls}</b>)<br>"
            f"Исправлений: <b>{stats.total_corrections}</b><br>"
            f"Среднее время: <b>{stats.avg_time_ms:.0f}</b> мс<br>"
            f"Макс время: <b>{stats.max_time_ms:.0f}</b> мс"
        )
        
        if hasattr(self, 'stats_label'):
            self.stats_label.setText(text)
            
        if hasattr(self, 'stats_table') and stats.detailed_corrections:
            self.stats_table.setRowCount(0)
            
            # Sort by count desc
            sorted_items = sorted(
                stats.detailed_corrections.values(), 
                key=lambda x: x.count, 
                reverse=True
            )
            
            self.stats_table.setRowCount(len(sorted_items))
            for row, item in enumerate(sorted_items):
                self.stats_table.setItem(row, 0, QTableWidgetItem(item.original))
                self.stats_table.setItem(row, 1, QTableWidgetItem(item.corrected))
                
                type_item = QTableWidgetItem("Ё" if item.type == 'yo' else "IPA")
                type_item.setTextAlignment(Qt.AlignCenter)
                if item.type == 'ipa':
                    type_item.setForeground(Qt.cyan)
                self.stats_table.setItem(row, 2, type_item)
                
                count_item = QTableWidgetItem(str(item.count))
                count_item.setTextAlignment(Qt.AlignCenter)
                self.stats_table.setItem(row, 3, count_item)
    
    def _on_reset_stats(self) -> None:
        """Обработчик сброса статистики Gemini."""
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Сбросить всю статистику вызовов Gemini?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            from app.gemini_stats import reset_stats
            reset_stats()
            self._update_stats_display()
            self._info("Статистика Gemini сброшена")
    
    def _init_custom_dictionary(self) -> None:
        """Initialize custom dictionary."""
        from app.custom_dictionary import init_dictionary
        
        try:
            dictionary = init_dictionary(self.dictionary_path)
            if self.dictionary_path.exists():
                self._info(f"Словарь замен загружен: {len(dictionary.replacements)} пар")
            else:
                # Create empty dictionary file
                self.dictionary_path.write_text("# Пользовательский словарь замен\n# Формат: Исходное_слово=Целевое_слово\n\n", encoding='utf-8')
                self._info("Создан пустой файл словаря замен")
        except Exception as e:
            self._error(f"Ошибка инициализации словаря замен: {e}")
    
    def _on_open_dictionary(self) -> None:
        """Open dictionary file in system editor."""
        import os
        import subprocess
        from app.custom_dictionary import get_dictionary
        
        try:
            # Ensure dictionary file exists
            if not self.dictionary_path.exists():
                self.dictionary_path.write_text("# Пользовательский словарь замен\n# Формат: Исходное_слово=Целевое_слово\n\n", encoding='utf-8')
            
            # Auto-sort dictionary before opening
            dictionary = get_dictionary()
            if dictionary:
                dictionary.reload()  # Reload from file
                dictionary.save()    # Save with auto-sort
                self._info("Словарь автоматически отсортирован")
            
            # Open in system editor
            if os.name == 'nt':  # Windows
                os.startfile(str(self.dictionary_path))
            elif os.name == 'posix':  # macOS/Linux
                subprocess.call(['open' if sys.platform == 'darwin' else 'xdg-open', str(self.dictionary_path)])
            
            self._info(f"Открыт файл словаря: {self.dictionary_path}")
            
            self.statusBar().showMessage("Словарь отсортирован. После редактирования сохраните файл.", 5000)
            
        except Exception as e:
            self._error(f"Ошибка открытия словаря: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл словаря:\n{e}")
    
    def _reload_dictionary(self) -> None:
        """Перезагрузить словарь замен."""
        from app.custom_dictionary import get_dictionary
        
        try:
            dictionary = get_dictionary()
            if dictionary:
                dictionary.reload()
                self.logger.debug(f"Словарь перезагружен: {len(dictionary.replacements)} пар")
        except Exception as e:
            self.logger.error(f"Ошибка перезагрузки словаря: {e}")
    
    def _on_open_triggers(self) -> None:
        """Open Gemini triggers file in system editor."""
        import os
        import subprocess
        from app.gemini_triggers import TRIGGERS_FILE, save_triggers, load_triggers
        
        try:
            # Ensure triggers file exists
            if not TRIGGERS_FILE.exists():
                default_triggers = ["все", "еще", "ещё", "нес", "шел", "шёл", "вел", "вёл", "звезд*"]
                save_triggers(default_triggers)
                self._info("Создан файл триггеров с дефолтными значениями")
            
            # Auto-sort triggers before opening
            triggers = load_triggers()
            if triggers:
                save_triggers(triggers)  # Save with auto-sort
                self._info("Триггеры автоматически отсортированы")
            
            # Open in system editor
            if os.name == 'nt':  # Windows
                os.startfile(str(TRIGGERS_FILE))
            elif os.name == 'posix':  # macOS/Linux
                subprocess.call(['open' if sys.platform == 'darwin' else 'xdg-open', str(TRIGGERS_FILE)])
            
            self._info(f"Открыт файл триггеров: {TRIGGERS_FILE}")
            
            self.statusBar().showMessage("Триггеры отсортированы. После редактирования сохраните файл.", 5000)
            
        except Exception as e:
            self._error(f"Ошибка открытия триггеров: {e}")
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл триггеров:\n{e}")

    # --- UI setup ---------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        central.setObjectName("root_widget")

        # Main vertical layout
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 1. Top Section: VLESS Controls (Global)
        vless_group = QWidget()
        vless_main_layout = QVBoxLayout(vless_group)
        vless_main_layout.setContentsMargins(0, 0, 0, 0)
        vless_main_layout.setSpacing(5)
        
        # Row 1: Status
        self.vless_status = QLabel("Статус: Отключено")
        vless_main_layout.addWidget(self.vless_status)

        # Row 2: Input and Buttons
        vless_controls_layout = QHBoxLayout()
        vless_controls_layout.setContentsMargins(0, 0, 0, 0)
        
        vless_controls_layout.addWidget(QLabel("VLESS Proxy:"))
        self.vless_url_input = QLineEdit()
        self.vless_url_input.setPlaceholderText("vless://UUID@server:port?...")
        if self.config.vless_default_url:
            self.vless_url_input.setText(self.config.vless_default_url)
        vless_controls_layout.addWidget(self.vless_url_input)

        self.vless_toggle_btn = QPushButton("VLESS: OFF")
        self.vless_toggle_btn.setCheckable(True)
        self.vless_toggle_btn.setMinimumHeight(35)
        self.vless_toggle_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.vless_toggle_btn.clicked.connect(lambda: self._toggle_vless_btn(self.vless_toggle_btn))
        vless_controls_layout.addWidget(self.vless_toggle_btn)
        
        vless_main_layout.addLayout(vless_controls_layout)

        main_layout.addWidget(vless_group)

        # 1.5 Global Toolbar (Dictionary, Triggers)
        global_toolbar = QHBoxLayout()
        global_toolbar.setContentsMargins(0, 5, 0, 5)
        
        self.dictionary_btn = QPushButton("📖 Словарь замен")
        self.dictionary_btn.clicked.connect(self._on_open_dictionary)
        self.dictionary_btn.setToolTip("Открыть файл словаря для редактирования")
        self.dictionary_btn.setMinimumHeight(35)
        global_toolbar.addWidget(self.dictionary_btn)
        
        self.triggers_btn = QPushButton("⚡ Триггеры")
        self.triggers_btn.clicked.connect(self._on_open_triggers)
        self.triggers_btn.setToolTip("Редактировать триггеры для Gemini AI")
        self.triggers_btn.setMinimumHeight(35)
        global_toolbar.addWidget(self.triggers_btn)
        
        global_toolbar.addStretch()
        main_layout.addLayout(global_toolbar)

        # 2. Tabs: Single Text / Batch Processing
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs, stretch=1)

        # Tab 1: Single Text
        self.tab_single = QWidget()
        self._build_single_tab(self.tab_single)
        self.tabs.addTab(self.tab_single, "📝 Один файл (Текст)")

        # Tab 2: SRT Voicing
        self.srt_tab = QWidget()
        self._build_srt_tab(self.srt_tab)
        self.tabs.addTab(self.srt_tab, "📹 Озвучка субтитров")

        # Tab 3: Batch Processing
        self.tab_batch = QWidget()
        self._build_batch_tab(self.tab_batch)
        self.tabs.addTab(self.tab_batch, "📦 Пакетная обработка")

        # Tab 4: Gemini Statistics
        self.tab_stats = QWidget()
        self._build_stats_tab(self.tab_stats)
        self.tabs.addTab(self.tab_stats, "📊 Статистика Gemini")

        # Tab 5: Logs
        self.tab_logs = QWidget()
        self._build_logs_tab(self.tab_logs)
        self.tabs.addTab(self.tab_logs, "📜 Логи")

        # 3. Common Controls (Voice, Rate, Pause)
        controls_group = QWidget()
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Голос:"))
        self.voice_combo = QComboBox()
        voices = list(VOICE_CHOICES)
        if all(v.voice_id != self.config.default_voice for v in voices):
            voices.insert(0, VoiceOption(f"Custom ({self.config.default_voice})", self.config.default_voice))
        
        selected_idx = 0
        for idx, voice in enumerate(voices):
            self.voice_combo.addItem(voice.label, voice.voice_id)
            if voice.voice_id == self.config.default_voice:
                selected_idx = idx
        self.voice_combo.setCurrentIndex(selected_idx)
        self.voice_combo.setCurrentIndex(selected_idx)
        self.voice_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        settings_row.addWidget(self.voice_combo, stretch=2)

        settings_row.addWidget(QLabel("Скорость:"))
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(-50, 50)
        self.rate_spin.setValue(self.config.default_rate)
        self.rate_spin.valueChanged.connect(self._on_rate_spin_changed)
        settings_row.addWidget(self.rate_spin)
        
        self.rate_label = QLabel(self._rate_label_text(self.config.default_rate))
        settings_row.addWidget(self.rate_label)

        settings_row.addWidget(QLabel("Пауза (мс):"))
        self.pause_spin = QSpinBox()
        self.pause_spin.setRange(0, 2000)
        self.pause_spin.setSingleStep(50)
        self.pause_spin.setValue(300)
        settings_row.addWidget(self.pause_spin)
        
        settings_row.addWidget(QLabel("Качество:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("Standard (48kbps)", "audio-24khz-48kbitrate-mono-mp3")
        self.quality_combo.addItem("High (96kbps)", "audio-24khz-96kbitrate-mono-mp3")
        self.quality_combo.addItem("Ultra (192kbps)", "audio-48khz-192kbitrate-mono-mp3")
        settings_row.addWidget(self.quality_combo, stretch=1)
        
        controls_layout.addLayout(settings_row)
        main_layout.addWidget(controls_group)

        # 5. Status & Progress (Global)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Прогресс:"))
        
        self.progress_scroll = QScrollArea()
        self.progress_scroll.setWidgetResizable(True)
        self.progress_scroll.setFixedHeight(45)
        self.progress_scroll.setFrameShape(QFrame.NoFrame)
        self.progress_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.progress_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.progress_scroll.setStyleSheet("background: transparent;")
        
        self.progress_label = QLabel("Готов")
        self.progress_label.setStyleSheet("background: transparent;")
        self.progress_scroll.setWidget(self.progress_label)
        
        status_row.addWidget(self.progress_scroll)
        main_layout.addLayout(status_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.hide()
        main_layout.addWidget(self.progress)
        
        # Detailed status for batch
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFixedHeight(45)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.detail_scroll.setStyleSheet("background: transparent;")
        self.detail_scroll.hide()

        self.detail_progress_label = QLabel("")
        self.detail_progress_label.setStyleSheet(f"color: {self.colors['accent']}; font-size: 10pt; background: transparent;")
        self.detail_scroll.setWidget(self.detail_progress_label)
        
        main_layout.addWidget(self.detail_scroll)



    def _build_single_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        
        # Header with IPA Toggle
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Текст для озвучивания:"))
        header_layout.addStretch()
        
        # Font size
        header_layout.addWidget(QLabel("Размер шрифта:"))
        self.single_font_spin = QSpinBox()
        self.single_font_spin.setRange(8, 72)
        self.single_font_spin.setValue(15)
        self.single_font_spin.valueChanged.connect(self._on_single_font_size_changed)
        header_layout.addWidget(self.single_font_spin)
        
        self.single_ipa_btn = QPushButton("IPA: OFF")
        self.single_ipa_btn.setCheckable(True)
        self.single_ipa_btn.setMinimumHeight(35)
        self.single_ipa_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.single_ipa_btn.clicked.connect(lambda: self._toggle_ipa_btn(self.single_ipa_btn))
        header_layout.addWidget(self.single_ipa_btn)

        self.single_thinking_btn = QPushButton("Thinking: OFF")
        self.single_thinking_btn.setCheckable(True)
        self.single_thinking_btn.setMinimumHeight(35)
        self.single_thinking_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.single_thinking_btn.clicked.connect(lambda: self._toggle_thinking_btn(self.single_thinking_btn))
        header_layout.addWidget(self.single_thinking_btn)

        # Fix Stress Button
        self.single_fix_stress_btn = QPushButton("✨ Fix Stress")
        self.single_fix_stress_btn.setToolTip("Исправить ударение в выделенном слове (через Gemini)")
        self.single_fix_stress_btn.setMinimumHeight(35)
        self.single_fix_stress_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.single_fix_stress_btn.clicked.connect(lambda: self._on_fix_stress_btn_click(self.text_edit))
        header_layout.addWidget(self.single_fix_stress_btn)
        
        layout.addLayout(header_layout)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Введите текст для преобразования в речь...")
        self.text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(
            lambda pos: self._on_context_menu(pos, self.text_edit)
        )
        layout.addWidget(self.text_edit)

        actions_row = QHBoxLayout()
        
        self.preview_btn = QPushButton("Прослушать")
        self.preview_btn.clicked.connect(self.on_preview)
        self.preview_btn.setMinimumHeight(60)
        self.preview_btn.setStyleSheet(f"background-color: {self.colors['btn_action']}; color: black; font-weight: bold; font-size: 14pt; border-radius: 5px;")
        actions_row.addWidget(self.preview_btn)

        self.save_btn = QPushButton("Сохранить в MP3")
        self.save_btn.clicked.connect(self.on_save)
        self.save_btn.setMinimumHeight(60)
        self.save_btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 14pt; border-radius: 5px;")
        actions_row.addWidget(self.save_btn)

        # Auto Stress Checkbox (Single) - REMOVED in favor of IPA button logic or kept as legacy?
        # User asked for IPA button. Let's keep checkbox for now but maybe sync them?
        # Actually, let's hide the old checkbox if we use the new button.
        # But the user said "buttons for enabling IPA".
        # Let's keep the old checkbox for "Auto Stress (RusStress)" and new button for "IPA Mode"?
        # Or maybe the button REPLACES the checkbox?
        # "И еще кнопки включения ipa сделай вверху каждой вкладки"
        # Let's assume this button controls the "use_stress" flag which now means "Auto IPA/Stress".
        
        self.single_stress_cb = QCheckBox("Авто-ударения")
        self.single_stress_cb.setToolTip("Автоматически расставлять ударения (экспериментально)")
        self.single_stress_cb.hide() # Hide old checkbox
        actions_row.addWidget(self.single_stress_cb)

        self.clear_text_btn = QPushButton("Очистить")
        self.clear_text_btn.setObjectName("clear_text_btn")
        self.clear_text_btn.setMinimumHeight(60)
        self.clear_text_btn.clicked.connect(self._on_clear_text)
        actions_row.addWidget(self.clear_text_btn)
        
        layout.addLayout(actions_row)

        playback_row = QHBoxLayout()
        self.play_btn = QPushButton("Старт")
        self.play_btn.clicked.connect(self._on_playback_start)
        self.play_btn.setMinimumHeight(60)
        playback_row.addWidget(self.play_btn)

        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.clicked.connect(self._on_playback_pause)
        self.pause_btn.setMinimumHeight(60)
        playback_row.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.clicked.connect(self.stop_audio)
        self.stop_btn.setMinimumHeight(60)
        playback_row.addWidget(self.stop_btn)
        
        
        layout.addLayout(playback_row)

    def _build_batch_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        
        # Header with IPA Toggle
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Список файлов (.txt):"))
        header_layout.addStretch()
        
        self.batch_ipa_btn = QPushButton("IPA: OFF")
        self.batch_ipa_btn.setCheckable(True)
        self.batch_ipa_btn.setMinimumHeight(35)
        self.batch_ipa_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.batch_ipa_btn.clicked.connect(lambda: self._toggle_ipa_btn(self.batch_ipa_btn))
        header_layout.addWidget(self.batch_ipa_btn)

        self.batch_thinking_btn = QPushButton("Thinking: OFF")
        self.batch_thinking_btn.setCheckable(True)
        self.batch_thinking_btn.setMinimumHeight(35)
        self.batch_thinking_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.batch_thinking_btn.clicked.connect(lambda: self._toggle_thinking_btn(self.batch_thinking_btn))
        header_layout.addWidget(self.batch_thinking_btn)
        
        layout.addLayout(header_layout)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.file_list)
        
        btn_row = QHBoxLayout()
        self.add_files_btn = QPushButton("Добавить файлы")
        self.add_files_btn.clicked.connect(self._on_add_files)
        btn_row.addWidget(self.add_files_btn)
        
        self.remove_file_btn = QPushButton("Удалить")
        self.remove_file_btn.setObjectName("remove_file_btn")
        self.remove_file_btn.clicked.connect(self._on_remove_file)
        btn_row.addWidget(self.remove_file_btn)
        
        self.clear_list_btn = QPushButton("Очистить список")
        self.clear_list_btn.setObjectName("clear_list_btn")
        self.clear_list_btn.clicked.connect(self._on_clear_list)
        btn_row.addWidget(self.clear_list_btn)
        layout.addLayout(btn_row)
        
        folder_row = QHBoxLayout()
        self.output_folder_combo = QComboBox()
        self.output_folder_combo.setEditable(False)
        self.output_folder_combo.addItem("Сохранять в папку с файлом") # Index 0
        folder_row.addWidget(self.output_folder_combo, stretch=1)
        
        self.select_folder_btn = QPushButton("Выбрать папку")
        self.select_folder_btn.clicked.connect(self._on_select_output_folder)
        folder_row.addWidget(self.select_folder_btn)
        layout.addLayout(folder_row)
        
        self.start_batch_btn = QPushButton("Начать пакетную обработку")
        self.start_batch_btn.clicked.connect(self._on_start_batch)
        self.start_batch_btn.setStyleSheet(f"background-color: {self.colors['btn_action']}; color: black; font-weight: bold; font-size: 14pt; border-radius: 5px;")
        self.start_batch_btn.setMinimumHeight(60)
        
        # Batch actions layout
        batch_actions = QVBoxLayout()
        batch_actions.addWidget(self.start_batch_btn)

        # Auto Stress Checkbox (Batch)
        self.batch_stress_cb = QCheckBox("Авто-ударения")
        self.batch_stress_cb.setToolTip("Автоматически расставлять ударения (экспериментально)")
        self.batch_stress_cb.hide()
        batch_actions.addWidget(self.batch_stress_cb)
        
        layout.addLayout(batch_actions)

    def _build_srt_tab(self, parent: QWidget) -> None:
        """Build SRT Voicing tab."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        # layout.setSpacing(15) # Removed to match Single tab default spacing

        # Controls Row 1: Load, Edit, Font, IPA, Thinking
        controls_row = QHBoxLayout()
        controls_row.setSpacing(6) # Force standard small spacing between buttons
        
        self.load_srt_btn = QPushButton("📂 Загрузить .srt")
        self.load_srt_btn.clicked.connect(self._on_load_srt)
        self.load_srt_btn.setMinimumHeight(40)
        controls_row.addWidget(self.load_srt_btn)

        self.edit_markers_btn = QPushButton("✏️ Редактировать метки")
        self.edit_markers_btn.clicked.connect(self._on_edit_markers)
        self.edit_markers_btn.setMinimumHeight(40)
        self.edit_markers_btn.setEnabled(False)
        controls_row.addWidget(self.edit_markers_btn)

        # Spacer
        controls_row.addStretch()

        # Font size control
        controls_row.addWidget(QLabel("Размер шрифта:"))
        self.srt_font_spin = QSpinBox()
        self.srt_font_spin.setRange(8, 72)
        self.srt_font_spin.setValue(22)
        self.srt_font_spin.valueChanged.connect(self._on_srt_font_size_changed)
        controls_row.addWidget(self.srt_font_spin)

        # IPA Button
        self.srt_ipa_btn = QPushButton("IPA: OFF")
        self.srt_ipa_btn.setCheckable(True)
        self.srt_ipa_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.srt_ipa_btn.clicked.connect(lambda: self._toggle_ipa_btn(self.srt_ipa_btn))
        self.srt_ipa_btn.setMinimumHeight(35)
        controls_row.addWidget(self.srt_ipa_btn)

        # Thinking Button
        self.srt_thinking_btn = QPushButton("Thinking: OFF")
        self.srt_thinking_btn.setCheckable(True)
        self.srt_thinking_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.srt_thinking_btn.clicked.connect(lambda: self._toggle_thinking_btn(self.srt_thinking_btn))
        self.srt_thinking_btn.setMinimumHeight(35)
        controls_row.addWidget(self.srt_thinking_btn)

        # Fix Stress Button
        self.srt_fix_stress_btn = QPushButton("✨ Fix Stress")
        self.srt_fix_stress_btn.setToolTip("Исправить ударение в выделенном слове (через Gemini)")
        self.srt_fix_stress_btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
        self.srt_fix_stress_btn.setMinimumHeight(35)
        self.srt_fix_stress_btn.clicked.connect(lambda: self._on_fix_stress_btn_click(self.srt_preview))
        controls_row.addWidget(self.srt_fix_stress_btn)

        # Auto Stress Checkbox (Hidden)
        self.auto_stress_cb = QCheckBox("Авто-ударения")
        self.auto_stress_cb.setToolTip("Автоматически расставлять ударения (экспериментально)")
        self.auto_stress_cb.setStyleSheet("font-size: 11pt; font-weight: bold;")
        self.auto_stress_cb.hide()
        controls_row.addWidget(self.auto_stress_cb)

        layout.addLayout(controls_row)

        # Preview Area
        preview_group = QGroupBox("Предпросмотр (текст с метками)")
        preview_group.setStyleSheet("QGroupBox { font-size: 12pt; font-weight: bold; color: #FFFFFF; }")
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(10, 20, 10, 10)

        self.srt_preview = QTextEdit()
        self.srt_preview.setReadOnly(False) # Allow editing for stress fixing
        self.srt_preview.setContextMenuPolicy(Qt.CustomContextMenu)
        self.srt_preview.customContextMenuRequested.connect(
            lambda pos: self._on_context_menu(pos, self.srt_preview)
        )
        self.srt_preview.setPlaceholderText("Здесь появится текст субтитров с метками голосов...")
        self.srt_preview.setStyleSheet("font-family: Calibri; font-size: 22pt;")
        preview_layout.addWidget(self.srt_preview)

        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # Controls Row 2: Generate
        gen_row = QHBoxLayout()
        
        self.generate_srt_btn = QPushButton("🎬 Генерировать озвучку")
        self.generate_srt_btn.clicked.connect(self._on_generate_srt)
        self.generate_srt_btn.setMinimumHeight(60)
        self.generate_srt_btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 14pt; border-radius: 5px;")
        self.generate_srt_btn.setEnabled(False)
        gen_row.addWidget(self.generate_srt_btn)

        layout.addLayout(gen_row)

        # Status Label
        self.srt_status_label = QLabel("")
        self.srt_status_label.setStyleSheet("color: #AAAAAA; font-size: 10pt;")
        layout.addWidget(self.srt_status_label)

    def _build_stats_tab(self, parent: QWidget) -> None:
        """Build Gemini Statistics tab."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Title and description
        title_label = QLabel("📊 Статистика использования Gemini AI")
        title_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #CCCCCC;")
        layout.addWidget(title_label)
        
        # Control buttons row (Checkbox, API Key, Reset Button)
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        
        # Gemini Enable/Disable Toggle Button
        self.gemini_toggle_btn = QPushButton("Gemini: OFF")
        self.gemini_toggle_btn.setCheckable(True)
        self.gemini_toggle_btn.setChecked(self.config.gemini_enabled)
        self.gemini_toggle_btn.clicked.connect(lambda: self._toggle_gemini_btn(self.gemini_toggle_btn))
        self.gemini_toggle_btn.setToolTip("Включить контекстный анализ омографов с помощью Gemini AI")
        self.gemini_toggle_btn.setMinimumHeight(35)
        # Initial style set
        self._toggle_gemini_btn(self.gemini_toggle_btn, initial=True)
        controls_row.addWidget(self.gemini_toggle_btn)
        
        # Gemini API Key field
        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setPlaceholderText("AIzaSy...")
        self.gemini_key_input.setEchoMode(QLineEdit.Password)
        if self.config.gemini_api_key:
            self.gemini_key_input.setText(self.config.gemini_api_key)
        self.gemini_key_input.textChanged.connect(self._on_gemini_key_changed)
        controls_row.addWidget(self.gemini_key_input, stretch=2)
        
        # Reset button (Moved to top right)
        reset_stats_btn = QPushButton("🔄 Сбросить")
        reset_stats_btn.setToolTip("Очистить всю статистику вызовов Gemini")
        reset_stats_btn.clicked.connect(self._on_reset_stats)
        reset_stats_btn.setMinimumHeight(35)
        reset_stats_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.colors['btn_warning']}; 
                color: white; 
                font-weight: bold; 
                border-radius: 5px; 
                padding: 8px 15px 12px 15px;
                margin-bottom: 5px;
            }}
            QPushButton:hover {{
                background-color: #D49A6A;
            }}
        """)
        controls_row.addWidget(reset_stats_btn)
        
        layout.addLayout(controls_row)
        
        # Statistics group
        stats_group = QGroupBox("Метрики")
        stats_group.setStyleSheet("QGroupBox { font-size: 12pt; font-weight: bold; color: #FFFFFF; }")
        stats_layout = QVBoxLayout()
        stats_layout.setContentsMargins(15, 15, 15, 15)
        stats_layout.setSpacing(10)
        
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet("font-size: 13pt; line-height: 1.8;")
        stats_layout.addWidget(self.stats_label)
        
        # Detailed stats table
        stats_layout.addWidget(QLabel("Детальная история исправлений:"))
        
        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(4)
        self.stats_table.setHorizontalHeaderLabels(["Оригинал", "Исправление", "Тип", "Кол-во"])
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # Force specific style for this table to ensure dark background
        self.stats_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                gridline-color: rgba(58,226,206,0.3);
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
            }}
            QHeaderView {{
                background-color: {self.colors['bg_panel']};
            }}
            QHeaderView::section {{
                background-color: #2B3440;
                color: {self.colors['text']};
                padding: 4px;
                border: 1px solid rgba(58,226,206,0.3);
            }}
            QTableCornerButton::section {{
                background-color: {self.colors['bg_panel']};
                border: 1px solid rgba(58,226,206,0.3);
            }}
        """)
        
        # Explicitly style vertical header to avoid gray background
        self.stats_table.verticalHeader().setStyleSheet(f"""
            QHeaderView {{
                background-color: {self.colors['bg_panel']};
            }}
            QHeaderView::section {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
            }}
        """)

        stats_layout.addWidget(self.stats_table)

        self._update_stats_display()  # Начальная загрузка
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)
        
        # Spacer to push content to top
        layout.addStretch()

    def _build_logs_tab(self, parent: QWidget) -> None:
        """Build Logs tab."""
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Controls
        controls_layout = QHBoxLayout()
        
        clear_btn = QPushButton("Очистить логи")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        clear_btn.setMinimumHeight(30)
        controls_layout.addWidget(clear_btn)
        
        copy_btn = QPushButton("Копировать все")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.log_view.toPlainText()))
        copy_btn.setMinimumHeight(30)
        controls_layout.addWidget(copy_btn)
        
        controls_layout.addStretch()
        layout.addLayout(controls_layout)
        
        # Log View
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        self.log_view.setStyleSheet(f"font-family: Consolas, monospace; font-size: 15pt; background-color: {self.colors['bg_panel']}; color: #CCCCCC;")
        layout.addWidget(self.log_view)
    def _on_rate_changed(self, value: int) -> None:
        # Deprecated slider handler, but keeping for safety if needed or removing
        pass

    def _on_rate_spin_changed(self, value: int) -> None:
        self.rate_label.setText(self._rate_label_text(value))

    def _start_worker(
        self,
        tasks: List[tuple[str, Optional[Path]]],
        voice_id: str,
        rate: int,
        quality: str,
        play_after: bool = False,
        show_saved_message: bool = False,
        thinking_mode: bool = False
    ) -> None:
        """Start the TTS worker with the given tasks."""
        if not tasks:
            return

        # Stop any existing playback
        self.stop_audio()
        
        # Update UI state
        self._lock_ui(True)
        self._set_status("Генерация...", busy=True)
        self.progress.setValue(0)
        self.progress.show()
        
        # Store state for callback
        self._current_play_after = play_after
        self._current_show_saved = show_saved_message
        
        # Get other settings
        temp_prefix = self.config.temp_prefix
        timeout = self.config.request_timeout
        proxy = self.vless_proxy if self.config.vless_enabled else None
        pause_ms = self.pause_spin.value()
        
        # Determine stress setting based on active tab
        use_stress = False
        current_tab = self.tabs.currentIndex()
        if current_tab == 0: # Single
            use_stress = self.single_ipa_btn.isChecked()
        elif current_tab == 2: # Batch (was 1)
            use_stress = self.batch_ipa_btn.isChecked()
            
        # Send request to worker
        self.worker.process_request(
            tasks=tasks,
            voice_id=voice_id,
            rate=rate,
            temp_prefix=temp_prefix,
            timeout=timeout,
            proxy=proxy,
            pause_ms=pause_ms,
            output_format=quality,
            gemini_enabled=self.config.gemini_enabled,
            use_stress=use_stress,
            thinking_mode=thinking_mode
        )

    def on_preview(self) -> None:
        # Reload dictionary before TTS generation
        self._reload_dictionary()
        
        text, voice_id, rate, quality, thinking_mode = self._collect_settings()
        if not text:
            self._warn("Пожалуйста, введите текст перед генерацией.")
            return
        self._info(f"Запрос превью: голос={voice_id}, скорость={rate}, качество={quality}, thinking={thinking_mode}")
        self._start_worker([(text, None)], voice_id, rate, quality, play_after=True, thinking_mode=thinking_mode)

    def on_save(self) -> None:
        # Reload dictionary before TTS generation
        self._reload_dictionary()
        
        self.stop_audio()
        text, voice_id, rate, quality, thinking_mode = self._collect_settings()
        if not text:
            self._warn("Пожалуйста, введите текст перед сохранением.")
            return

        suggested_dir = str(self.config.output_dir)
        self.config.ensure_paths()
        file_path_str, _ = QFileDialog.getSaveFileName(
            self, "Сохранить MP3", suggested_dir, "Audio Files (*.mp3)"
        )
        if not file_path_str:
            return

        output_path = Path(file_path_str)
        if output_path.suffix.lower() != ".mp3":
            output_path = output_path.with_suffix(".mp3")

        self._info(f"Запрос сохранения: голос={voice_id}, скорость={rate}, качество={quality}, путь={output_path}, thinking={thinking_mode}")
        self._start_worker(
            [(text, output_path)], 
            voice_id, 
            rate,
            quality, 
            play_after=False, 
            show_saved_message=True,
            thinking_mode=thinking_mode
        )

    # --- Batch Handlers ---
    def _on_add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите текстовые файлы", str(self.config.output_dir), "Text Files (*.txt)"
        )
        if files:
            for f in files:
                path = Path(f)
                if path not in self.batch_files:
                    self.batch_files.append(path)
                    self.file_list.addItem(path.name)
            self._info(f"Добавлено файлов: {len(files)}")

    def _on_remove_file(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            return
            
        # Collect rows to remove
        rows = []
        for item in items:
            row = self.file_list.row(item)
            rows.append(row)
            
        # Remove in reverse order to maintain indices
        for row in sorted(rows, reverse=True):
            self.file_list.takeItem(row)
            self.batch_files.pop(row)

    def _on_clear_list(self) -> None:
        self.file_list.clear()
        self.batch_files.clear()

    def _on_select_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения")
        if folder:
            path_str = str(Path(folder))
            # Add to history
            if path_str in self.batch_output_history:
                self.batch_output_history.remove(path_str)
            self.batch_output_history.insert(0, path_str)
            self.batch_output_history = self.batch_output_history[:5]
            
            self._update_output_history_ui()
            # Select the newly added folder (index 1 because 0 is "Same folder")
            self.output_folder_combo.setCurrentIndex(1)

    def _on_start_batch(self) -> None:
        # Reload dictionary before TTS generation
        self._reload_dictionary()
        
        if not self.batch_files:
            self._warn("Список файлов пуст.")
            return

        voice_id = self.voice_combo.currentData()
        rate = self.rate_spin.value()
        quality = self.quality_combo.currentData()
        thinking_mode = self.config.thinking_mode
        
        # Determine output folder
        output_mode_idx = self.output_folder_combo.currentIndex()
        output_dir = None
        if output_mode_idx > 0:
            # Specific folder selected
            path_str = self.output_folder_combo.currentText()
            if path_str != "Сохранять в папку с файлом":
                output_dir = Path(path_str)

        # Prepare tasks
        tasks = []
        for file_path in self.batch_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                # Try cp1251 fallback
                try:
                    with open(file_path, "r", encoding="cp1251") as f:
                        text = f.read()
                except Exception as e:
                    self._error(f"Ошибка чтения {file_path}: {e}")
                    continue
            except Exception as e:
                self._error(f"Ошибка чтения {file_path}: {e}")
                continue
                
            if not text.strip():
                continue
                
            # Determine output path
            if output_dir:
                out_path = output_dir / file_path.with_suffix(".mp3").name
            else:
                out_path = file_path.with_suffix(".mp3")
                
            tasks.append((text, out_path))
            
        if not tasks:
            self._warn("Нет задач для обработки (пустые файлы или ошибки чтения).")
            return
            
        self._info(f"Старт пакетной обработки: {len(tasks)} файлов, thinking={thinking_mode}")
        self._lock_ui(True)
        self._set_status("Обработка...", busy=True)
        self.progress.setValue(0)
        self.progress.show()
        
        self.start_batch_btn.setEnabled(False)
        self.stop_batch_btn.setEnabled(True)
        
        self._start_worker(
            tasks, 
            voice_id, 
            rate, 
            quality, 
            play_after=False, 
            show_saved_message=False,
            thinking_mode=thinking_mode
        )

    def _on_stop_batch(self) -> None:
        self.worker.stop()
        self._info("Пакетная обработка остановлена пользователем")
        self._lock_ui(False)
        self.start_batch_btn.setEnabled(True)
        self.stop_batch_btn.setEnabled(False)
        self._set_status("Остановлено", busy=False)

    def _update_output_history_ui(self) -> None:
        current_text = self.output_folder_combo.currentText()
        self.output_folder_combo.clear()
        self.output_folder_combo.addItem("Сохранять в папку с файлом")
        for path in self.batch_output_history:
            self.output_folder_combo.addItem(path)
            
        # Try to restore selection
        index = self.output_folder_combo.findText(current_text)
        if index >= 0:
            self.output_folder_combo.setCurrentIndex(index)
        else:
            self.output_folder_combo.setCurrentIndex(0)

    def _on_file_finished(self, path: str) -> None:
        self._info(f"Файл готов: {path}")

    def _on_worker_finished(self, path: str, play_after: bool = False, show_saved_message: bool = False) -> None:
        self._lock_ui(False)
        self._set_status("Готов", busy=False)
        self.progress.setValue(100)
        self.detail_scroll.hide()
        
        self.current_audio_path = path
        if play_after:
            self._play_audio(path)
            self._info(f"Превью готово: {path}")
        if show_saved_message:
            self._info(f"Сохранено в: {path}")
            self.statusBar().showMessage(f"Сохранено в: {path}", 5000)
        
        # Batch finished message
        if not play_after and not show_saved_message:
             QMessageBox.information(self, "Готово", "Пакетная обработка завершена!")
        
        # Update Gemini stats display
        self._update_stats_display()

    def _on_worker_finished_signal(self, path: str) -> None:
        """Handle finished signal from persistent worker."""
        self._on_worker_finished(
            path, 
            play_after=self._current_play_after, 
            show_saved_message=self._current_show_saved
        )


    def _on_worker_error(self, message: str) -> None:
        self._lock_ui(False)
        self._set_status("Ошибка", busy=False)
        self._error(f"Ошибка: {message}")
        QMessageBox.critical(self, "Ошибка генерации", f"Не удалось выполнить задачу:\n{message}")


    def _on_batch_progress(self, current: int, total: int, filename: str) -> None:
        percent = int((current - 1) / total * 100)
        self.progress.setValue(percent)
        self.progress_label.setText(f"Обработка файла {current} из {total}: {filename}")
        self.progress.setVisible(True)

    def _on_worker_detail_progress(self, msg: str) -> None:
        self.detail_progress_label.setText(msg)

    def stop_audio(self) -> None:
        self.player.stop()
        self.play_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Пауза")

    def _on_playback_start(self) -> None:
        if not self.current_audio_path:
            return
        self.player.play()
        self._info("Воспроизведение продолжено")

    def _on_playback_pause(self) -> None:
        self.player.pause()
        self._info("Пауза воспроизведения")

    def _on_playback_stop(self) -> None:
        self.stop_audio()
        self._info("Воспроизведение остановлено")

    def _play_audio(self, path: str) -> None:
        self.stop_audio()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._info(f"Воспроизведение: {path}")

    def _lock_ui(self, locked: bool) -> None:
        self.tabs.setDisabled(locked)
        self.voice_combo.setDisabled(locked)
        self.rate_spin.setDisabled(locked)
        self.stop_btn.setDisabled(locked)

        self.play_btn.setDisabled(locked)
        self.pause_btn.setDisabled(locked)
        
        if not locked:
            self.progress.hide()
        if not locked:
            self.progress.hide()
            self.detail_scroll.hide()

    def _set_status(self, text: str, busy: bool) -> None:
        self.progress_label.setText(text)
        self.progress.setVisible(busy)
        if not busy:
            self.pause_btn.setText("Пауза")

    @staticmethod
    def _rate_label_text(value: int) -> str:
        return f"{value:+d}%"

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Внимание", message)

    def _append_log(self, message: str) -> None:
        # Legacy method, now just logs to logger which triggers handler
        # But to avoid recursion if called from _info which calls logger.info...
        # We should deprecate direct calls to _append_log or make it just update UI
        # If _info calls logger.info, that triggers handler -> _append_log_direct
        # So _info should NOT call _append_log manually anymore.
        pass

    def _append_log_direct(self, message: str) -> None:
        """Slot for log handler signal."""
        if hasattr(self, "log_view") and self.log_view is not None:
            self.log_view.append(message)
        else:
            self._log_buffer.append(message)

    def _on_clear_text(self) -> None:
        self.text_edit.clear()
        self.statusBar().showMessage("Текст очищен", 3000)

    def _info(self, message: str) -> None:
        self.logger.info(message)
        # self._append_log(message) # Removed to avoid double logging

    def _error(self, message: str) -> None:
        self.logger.error(message)
        # self._append_log(message) # Removed to avoid double logging

    def _flush_log_buffer(self) -> None:
        if not hasattr(self, "log_view") or self.log_view is None:
            return
        for entry in self._log_buffer:
            self.log_view.append(entry)
        self._log_buffer.clear()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#root_widget {{
                background-color: {self.colors['bg_main']};
                font-family: "Aptos", "Segoe UI", sans-serif;
                font-size: 15pt;
                color: {self.colors['text']};
            }}
            QMessageBox {{
                background-color: {self.colors['bg_panel']};
            }}
            QMessageBox QLabel {{
                color: {self.colors['text']};
            }}
            QLabel {{
                color: {self.colors['accent']};
                font-weight: 600;
            }}
            QTextEdit, QListWidget, QLineEdit, QTableWidget {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
                padding: 6px;
            }}
            QSpinBox {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
                padding: 4px 6px;
            }}
            QComboBox {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
                padding: 6px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                selection-background-color: {self.colors['accent']};
                selection-color: black;
            }}
            QPushButton {{
                border-radius: 8px;
                border: 1px solid transparent;
                padding: 8px 14px;
                font-size: 14pt;
                color: {self.colors['text']};
                background-color: {self.colors['btn_primary']};
            }}
            QPushButton#stop_btn, QPushButton#vless_stop_btn {{
                background-color: {self.colors['btn_warning']};
                color: {self.colors['text']};
            }}
            QPushButton#clear_text_btn, QPushButton#remove_file_btn, QPushButton#clear_list_btn {{
                background-color: {self.colors['btn_warning']};
                color: {self.colors['text']};
            }}
            QPushButton:hover {{
                border-color: {self.colors['accent']};
            }}
            QProgressBar {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
                padding: 2px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {self.colors['accent']};
                border-radius: 6px;
            }}
            QStatusBar {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border-top: 1px solid rgba(58,226,206,0.3);
            }}
            QTabWidget::pane {{
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 6px;
                background-color: {self.colors['bg_panel']};
            }}
            QTabBar::tab {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                padding: 8px 12px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {self.colors['accent']};
                color: black;
            }}
            QHeaderView {{
                background-color: {self.colors['bg_panel']};
                border: none;
            }}
            QHeaderView::section {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                padding: 4px;
                border: 1px solid rgba(58,226,206,0.3);
            }}
            QHeaderView::section:vertical {{
                border-top: none;
                border-bottom: 1px solid rgba(58,226,206,0.3);
                border-right: 1px solid rgba(58,226,206,0.3);
                border-left: none;
            }}
            QTableCornerButton::section {{
                background-color: {self.colors['bg_panel']};
                border: 1px solid rgba(58,226,206,0.3);
            }}
            QTableWidget {{
                gridline-color: rgba(58,226,206,0.3);
                background-color: {self.colors['bg_panel']};
                selection-background-color: {self.colors['accent']};
                selection-color: black;
            }}
            QTableWidget::item {{
                padding: 5px;
            }}
        """
        )

    # --- VLESS controls ---------------------------------------------------
    # --- VLESS controls ---------------------------------------------------
    def _toggle_vless_btn(self, btn: QPushButton) -> None:
        """Handle VLESS toggle button click."""
        # If button is checked, it means user wants to turn ON (was OFF)
        # If button is unchecked, it means user wants to turn OFF (was ON)
        # However, the connection might fail, so we should only update state on success.
        # But for a toggle button, the state changes immediately on click.
        # So we handle the logic based on the new state.
        
        is_checked = btn.isChecked()
        
        if is_checked:
            # User wants to connect
            url = self.vless_url_input.text().strip()
            if not url:
                self._warn("Сначала вставьте VLESS URL.")
                btn.setChecked(False) # Revert state
                return
                
            self._info("Подключение VLESS...")
            ok = self.vless_manager.start(url)
            if ok:
                port = self.vless_manager.local_socks_port
                self.vless_proxy = f"socks5://127.0.0.1:{port}"
                self.vless_status.setText(f"Статус: подключено (SOCKS5 {port})")
                self._info(f"VLESS подключен на порту {port}")
                self._update_vless_btn_style(btn, True)
            else:
                self.vless_proxy = None
                self.vless_status.setText("Статус: ошибка подключения")
                self._warn("Ошибка подключения VLESS. См. логи.")
                btn.setChecked(False) # Revert state
                self._update_vless_btn_style(btn, False)
        else:
            # User wants to disconnect
            self.vless_manager.stop()
            self.vless_proxy = None
            self.vless_status.setText("Статус: отключено")
            self._info("VLESS отключен")
            self._update_vless_btn_style(btn, False)

    def _update_vless_btn_style(self, btn: QPushButton, is_active: bool) -> None:
        if is_active:
            btn.setText("VLESS: ON")
            btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 11pt;")
        else:
            btn.setText("VLESS: OFF")
            btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")

    def _on_vless_connect(self) -> None:
        # Legacy/Programmatic call support
        if not self.vless_toggle_btn.isChecked():
            self.vless_toggle_btn.setChecked(True)
            self._toggle_vless_btn(self.vless_toggle_btn)

    def _on_vless_disconnect(self) -> None:
        # Legacy/Programmatic call support
        if self.vless_toggle_btn.isChecked():
            self.vless_toggle_btn.setChecked(False)
            self._toggle_vless_btn(self.vless_toggle_btn)

    # --- SRT Handlers ---
    def _on_load_srt(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите .srt файл", str(self.config.output_dir), "SRT Files (*.srt)"
        )
        if not file_path:
            return

        try:
            self.srt_entries = parse_srt_file(file_path)
            self.current_srt_path = Path(file_path)
            
            # Generate marked text
            self.marked_text = generate_marked_text(self.srt_entries)
            self.srt_preview.setPlainText(self.marked_text)
            
            self.edit_markers_btn.setEnabled(True)
            self.generate_srt_btn.setEnabled(True)
            self.srt_status_label.setText(f"Загружено: {Path(file_path).name} ({len(self.srt_entries)} реплик)")
            self._info(f"SRT загружен: {file_path}")
            
        except Exception as e:
            self._error(f"Ошибка загрузки SRT: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить SRT файл:\n{e}")

    def _on_edit_markers(self) -> None:
        if self.srt_preview.isReadOnly():
            self.srt_preview.setReadOnly(False)
            current_size = self.srt_font_spin.value()
            self.srt_preview.setStyleSheet(f"font-family: Calibri; font-size: {current_size}pt; background-color: #2A3B4C;")
            self.edit_markers_btn.setText("💾 Сохранить метки")
            self.generate_srt_btn.setEnabled(False)
            self._info("Режим редактирования меток включен")
        else:
            # Save changes
            self.marked_text = self.srt_preview.toPlainText()
            self.srt_preview.setReadOnly(True)
            current_size = self.srt_font_spin.value()
            self.srt_preview.setStyleSheet(f"font-family: Calibri; font-size: {current_size}pt;")
            self.edit_markers_btn.setText("✏️ Редактировать метки")
            self.generate_srt_btn.setEnabled(True)
            self._info("Метки сохранены")

    def _on_single_font_size_changed(self, value: int) -> None:
        self.text_edit.setStyleSheet(
            f"""
            background-color: {self.colors['bg_panel']};
            color: {self.colors['text']};
            border: 1px solid rgba(58,226,206,0.3);
            border-radius: 6px;
            padding: 6px;
            font-family: Calibri;
            font-size: {value}pt;
            """
        )

    def _on_srt_font_size_changed(self, value: int) -> None:
        self.srt_preview.setStyleSheet(f"font-family: Calibri; font-size: {value}pt;")

    def _on_generate_srt(self) -> None:
        if not self.marked_text or not self.srt_entries:
            return

        # Reload dictionary before SRT generation
        self._reload_dictionary()

        # Update entries from marked text
        try:
            # Validate markers and count
            parsed_data = parse_marked_text(self.marked_text)
            if len(parsed_data) != len(self.srt_entries):
                raise ValueError(
                    f"Количество реплик ({len(parsed_data)}) не совпадает с оригиналом ({len(self.srt_entries)}). "
                    "Не удаляйте и не добавляйте строки, только редактируйте текст."
                )
            updated_entries = self.srt_entries # Use original entries (text will be taken from marked_text)
        except ValueError as e:
            self._error(f"Ошибка парсинга меток: {e}")
            QMessageBox.warning(self, "Ошибка меток", str(e))
            return

        voice_id = self.voice_combo.currentData()
        rate = self.rate_spin.value()
        quality = self.quality_combo.currentData()
        
        # Determine output path
        suggested_name = self.current_srt_path.with_suffix('.mp3').name
        output_path_str, _ = QFileDialog.getSaveFileName(
            self, "Сохранить озвучку", str(self.config.output_dir / suggested_name), "Audio Files (*.mp3)"
        )
        if not output_path_str:
            return
            
        self._info(f"Старт генерации SRT: {len(updated_entries)} реплик")
        
        # Get stress setting
        use_stress = self.auto_stress_cb.isChecked()
        
        # Call worker specifically for SRT
        self._lock_ui(True)
        self._set_status("Генерация SRT...", busy=True)
        
        self.worker.process_srt_request(
            marked_text=self.marked_text,
            entries=updated_entries,
            output_path=output_path_str,
            quality=quality,
            rate=rate,
            voice_id=voice_id,
            use_stress=use_stress
        )

    # --- Gemini Stats Handlers ---
    def _toggle_gemini_btn(self, btn: QPushButton, initial: bool = False) -> None:
        """Handle Gemini toggle button click."""
        is_checked = btn.isChecked()
        
        if is_checked:
            btn.setText("Gemini: ON")
            btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 11pt;")
        else:
            btn.setText("Gemini: OFF")
            btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
            
        if not initial:
            self.config.gemini_enabled = is_checked
            self._info(f"Gemini AI {'включен' if is_checked else 'выключен'}")
        
    def _on_gemini_key_changed(self, text: str) -> None:
        self.config.gemini_api_key = text.strip()
        
    def _on_open_dictionary(self) -> None:
        import os
        if not self.dictionary_path.exists():
            with open(self.dictionary_path, "w", encoding="utf-8") as f:
                f.write("# Словарь замен (формат: слово=замена)\n")
        
        try:
            os.startfile(self.dictionary_path)
        except Exception as e:
            self._warn(f"Не удалось открыть словарь: {e}")
            




    def _on_context_menu(self, pos: QPoint, editor: QTextEdit | QPlainTextEdit) -> None:
        """
        Show context menu with AI Stress Helper.
        """
        menu = editor.createStandardContextMenu()
        menu.addSeparator()
        
        # Get selected text
        cursor = editor.textCursor()
        selected_text = cursor.selectedText().strip()
        
        fix_stress_action = menu.addAction("✨ Исправить ударение (AI)")
        
        if not selected_text:
            fix_stress_action.setEnabled(False)
            fix_stress_action.setToolTip("Выделите слово для исправления")
        elif len(selected_text.split()) > 1:
            fix_stress_action.setEnabled(False)
            fix_stress_action.setToolTip("Выделите только одно слово")
        
        action = menu.exec(editor.mapToGlobal(pos))
        
        if action == fix_stress_action and selected_text:
            self._handle_stress_fix(editor, selected_text)

    def _handle_stress_fix(self, editor: QTextEdit | QPlainTextEdit, word: str) -> None:
        """
        Fetch IPA variants and show selection menu.
        """
        # Show loading indicator (cursor)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            variants = generate_ipa_variants(word)
        except Exception as e:
            self._error(f"Ошибка AI помощника: {e}")
            variants = []
        finally:
            QApplication.restoreOverrideCursor()
            
        if not variants:
            QMessageBox.warning(self, "AI Помощник", f"Не удалось найти варианты ударений для '{word}'.\nПроверьте API ключ Gemini.")
            return
            
        # Show menu with variants
        menu = QMenu(self)
        for ipa_tag, desc in variants:
            # ipa_tag looks like: <phoneme alphabet='ipa' ph='...'>word</phoneme>
            # We want to show: "word (desc)"
            display_text = f"{desc}"
            
            act = menu.addAction(display_text)
            act.setData(ipa_tag)
            
        # Show menu at cursor position
        cursor_pos = editor.mapToGlobal(editor.cursorRect().bottomRight())
        selected_action = menu.exec(cursor_pos)
        
        if selected_action:
            tag = selected_action.data()
            cursor = editor.textCursor()
            cursor.insertText(tag)
            self._info(f"Вставлен IPA тег: {tag}")

    def _on_fix_stress_btn_click(self, editor: QTextEdit | QPlainTextEdit) -> None:
        """Handle 'Fix Stress' button click."""
        cursor = editor.textCursor()
        selected_text = cursor.selectedText().strip()
        
        if not selected_text:
            QMessageBox.warning(self, "Ошибка", "Выделите слово для исправления ударения.")
            return
            
        if len(selected_text.split()) > 1:
            QMessageBox.warning(self, "Ошибка", "Выделите только одно слово.")
            return
            
        self._handle_stress_fix(editor, selected_text)

    def _toggle_ipa_btn(self, btn: QPushButton) -> None:
        """Handle IPA toggle button click."""
        is_checked = btn.isChecked()
        if is_checked:
            btn.setText("IPA: ON")
            btn.setStyleSheet(f"background-color: {self.colors['accent']}; color: black; font-weight: bold; font-size: 11pt;")
        else:
            btn.setText("IPA: OFF")
            btn.setStyleSheet("color: white; font-weight: bold; font-size: 11pt;")
            
        # Sync with checkboxes
        if btn == self.single_ipa_btn:
            self.single_stress_cb.setChecked(is_checked)
        elif btn == self.batch_ipa_btn:
            self.batch_stress_cb.setChecked(is_checked)
        elif btn == self.srt_ipa_btn:
            self.auto_stress_cb.setChecked(is_checked)

    def _collect_settings(self) -> Tuple[str, str, int, str, bool]:
        """Collect current settings from UI."""
        # Determine text source based on active tab
        current_tab_idx = self.tabs.currentIndex()
        text = ""
        
        if current_tab_idx == 0: # Single
            text = self.text_edit.toPlainText().strip()
        elif current_tab_idx == 2: # Batch (was 1)
            # For batch, text is passed separately, but we might need settings
            pass
            
        voice_id = self.voice_combo.currentData()
        rate = self.rate_spin.value()
        quality = self.quality_combo.currentData()
        thinking_mode = self.config.thinking_mode
        
        return text, voice_id, rate, quality, thinking_mode

    def _load_settings(self) -> None:
        try:
            if not self.settings_path.exists():
                return
            with open(self.settings_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return

        geometry_data = data.get("geometry")
        if geometry_data:
            try:
                self.restoreGeometry(base64.b64decode(geometry_data))
            except Exception:
                pass

        vless_url = data.get("vless_url", "").strip()
        if vless_url:
            self.vless_url_input.setText(vless_url)
            self._on_vless_connect()

        gemini_key = data.get("gemini_api_key", "").strip()
        if gemini_key:
            self.gemini_key_input.setText(gemini_key)

        pause_ms = data.get("pause_ms")
        if isinstance(pause_ms, int):
            self.pause_spin.setValue(max(0, min(2000, pause_ms)))

        saved_quality = data.get("output_format")
        if saved_quality:
            index = self.quality_combo.findData(saved_quality)
            if index >= 0:
                self.quality_combo.setCurrentIndex(index)

        saved_text = data.get("text", "")
        if saved_text:
            self.text_edit.setPlainText(saved_text)
            
        # Load batch history
        self.batch_output_history = data.get("batch_output_history", [])
        self._update_output_history_ui()
        
        # Load batch files
        saved_files = data.get("batch_files", [])
        for f in saved_files:
            path = Path(f)
            if path.exists():
                self.batch_files.append(path)
                self.file_list.addItem(path.name)

        # New: Restore Voice and Rate
        saved_voice = data.get("voice_id")
        if saved_voice:
            index = self.voice_combo.findData(saved_voice)
            if index >= 0:
                self.voice_combo.setCurrentIndex(index)
        
        saved_rate = data.get("rate")
        if isinstance(saved_rate, int):
            self.rate_spin.setValue(max(-50, min(50, saved_rate)))

        # Restore SRT font size
        srt_font_size = data.get("srt_font_size")
        if isinstance(srt_font_size, int) and hasattr(self, 'srt_font_spin'):
            self.srt_font_spin.setValue(max(8, min(72, srt_font_size)))

        # Restore Single font size
        single_font_size = data.get("single_font_size")
        if isinstance(single_font_size, int) and hasattr(self, 'single_font_spin'):
            self.single_font_spin.setValue(max(8, min(72, single_font_size)))
            # Force style update
            self._on_single_font_size_changed(self.single_font_spin.value())

        # Restore IPA button states
        if data.get("stress_single", False):
            self.single_ipa_btn.setChecked(True)
            self._toggle_ipa_btn(self.single_ipa_btn)
            
        if data.get("stress_batch", False):
            self.batch_ipa_btn.setChecked(True)
            self._toggle_ipa_btn(self.batch_ipa_btn)

        if data.get("stress_srt", False):
            self.srt_ipa_btn.setChecked(True)
            self._toggle_ipa_btn(self.srt_ipa_btn)

    def _save_settings(self) -> None:
        settings = {}
        try:
            settings["geometry"] = base64.b64encode(self.saveGeometry().data()).decode("utf-8")
            settings["vless_url"] = self.vless_url_input.text().strip()
            settings["gemini_api_key"] = self.gemini_key_input.text().strip()
            settings["pause_ms"] = self.pause_spin.value()
            settings["output_format"] = self.quality_combo.currentData()
            settings["text"] = self.text_edit.toPlainText()
            settings["batch_output_history"] = self.batch_output_history
            settings["batch_files"] = [str(p) for p in self.batch_files]
            
            # New: Save Voice and Rate
            settings["voice_id"] = self.voice_combo.currentData()
            settings["rate"] = self.rate_spin.value()
            
            # Save SRT font size
            if hasattr(self, 'srt_font_spin'):
                settings["srt_font_size"] = self.srt_font_spin.value()

            # Save Single font size
            if hasattr(self, 'single_font_spin'):
                settings["single_font_size"] = self.single_font_spin.value()
            
            # Save IPA button states
            settings["stress_single"] = self.single_ipa_btn.isChecked()
            settings["stress_batch"] = self.batch_ipa_btn.isChecked()
            settings["stress_srt"] = self.srt_ipa_btn.isChecked()

            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save settings: {e}")


    def closeEvent(self, event) -> None:
        """Handle application close event."""
        self._save_settings()
        
        # Stop VLESS if running
        if self.vless_manager.is_running:
            self.vless_manager.stop()
            
        # Stop worker
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1000)
            
        event.accept()


def run_app() -> None:
    config = AppConfig.from_env()
    config.ensure_paths()
    app = QApplication(sys.argv)
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
