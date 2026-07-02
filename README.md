# SpaceWeekly

SpaceWeekly is a desktop aerospace news workflow for collecting RSS feeds,
parsing article pages, generating AI summaries/translations, saving raw
articles to SQLite, and exporting weekly reports.

Current version: v1.5 AI Translation Edition

## Desktop GUI

Start the desktop app:

```bash
python app.py
```

The GUI is built with PySide6 and calls only one business entry point:

```python
generate_weekly()
```

The interface supports:

- RSS feed management
- SQLite, Markdown, and Word output options
- AI summary, translation, keywords, category, and importance switches
- Body output modes: original, bilingual, translated
- Date filtering: today, last 3 days, last 7 days, last 30 days, custom
- Token and estimated time display
- Open output directory
- Real-time colored logging

## Feed Manager

RSS sources are stored in `feeds.json`, not SQLite.

```json
[
  {
    "name": "NASA Science",
    "rss": "https://science.nasa.gov/feed/",
    "enabled": true
  }
]
```

Parser selection is automatic through `parsers.registry`.

## User Settings

User settings are stored in `config.json`. `config.py` remains the code default
configuration.

```json
{
  "deepseek_api_key": "",
  "deepseek_base_url": "",
  "deepseek_model": "",
  "temperature": 0.2,
  "max_tokens": 2000,
  "user_agent": "",
  "request_timeout": 10,
  "sqlite_path": "",
  "output_dir": "",
  "export_markdown": true,
  "export_word": false,
  "ai_summary_enabled": true,
  "ai_translation_enabled": false,
  "ai_keywords_enabled": true,
  "ai_category_enabled": true,
  "ai_importance_enabled": true,
  "body_mode": "original",
  "date_mode": "last_7_days",
  "start_date": "",
  "end_date": "",
  "max_article_tokens": 2000,
  "max_daily_api_calls": 100,
  "ai_limit_action": "skip"
}
```

## Export

Markdown and Word export support AI translation, AI summary fields, RSS summary
fallback, and configurable body output modes. SQLite continues to store the raw
original article only.

## Language Resources

GUI text is loaded from language resources:

- `language/zh_CN.json`
- `language/en_US.json`

The default language is Simplified Chinese.

## Resources

All packaged resources are resolved through `resources.resource_path()`:

- `config.json`
- `feeds.json`
- `language/`
- `assets/sspo.ico`
- `output/`
- `database/`

This supports development runs and PyInstaller onedir/onefile builds.

## Dependencies

Install dependencies:

```bash
pip install -r requirements.txt
```

If a dependency is missing, the desktop app shows a friendly message instead of
a Python traceback.

## PyInstaller Notes

Include resource files when packaging:

```bash
pyinstaller app.py --windowed --icon assets/sspo.ico \
  --add-data "assets;assets" \
  --add-data "language;language" \
  --add-data "config.json;." \
  --add-data "feeds.json;."
```
