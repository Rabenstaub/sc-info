@echo off
setlocal
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM SC Info starten - legt beim ersten Start automatisch eine
REM virtuelle Umgebung an und installiert die Abhaengigkeiten.
REM ---------------------------------------------------------------

if not exist "venv\Scripts\pythonw.exe" (
    echo Erste Einrichtung - virtuelle Umgebung wird erstellt...
    py -3.11 -m venv venv 2>nul || py -3 -m venv venv || python -m venv venv
    if not exist "venv\Scripts\python.exe" (
        echo FEHLER: Python wurde nicht gefunden. Bitte Python 3.11 installieren.
        pause
        exit /b 1
    )
    echo Abhaengigkeiten werden installiert, das dauert einen Moment...
    "venv\Scripts\python.exe" -m pip install --upgrade pip
    "venv\Scripts\python.exe" -m pip install PyQt6 rapidocr_onnxruntime mss pillow
)

start "" "venv\Scripts\pythonw.exe" sc_info.pyw
endlocal
