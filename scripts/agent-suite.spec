# -*- mode: python ; coding: utf-8 -*-


from pathlib import Path


repo_root = Path.cwd()
src_root = repo_root / "src"

a = Analysis(
    [
        str(src_root / "cloud_av_agent_lab" / "guest_agent_server" / "main.py"),
        str(src_root / "cloud_av_agent_lab" / "desktop_worker" / "main.py"),
    ],
    pathex=[str(src_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

guest_agent = EXE(
    pyz,
    a.scripts[0],
    [],
    exclude_binaries=True,
    name="guest-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

desktop_worker = EXE(
    pyz,
    a.scripts[1],
    [],
    exclude_binaries=True,
    name="desktop-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    guest_agent,
    desktop_worker,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="bin",
)
