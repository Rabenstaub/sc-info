# -*- mode: python ; coding: utf-8 -*-
"""
Bauanleitung fuer die fertige SC-Info.exe (PyInstaller).

Bauen mit:   venv\\Scripts\\pyinstaller.exe sc_info.spec --noconfirm
Ergebnis:    dist\\SC Info\\SC Info.exe  (kompletter Ordner weitergeben)
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Texterkennung: Modelldateien (.onnx) und config.yaml muessen mit ins Paket,
# sonst startet das Programm zwar, findet aber keine Erkennungsmodelle.
for paket in ("rapidocr_onnxruntime", "onnxruntime"):
    d, b, h = collect_all(paket)
    datas += d
    binaries += b
    hiddenimports += h

# Grosse Qt-Bausteine, die SC Info nicht braucht - spart mehrere hundert MB.
AUSSCHLUSS = [
    "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineQuick",
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtQuickWidgets",
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtBluetooth",
    "PyQt6.QtCharts", "PyQt6.QtDataVisualization", "PyQt6.QtNetworkAuth",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtPositioning", "PyQt6.QtSql",
    "PyQt6.QtTest", "PyQt6.QtDesigner", "PyQt6.QtHelp", "PyQt6.QtOpenGL",
    "PyQt6.QtOpenGLWidgets", "PyQt6.QtSerialPort", "PyQt6.QtSpatialAudio",
    "PyQt6.QtSvgWidgets", "PyQt6.QtTextToSpeech", "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets", "PyQt6.QtNfc", "PyQt6.QtRemoteObjects",
    "PyQt6.QtSensors", "PyQt6.Qt3DCore", "PyQt6.Qt3DRender",
    "matplotlib", "tkinter", "scipy", "pandas", "IPython", "notebook",
    "torch", "torchvision", "tensorflow", "sklearn", "sympy",
]

a = Analysis(
    ["sc_info.pyw"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=AUSSCHLUSS,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SC Info",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX-Packen loest zusaetzliche Virenscanner-Alarme aus
    console=False,             # kein schwarzes Konsolenfenster
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="sc_info.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SC Info",
)
