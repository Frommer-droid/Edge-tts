# -*- coding: utf-8 -*-
"""GUI-сборщик для Edge-TTS Desktop (PyInstaller)."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class CompilerThread(QThread):
    new_log_line = Signal(str)
    finished_compilation = Signal(bool, str)

    def __init__(self, command, workdir):
        super().__init__()
        self.command = command
        self.workdir = workdir

    def run(self):
        try:
            process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=self.workdir,
            )
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                self.new_log_line.emit(line.rstrip())

            process.wait()
            if process.returncode == 0:
                self.finished_compilation.emit(True, "Сборка завершена успешно.")
            else:
                self.finished_compilation.emit(
                    False, f"Ошибка сборки (код {process.returncode})."
                )
        except FileNotFoundError:
            self.finished_compilation.emit(
                False,
                "PyInstaller не найден. Убедитесь в установке и наличии в PATH.",
            )
        except Exception as exc:
            self.finished_compilation.emit(False, f"Неожиданная ошибка: {exc}")


def _read_version(project_root: Path) -> str:
    version_file = project_root / "VERSION"
    if version_file.exists():
        text = version_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return "0.0.0"


class PyCompilerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project_root = Path(__file__).resolve().parent.parent
        self.settings_path = os.path.join(
            os.path.dirname(__file__), "PyCompiler_settings.json"
        )
        self.thread = None
        self.colors = {
            "bg_main": "#17212B",
            "bg_panel": "#0E1621",
            "accent": "#3AE2CE",
            "button_primary": "#4B82E5",
            "button_warning": "#BF8255",
            "button_action": "#6AF1E2",
            "text": "#FFFFFF",
        }

        self._setup_window()
        self._create_widgets()
        self._connect_signals()
        self._apply_styles()
        self._load_settings()

    def _setup_window(self):
        version = _read_version(self.project_root)
        self.setWindowTitle(f"Edge-TTS Desktop - Compiler v{version}")
        self.setWindowIcon(QIcon(resource_path("logo.ico")))
        self.resize(780, 520)
        self.setMinimumSize(360, 260)
        status = QStatusBar(self)
        self.setStatusBar(status)
        status.showMessage("Готово к сборке")

    def _create_widgets(self):
        central = QWidget()
        central.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root_layout = QVBoxLayout(central)
        root_layout.setSpacing(16)

        spec_label = QLabel("Spec-файл для сборки:")
        self.spec_combo = QComboBox()
        self.spec_combo.setEditable(True)
        self.spec_combo.setPlaceholderText("D:/path/to/Edge_TTS_Desktop.spec")
        browse_btn = QPushButton("Выбрать")
        browse_btn.setFixedWidth(120)
        browse_btn.clicked.connect(self._browse_spec_file)

        spec_row = QHBoxLayout()
        spec_row.addWidget(self.spec_combo, stretch=1)
        spec_row.addWidget(browse_btn)

        controls_row = QHBoxLayout()
        self.build_btn = QPushButton("Собрать")
        self.build_btn.setObjectName("build_btn")
        self.clear_log_btn = QPushButton("Очистить лог")
        self.clear_log_btn.setObjectName("clear_log_btn")
        controls_row.addWidget(self.build_btn)
        controls_row.addWidget(self.clear_log_btn)
        controls_row.addStretch(1)

        log_label = QLabel("Лог PyInstaller:")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Здесь появится вывод PyInstaller...")

        root_layout.addWidget(spec_label)
        root_layout.addLayout(spec_row)
        root_layout.addLayout(controls_row)
        root_layout.addWidget(log_label)
        root_layout.addWidget(self.log_output, stretch=1)

        self.setCentralWidget(central)
        self.centralWidget().setObjectName("root_widget")

    def _connect_signals(self):
        self.build_btn.clicked.connect(self._start_compilation)
        self.clear_log_btn.clicked.connect(self.log_output.clear)

    def _apply_styles(self):
        self.setStyleSheet(
            f"""
            QWidget#root_widget {{
                background-color: {self.colors['bg_main']};
                font-family: "Aptos", "Segoe UI", sans-serif;
                font-size: 15pt;
                color: {self.colors['text']};
            }}
            QLabel {{
                color: {self.colors['accent']};
                font-weight: 600;
            }}
            QTextEdit {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 8px;
                padding: 8px;
            }}
            QComboBox {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
                border: 1px solid rgba(58,226,206,0.3);
                border-radius: 8px;
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
                padding: 10px 18px;
                font-size: 15pt;
                color: {self.colors['text']};
                background-color: {self.colors['button_primary']};
            }}
            QPushButton#build_btn {{
                background-color: {self.colors['button_action']};
                color: black;
                font-weight: 700;
            }}
            QPushButton#clear_log_btn {{
                background-color: {self.colors['button_warning']};
            }}
            QPushButton:hover {{
                border-color: {self.colors['accent']};
            }}
            QStatusBar {{
                background-color: {self.colors['bg_panel']};
                color: {self.colors['text']};
            }}
        """
        )

    def _append_log(self, message):
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()

    def _browse_spec_file(self):
        current_text = self.spec_combo.currentText().strip()
        start_dir = os.path.dirname(current_text) if current_text else "."
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выбор spec-файла", start_dir, "Spec Files (*.spec)"
        )
        if file_path:
            self._add_to_history(file_path)

    def _start_compilation(self):
        spec_file = self.spec_combo.currentText().strip()
        if not spec_file or not os.path.exists(spec_file):
            QMessageBox.warning(self, "Ошибка", "Укажите путь к .spec файлу.")
            return

        spec_dir = os.path.abspath(os.path.dirname(spec_file) or ".")
        command = [sys.executable, "-m", "PyInstaller", spec_file]
        self._add_to_history(spec_file)
        self._append_log(f">>> Старт сборки: {spec_file}")
        self.statusBar().showMessage("Сборка...")
        self.build_btn.setEnabled(False)

        self.thread = CompilerThread(command, spec_dir)
        self.thread.new_log_line.connect(self._append_log)
        self.thread.finished_compilation.connect(self._on_compilation_finished)
        self.thread.start()

    def _on_compilation_finished(self, success, message):
        self.build_btn.setEnabled(True)
        self.statusBar().showMessage(message)
        self._append_log(message)

        spec_file = self.spec_combo.currentText().strip()
        if success and spec_file and os.path.exists(spec_file):
            self._run_post_build(spec_file)

    def _run_post_build(self, spec_file):
        spec_dir = os.path.dirname(os.path.abspath(spec_file))
        script_path = os.path.join(spec_dir, "post_build.py")
        if not os.path.exists(script_path):
            return

        self._append_log(">>> post_build.py вывод:")
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                cwd=spec_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            if result.stdout:
                self._append_log(result.stdout.strip())
            if result.returncode == 0:
                self._append_log("post_build.py выполнен успешно.")
            else:
                self._append_log(
                    f"post_build.py завершился с ошибкой (код {result.returncode})."
                )
        except Exception as exc:
            self._append_log(f"Не удалось выполнить post_build.py: {exc}")

    def _add_to_history(self, path):
        existing = [self.spec_combo.itemText(i) for i in range(self.spec_combo.count())]
        if path not in existing:
            self.spec_combo.insertItem(0, path)
        self.spec_combo.setCurrentText(path)
        self._save_settings()

    def _load_settings(self):
        if not os.path.exists(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            history = data.get("spec_file_history", [])
            last_index = data.get("last_spec_index", 0)
            last_path = (data.get("last_spec_path") or "").strip()
            geo = data.get("window_geometry") or {}
            was_maximized = bool(data.get("window_maximized", False))
            for entry in history:
                self.spec_combo.addItem(entry)
            if last_path:
                if last_path not in history:
                    self.spec_combo.insertItem(0, last_path)
                self.spec_combo.setCurrentText(last_path)
            elif 0 <= last_index < len(history):
                self.spec_combo.setCurrentIndex(last_index)
            self._restore_geometry(geo, was_maximized)
        except Exception as exc:
            self._append_log(f"Не удалось загрузить настройки: {exc}")

    def _save_settings(self):
        history = [self.spec_combo.itemText(i) for i in range(self.spec_combo.count())]
        g = self.geometry()
        data = {
            "spec_file_history": history,
            "last_spec_index": self.spec_combo.currentIndex(),
            "last_spec_path": self.spec_combo.currentText().strip(),
            "window_geometry": {
                "x": g.x(),
                "y": g.y(),
                "w": g.width(),
                "h": g.height(),
            },
            "window_maximized": self.isMaximized(),
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._append_log(f"Не удалось сохранить настройки: {exc}")

    def _restore_geometry(self, geo: dict, was_maximized: bool):
        try:
            x = int(geo.get("x", 0))
            y = int(geo.get("y", 0))
            w = int(geo.get("w", 0))
            h = int(geo.get("h", 0))
            if w > 100 and h > 100:
                self.setGeometry(x, y, w, h)
            if was_maximized:
                self.showMaximized()
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "edge.tts.desktop.compiler"
            )
        except Exception:
            pass
    window = PyCompilerApp()
    window.show()
    sys.exit(app.exec())
