# -*- mode: python ; coding: utf-8 -*-


import sys
from pathlib import Path


repo_root = Path.cwd()
src_root = repo_root / "src"
guest_agent_script = src_root / "cloud_av_agent_lab" / "guest_agent_server" / "main.py"
desktop_worker_script = src_root / "cloud_av_agent_lab" / "desktop_worker" / "main.py"
conda_runtime_dlls = (
    "ffi.dll",
    "libbz2.dll",
    "libcrypto-3-x64.dll",
    "libexpat.dll",
    "liblzma.dll",
    "libssl-3-x64.dll",
    "sqlite3.dll",
)


def conda_runtime_binaries():
    library_bin = Path(sys.prefix) / "Library" / "bin"
    return [
        (str(library_bin / dll_name), ".")
        for dll_name in conda_runtime_dlls
        if (library_bin / dll_name).exists()
    ]

a = Analysis(
    [
        str(guest_agent_script),
        str(desktop_worker_script),
    ],
    pathex=[str(src_root)],
    binaries=conda_runtime_binaries(),
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


def runtime_hook_scripts():
    return [script for script in a.scripts if "pyi_rth_" in script[0]]


def scripts_for_entrypoint(entrypoint, name):
    selected = []
    selected.extend(runtime_hook_scripts())
    selected.append((name, str(entrypoint), "PYSOURCE"))
    return selected

guest_agent = EXE(
    pyz,
    scripts_for_entrypoint(guest_agent_script, "guest_agent_main"),
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
    scripts_for_entrypoint(desktop_worker_script, "desktop_worker_main"),
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
