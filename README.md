# SpaceWeekly

SpaceWeekly v2.1 AI Workflow Edition is an AI Aerospace News Workspace.

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
- AI Workflow with optional score, filter, category, keywords, summary, and translation
- Prompt Studio for editing, testing, importing, and exporting prompt presets
- Strict JSON AI output for stable parsing
- AI news score from 0 to 100
- AI keep/ignore decision with reason
- Fixed multi-category system
- Original, bilingual, translated, or no-body export modes
- Custom report title
- Word and Markdown export
- Token, cost, and time estimates
- Daily token and API-call limits

## Prompt Studio

Prompt Studio manages prompt presets in `prompts/*.json`.

Supported variables:

- `{title}`
- `{body}`
- `{summary}`
- `{source}`
- `{published}`
- `{url}`

The tester renders prompts locally with a sample article, estimates tokens, and
does not refetch RSS.

## AI Providers

The AI layer uses an OpenAI-compatible provider interface. The GUI supports:

- DeepSeek
- OpenAI
- Gemini
- Claude
- OpenRouter
- SiliconFlow

Set API Key, Base URL, Model, Temperature, Max Tokens, timeout, and retry count
in Settings.

## Packaging

Use the included spec file:

```bash
pyinstaller SpaceWeekly.spec
```

The spec includes:

- `assets/`
- `language/`
- `prompts/`
- `config.json`
- `feeds.json`

## Dependencies

Install dependencies for development:

```bash
pip install -r requirements.txt
```
