"""Entry point for the Edge-TTS desktop application."""
import sys
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def setup_frozen_environment():
    """
    Configures environment variables for correct gRPC operation
    in a frozen PyInstaller environment.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller unpacks data to sys._MEIPASS
        base_dir = sys._MEIPASS
        
        # 1. SSL Certificate Discovery
        # gRPC looks for roots.pem. In a frozen env, the path changes.
        possible_paths = [
            os.path.join(base_dir, 'grpc', '_cython', '_credentials', 'roots.pem'),
            os.path.join(base_dir, 'grpc', 'roots.pem'),
            os.path.join(base_dir, 'roots.pem')
        ]
        
        ca_path = None
        for path in possible_paths:
            if os.path.exists(path):
                ca_path = path
                break
        
        if ca_path:
            # Force gRPC to use the found certificate
            os.environ['GRPC_DEFAULT_SSL_ROOTS_FILE_PATH'] = ca_path
            os.environ['SSL_CERT_FILE'] = ca_path
            os.environ['REQUESTS_CA_BUNDLE'] = ca_path
        
        # 2. Fix Windows Polling Strategy
        # Prevents deadlocks when restarting loops by avoiding IOCP race conditions.
        # 'poll' is less efficient but stable for desktop apps.
        os.environ['GRPC_POLL_STRATEGY'] = 'poll'

# Apply environment fixes immediately
# Apply environment fixes immediately
setup_frozen_environment()

# Apply edge-tts patch for raw SSML support
try:
    from app.edge_tts_patch import apply_patch
    apply_patch()
except ImportError:
    pass  # Patch might not be available yet or not needed
except Exception as e:
    print(f"Failed to apply edge-tts patch: {e}")

from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow
from app.config import load_config
from app.main_window import run_app


if __name__ == "__main__":
    run_app()
