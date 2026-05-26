@echo off
title UI Preview Server
pushd C:\chat_code

echo.
echo ===========================================
echo   UI Preview Server - port 8000
echo ===========================================
echo.
echo   Root: %CD%
echo.
echo   Quick entries:
echo     http://localhost:8000/code_1/design/ui_white_index.html
echo     http://localhost:8000/code_1/design/ui_glossary.html
echo.
echo   Any .html under C:\chat_code is accessible via:
echo     http://localhost:8000/^<relative path^>
echo.
echo   Stop: Ctrl+C  or  close this window
echo ===========================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000/code_1/design/ui_white_index.html"

python -m http.server 8000
