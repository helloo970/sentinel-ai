# PyInstaller spec for the Tauri sidecar (a single .exe without a console)
# Usage: python -m PyInstaller --noconfirm --clean sentinel-backend.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("sentinel") + collect_submodules("uvicorn") + collect_submodules("edge_tts")
)

a = Analysis(  # noqa: F821
    ["src/sentinel/__main__.py"],
    pathex=["src"],
    hiddenimports=hidden,
    # Model files the packages load at runtime (e.g. Silero VAD from faster-whisper)
    datas=collect_data_files("faster_whisper") + collect_data_files("openwakeword"),
    excludes=["tkinter", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="sentinel-backend",
    console=False,
    upx=False,
    strip=False,
)
