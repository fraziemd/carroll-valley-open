"""JDCVO scoring system - 2026 rebuild.

A config-driven engine for the 'Jimmy D' Carroll Valley Open:
- scoring.py: pure scoring functions (ported faithfully from the 2025 notebook)
- playthru.py: requests-based scraper for PlayThru leaderboards (no Selenium)
- config.py: event/player/course configuration loading
- store.py: storage backends (local JSON for dev/tests, Google Sheets for production)
- pipeline.py: scrape -> apply corrections -> score -> publish
"""
