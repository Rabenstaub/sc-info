@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

REM ---------------------------------------------------------------
REM  Baut das fertige Weitergabe-Paket:
REM    dist\SC Info\          - der Ordner, den die Gilde bekommt
REM    SC-Info-Weitergabe.zip - dasselbe als ZIP fuer den Upload
REM
REM  Einfach doppelklicken, wenn du eine neue Version verteilen willst.
REM  Dauert ein paar Minuten.
REM ---------------------------------------------------------------

echo.
echo   [1/4] Alte Baureste entfernen ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "SC-Info-Weitergabe.zip" del "SC-Info-Weitergabe.zip"

echo   [2/4] Programm bauen (dauert einige Minuten) ...
"venv\Scripts\pyinstaller.exe" sc_info.spec --noconfirm --log-level WARN
if not exist "dist\SC Info\SC Info.exe" (
    echo.
    echo   FEHLER: Die EXE wurde nicht erstellt. Bitte Meldungen oben lesen.
    pause
    exit /b 1
)

echo   [3/4] Paket aufraeumen ...
REM Videobibliothek wird nicht gebraucht - spart 29 MB
if exist "dist\SC Info\_internal\cv2\opencv_videoio_ffmpeg500_64.dll" (
    del "dist\SC Info\_internal\cv2\opencv_videoio_ffmpeg*.dll"
)
REM Anleitung beilegen
copy /y "LIESMICH-fuer-Weitergabe.txt" "dist\SC Info\LIESMICH - Bitte zuerst lesen.txt" >nul
REM Sicherheit: eigene Einstellungen und Messungen duerfen NICHT mitgehen
if exist "dist\SC Info\einstellungen.json" del "dist\SC Info\einstellungen.json"
if exist "dist\SC Info\verlauf.json" del "dist\SC Info\verlauf.json"
if exist "dist\SC Info\letzter_screenshot.png" del "dist\SC Info\letzter_screenshot.png"
if exist "dist\SC Info\sc_info.log" del "dist\SC Info\sc_info.log"

echo   [4/4] ZIP fuer den Upload packen ...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SC Info' -DestinationPath 'SC-Info-Weitergabe.zip' -CompressionLevel Optimal -Force"

echo.
echo   Fertig.
echo     Ordner : dist\SC Info\
echo     ZIP    : SC-Info-Weitergabe.zip   (diese Datei hochladen)
echo.
pause
