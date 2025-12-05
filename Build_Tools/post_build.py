# -*- coding: utf-8 -*-
"""
POST-BUILD CLEANUP SCRIPT
Копирует ключевые файлы и убирает временные директории для Edge_TTS_Desktop.
"""

import os
import shutil


def safe_copy(src: str, dst: str, label: str) -> None:
    if os.path.exists(src) and os.path.exists(os.path.dirname(dst)):
        try:
            shutil.copy2(src, dst)
            print(f"[OK] Copied {label}")
        except Exception as e:
            print(f"[ERROR] Failed to copy {label}: {e}")
    else:
        print(f"[SKIP] {label} or destination not found")


def main() -> None:
    print("\n" + "=" * 60)
    print("POST-BUILD CLEANUP")
    print("=" * 60)

    script_dir = os.path.abspath(os.path.dirname(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    dist_app_dir = os.path.join(script_dir, "dist", "Edge_TTS_Desktop")
    final_app_dir = os.path.join(project_root, "Edge_TTS_Desktop")

    # ========== 1. Копируем .env.example ==========
    safe_copy(
        os.path.join(project_root, ".env.example"),
        os.path.join(dist_app_dir, "_internal", ".env.example"),
        ".env.example",
    )

    # ========== 2. Переносим собранное приложение ==========
    if os.path.exists(dist_app_dir):
        try:
            if os.path.exists(final_app_dir):
                shutil.rmtree(final_app_dir)
                print(f"[OK] Removed old Edge_TTS_Desktop/")
            shutil.move(dist_app_dir, final_app_dir)
            print(f"[OK] Moved to: {final_app_dir}")
        except Exception as e:
            print(f"[ERROR] Failed to move: {e}")
    else:
        print("[ERROR] dist/Edge_TTS_Desktop not found!")

    # ========== 3. Удаляем временные директории ==========
    print("\n[CLEANUP] Removing temporary directories...")
    temp_folders = [
        os.path.join(script_dir, "build"),
        os.path.join(script_dir, "dist"),
        os.path.join(script_dir, "__pycache__"),
        os.path.join(project_root, "dist"),
        os.path.join(project_root, "build"),
        os.path.join(project_root, "__pycache__"),
        os.path.join(final_app_dir, "__pycache__"),
    ]

    for folder_path in temp_folders:
        if folder_path and os.path.exists(folder_path):
            try:
                shutil.rmtree(folder_path)
                print(f"[OK] Removed {folder_path}/")
            except Exception as e:
                print(f"[ERROR] Failed to remove {folder_path}/: {e}")

    # ========== 4. Копируем edge_tts_settings.json ==========
    settings_src = os.path.join(project_root, "edge_tts_settings.json")
    settings_dst = os.path.join(final_app_dir, "edge_tts_settings.json")
    safe_copy(settings_src, settings_dst, "edge_tts_settings.json")

    # ========== 5. Копируем custom_dictionary.txt ==========
    dict_src = os.path.join(project_root, "custom_dictionary.txt")
    dict_dst = os.path.join(final_app_dir, "custom_dictionary.txt")
    safe_copy(dict_src, dict_dst, "custom_dictionary.txt")

    # ========== 6. Копируем gemini_triggers.txt ==========
    triggers_src = os.path.join(project_root, "gemini_triggers.txt")
    triggers_dst = os.path.join(final_app_dir, "gemini_triggers.txt")
    safe_copy(triggers_src, triggers_dst, "gemini_triggers.txt")

    print("\n" + "=" * 60)
    print(f"DONE! App: {final_app_dir}")
    print("=" * 60)

    # ========== 6. Запускаем приложение ==========
    exe_path = os.path.join(final_app_dir, "Edge_TTS_Desktop.exe")
    if os.path.exists(exe_path):
        print(f"\n[EXEC] Launching {exe_path}...")
        try:
            os.startfile(exe_path)
        except Exception as e:
            print(f"[ERROR] Failed to launch exe: {e}")
    else:
        print(f"[ERROR] Executable not found: {exe_path}")


if __name__ == "__main__":
    main()
