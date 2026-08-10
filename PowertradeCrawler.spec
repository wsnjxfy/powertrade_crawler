# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


rag_model_path = Path('work/rag-model')
rag_manifest = rag_model_path / 'powertrade-rag-model-manifest.json'
if not rag_manifest.is_file():
    raise SystemExit(
        'RAG model is missing. Run: '
        '.\\.venv\\Scripts\\python.exe scripts\\prepare_rag_model.py'
    )

fastembed_datas, fastembed_binaries, fastembed_hiddenimports = collect_all('fastembed')
tokenizers_datas, tokenizers_binaries, tokenizers_hiddenimports = collect_all('tokenizers')
onnxruntime_binaries = collect_dynamic_libs('onnxruntime')
system32 = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
vc_runtime_names = (
    'msvcp140.dll',
    'msvcp140_1.dll',
    'vcruntime140.dll',
    'vcruntime140_1.dll',
)
vc_runtime_binaries = []
for runtime_name in vc_runtime_names:
    runtime_path = system32 / runtime_name
    if not runtime_path.is_file():
        raise SystemExit(f'Required Visual C++ runtime is missing: {runtime_path}')
    vc_runtime_binaries.append((str(runtime_path), '.'))


a = Analysis(
    ['desktop_launcher.py'],
    pathex=['src'],
    binaries=(
        vc_runtime_binaries
        + fastembed_binaries
        + tokenizers_binaries
        + onnxruntime_binaries
    ),
    datas=[
        ('configs', 'configs'),
        ('src/powertrade_crawler/agent/data', 'powertrade_crawler/agent/data'),
        ('src/powertrade_crawler/market_agent/data', 'powertrade_crawler/market_agent/data'),
        ('resources/initial', 'initial_data'),
        ('work/rag-model', 'rag_model'),
        ('docs/THIRD_PARTY_NOTICES_RAG.md', '.'),
        ('.env.example', '.'),
        ('EXE_README.txt', '.'),
    ] + fastembed_datas + tokenizers_datas,
    hiddenimports=[
        'powertrade_crawler.gui',
        'powertrade_crawler.agent.gui',
        'powertrade_crawler.agent.evaluation',
        'matplotlib.backends.backend_tkagg',
    ] + fastembed_hiddenimports + tokenizers_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PowertradeCrawler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PowertradeCrawler',
)
