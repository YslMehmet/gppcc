# PyInstaller spec for gppcc.
#   macOS:   ./build_macos.sh        -> dist/gppcc.app (menu-bar app)
#   Windows: .\build_windows.ps1     -> dist\gppcc.exe (single-file, console window)
import os
import re
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
with open(os.path.join(ROOT, "gppcc", "__init__.py"), encoding="utf-8") as _f:
    __version__ = re.search(r'__version__ = "([^"]+)"', _f.read()).group(1)

hiddenimports = ["pygame._sdl2.controller"]
if IS_MAC:
    hiddenimports += [
        "gppcc.macmenu",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "HIServices",
        "Quartz",
        "AppKit",
    ]
elif IS_WIN:
    hiddenimports += [
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pynput._util.win32",
    ]

excludes = ["unittest", "pydoc", "numpy"]
if IS_MAC:
    # The keyboard overlay (tkinter + Pillow) is Windows-only; Mac doesn't need it.
    excludes += ["tkinter", "PIL"]
else:
    # Mac-only modules are useless (and unimportable) off macOS.
    excludes += ["gppcc.macmenu", "gppcc.machid"]
if IS_WIN:
    hiddenimports += ["gppcc.overlay", "gppcc.overlay_render", "PIL.Image",
                      "PIL.ImageDraw", "PIL.ImageFont", "PIL.ImageFilter", "PIL.ImageChops"]

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "gppcc", "web"), "gppcc/web")],
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="gppcc",
        console=False,
        argv_emulation=False,
        codesign_identity=os.environ.get("GPPCC_SIGN_IDENTITY") or None,
    )

    coll = COLLECT(exe, a.binaries, a.datas, name="gppcc")

    app = BUNDLE(
        coll,
        name="gppcc.app",
        icon=os.path.join(ROOT, "build", "gppcc.icns"),
        bundle_identifier="com.gppcc.app",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "gppcc",
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,              # menu-bar app, no Dock icon
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "gppcc — Gamepad PC Controller",
        },
    )
else:
    # Windows has no tray icon yet, so keep the console: it shows the UI URL and
    # Ctrl+C (or closing the window) quits.
    icon = os.path.join(ROOT, "build", "gppcc.ico")
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="gppcc",
        console=True,
        upx=False,
        icon=icon if os.path.exists(icon) else None,
    )
