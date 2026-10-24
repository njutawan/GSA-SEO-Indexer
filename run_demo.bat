@echo off
cd /d "%~dp0"
python -m gsa_seo_indexer.cli submit --url http://example.com/ --sites 100 --threads 20 --with-link-type --report demo.csv
