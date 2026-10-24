@echo off
cd /d "%~dp0"
python -m gsa_seo_indexer.cli %*
