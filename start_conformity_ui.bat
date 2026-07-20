@echo off
REM ─────────────────────────────────────────────────────────────────
REM  LEON — Conformity Matrix Analyzer
REM  Starts the web interface and prints the address to share with
REM  colleagues on the same network.
REM ─────────────────────────────────────────────────────────────────
cd /d "%~dp0"

echo.
echo  LEON - Conformity Matrix Analyzer
echo  =================================
echo.
echo  Your address:        http://localhost:8012/
echo.
echo  Share with colleagues on the same network (pick your IPv4 below):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo    http://%%a:8012/  (remove spaces)
echo.
echo  If colleagues cannot connect, allow Python through the Windows
echo  Firewall when prompted (or ask IT to open inbound TCP 8012).
echo.
echo  Press Ctrl+C to stop the server.
echo.

".venv\Scripts\python.exe" -m uvicorn app.conformity_server:app --host 0.0.0.0 --port 8012
if errorlevel 1 python -m uvicorn app.conformity_server:app --host 0.0.0.0 --port 8012
pause
