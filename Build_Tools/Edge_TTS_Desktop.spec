# -*- coding: utf-8 -*-
import os
import sys
import certifi

from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

# Collect gRPC data (including roots.pem)
grpc_datas = collect_data_files('grpc')

# Collect all google.genai resources
genai_datas, genai_binaries, genai_hiddenimports = collect_all('google.genai')

# Combine hidden imports
my_hiddenimports = [
    'edge_tts',
    'vless',
    'google.genai',
    'google.generativeai',
    'grpc',
    'grpc._cython.cygrpc',
    'anyio',
    'h11',
    'httpcore',
    'pydantic.deprecated.decorator'
] + genai_hiddenimports

# Combine datas
my_datas = grpc_datas + genai_datas
spec_path = os.path.abspath(sys.argv[0])
spec_dir = os.path.dirname(spec_path)
project_root = os.path.abspath(os.path.join(spec_dir, '..'))
script_path = os.path.join(project_root, 'main.py')


added_files = [
    (os.path.join(project_root, 'xray.exe'), '.'),
    (os.path.join(project_root, '.env.example'), '.'),
    (os.path.join(project_root, 'README.md'), '.'),
    (os.path.join(project_root, 'logo.ico'), '.'),
    (os.path.join(project_root, 'libs'), 'libs'),
]

print("=" * 60)
print("Files to include:")
for src, dst in added_files:
    if os.path.exists(src):
        print(f"  [OK] {os.path.basename(src)}")
    else:
        print(f"  [MISSING] {os.path.basename(src)}")
print("=" * 60)

added_files = [(src, dst) for src, dst in added_files if os.path.exists(src)]

certifi_path = os.path.join(os.path.dirname(certifi.__file__), 'cacert.pem')
if os.path.exists(certifi_path):
    added_files.append((certifi_path, 'certifi'))
    print("[OK] Added certifi certificate\n")

a = Analysis(
    [script_path],
    pathex=[project_root, os.path.join(project_root, 'libs')],
    binaries=genai_binaries,
    datas=my_datas + added_files,
    hiddenimports=my_hiddenimports + [
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'psutil',
        'edge_tts',
        'python_dotenv',
        'vless_manager',
        'google.genai',
        'aiohttp',
    ],
    hookspath=[spec_dir],
    runtime_hooks=[],
    excludes=[
        'onnxruntime.providers.cuda', 'onnxruntime.providers.tensorrt',
        'onnxruntime.providers.dml', 'matplotlib', 'PIL',
        'tkinter', 'torch', 'tensorflow',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

certifi_dir = os.path.dirname(certifi.__file__)
for root, dirs, files in os.walk(certifi_dir):
    for file in files:
        if file.endswith(('.pem', '.txt')):
            file_path = os.path.join(root, file)
            target_path = os.path.join('certifi', os.path.relpath(file_path, certifi_dir))
            a.datas.append((target_path, file_path, 'DATA'))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Edge_TTS_Desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, upx=True, console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'logo.ico') if os.path.exists(os.path.join(project_root, 'logo.ico')) else None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name='Edge_TTS_Desktop',
)
