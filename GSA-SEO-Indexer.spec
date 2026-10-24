# PyInstaller spec — GSA SEO Indexer onefile GUI build
# Build:  python -m PyInstaller GSA-SEO-Indexer.spec --noconfirm
# Output: dist/GSA-SEO-Indexer.exe

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("gsa_seo_indexer/data", "gsa_seo_indexer/data"),
        ("gsa_seo_indexer/index_service", "gsa_seo_indexer/index_service"),
    ],
    hiddenimports=["gsa_seo_indexer.__main__"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # heavy stdlib pieces the app never touches
        "test",
        "unittest",
        "pydoc",
        "doctest",
        "turtle",
        "matplotlib",
        "numpy",
        "PIL",
        "asyncio",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GSA-SEO-Indexer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
