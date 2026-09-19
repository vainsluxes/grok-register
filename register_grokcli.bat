@echo off
echo ========================================
echo  Grok Register + Auto Import to 9Router
echo ========================================
echo.
echo Starting auto-import watcher (grok-cli)...
start "9Router Grok-CLI Import" /MIN python "%~dp0auto_import_grok_cli.py" --watch
echo.
echo Starting Grok Register...
python "%~dp0grok_register_ttk.py" %*
echo.
echo Stopping watcher...
taskkill /FI "WINDOWTITLE eq 9Router Grok-CLI Import" /F >nul 2>&1
echo Done.
