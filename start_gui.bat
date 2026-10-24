@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw -m gsa_seo_indexer & exit /b)
start "" python -m gsa_seo_indexer
