@echo off
echo Starting Grok Register + 9Router Auto-Import...
echo.

REM Start auto-push watcher in background
start "9Router Auto-Import" /MIN python "%~dp0auto_import_9router.py" --watch

REM Run registration
python "%~dp0grok_register_ttk.py" %*

REM Kill watcher when registration ends
taskkill /FI "WINDOWTITLE eq 9Router Auto-Import" /F >nul 2>&1
echo.
echo Done.
