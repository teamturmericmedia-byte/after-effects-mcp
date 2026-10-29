@echo off
rem After Effects MCP for Claude - one-click setup (Windows).
rem Installs the Python helpers and registers the server with Claude Code for this Windows user.
setlocal
set HERE=%~dp0
echo.
echo === After Effects MCP setup ===

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python not found. Install Python 3.10+ from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause & exit /b 1
)

echo [1/3] Installing Python helpers (Pillow, numpy, OpenCV)...
python -m pip install --user -r "%HERE%requirements.txt"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [i] ffmpeg not found - optional, needed for ae_compare with a video reference and render contact sheets.
  echo     Install with:  winget install Gyan.FFmpeg
)

echo [2/3] Registering with Claude Code...
where claude >nul 2>nul
if errorlevel 1 (
  echo [i] The "claude" command is not on PATH. Add the server manually - see README.md "Setup for a new PC".
) else (
  claude mcp add after-effects --scope user -- python "%HERE%server.py"
)

echo [3/3] In After Effects: Edit ^> Preferences ^> Scripting ^& Expressions ^> tick
echo       "Allow Scripts to Write Files and Access Network", then restart After Effects.
echo.
echo Done. Open After Effects, start a new Claude session and ask: "check After Effects".
pause
