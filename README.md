# SpaceWeekly

SpaceWeekly v2.0 Intelligent Edition is an AI Aerospace News Workspace.

It collects aerospace RSS feeds, parses article pages, analyzes news value with
AI, filters low-value items, classifies reports, and exports Word/Markdown
weekly reports.

## One-Minute Start

1. Download SpaceWeekly.
2. Extract the package.
3. Double-click `SpaceWeekly.exe` or run:

```bash
python app.py
```

4. Add RSS feeds.
5. Optionally enter an AI API key.
6. Click Start.

## Main Features

- RSS feed manager
- Dedicated parsers plus Generic Parser fallback
- AI news score from 0 to 100
- AI keep/ignore decision with reason
- Multiple categories per article
- AI summary, keywords, category, importance, and translation
- Original, bilingual, translated, or no-body export modes
- Custom report title
- Word and Markdown export
- Token, cost, and time estimates
- Daily token and API-call limits

## Generic Parser

Special parsers are used first for CNEOS, JPL, and NASA Science. Other websites
fall back to the Generic Parser. If page parsing is weak, RSS Summary is used as
the article body so the workflow continues.

## AI Providers

The AI layer uses an OpenAI-compatible provider interface. The GUI supports:

- DeepSeek
- OpenAI
- Gemini
- Claude
- OpenRouter
- SiliconFlow

Set API Key, Base URL, Model, Temperature, and Max Tokens in Settings.

## User Settings

User settings are stored in `config.json`. RSS feeds are stored in `feeds.json`.
Both files are user editable and are not stored in SQLite.

## Packaging

Use the included spec file:

```bash
pyinstaller SpaceWeekly.spec
```

The spec includes:

- `assets/`
- `language/`
- `config.json`
- `feeds.json`

## Dependencies

Install dependencies for development:

```bash
pip install -r requirements.txt
```
