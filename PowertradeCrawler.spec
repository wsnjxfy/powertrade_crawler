# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_launcher.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('configs', 'configs'),
        ('src/powertrade_crawler/agent/data', 'powertrade_crawler/agent/data'),
        ('src/powertrade_crawler/market_agent/data', 'powertrade_crawler/market_agent/data'),
        ('resources/initial', 'initial_data'),
        ('.env.example', '.'),
        ('EXE_README.txt', '.'),
    ],
    hiddenimports=[
        'powertrade_crawler.gui',
        'powertrade_crawler.agent.gui',
        'powertrade_crawler.agent.evaluation',
        'matplotlib.backends.backend_tkagg',
    ],
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
