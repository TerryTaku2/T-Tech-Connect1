@echo off
echo === T-Tech Connect — Setup & Run ===

cd /d "%~dp0Backend"

echo.
echo [1/3] Checking Python...
python --version 2>nul || (echo Python not found. Install from python.org && pause && exit /b 1)

echo [2/3] Installing dependencies...
pip install -r requirements.txt --quiet

echo [3/3] Starting server...
echo.
echo  App running at: http://127.0.0.1:5000
echo  Press Ctrl+C to stop.
echo.
python app.py
pause
