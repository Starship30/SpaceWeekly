# SpaceWeekly

[Download](https://github.com/Starship30/SpaceWeekly/releases/download/v2.3.1/SpaceWeekly.exe)

SpaceWeekly v2.4.3 AI Workflow Edition is an AI Aerospace News Workspace.

It collects aerospace RSS feeds, parses article pages, screens news importance,
generates content only for retained items, and exports Word/Markdown weekly
reports.

## v2.4.3 Controlled News Categories

- Non-overwriting numbered Word and Markdown report names
- Cooperative task cancellation with atomic report writes
- Centralized provider/model configuration and automatic model discovery
- Real API connection diagnostics with HTTP error details
- Daily/latest logs, standalone error reports, and recoverable AI JSON parsing
- Unified Chinese/English AI output rules and asynchronous startup checks
- Stage 1 returns only `importance` and `reason`
- Stage 2 returns only `summary`, `keywords`, and `category` for retained news
- Selectable `High_only`, `High_Medium` (default), and `All` filter levels
- RSS titles are preserved as source data and translated in batches when needed
- Stage 2 also selects up to three categories from `categories.json`
- Category IDs and Chinese/English labels are centralized and validated locally
- Category statistics are available through `category_counts`

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
- Two-stage AI workflow: importance screening, then retained-news summaries
- Prompt Studio for editing, testing, importing, and exporting prompt presets
- Strict JSON AI output for stable parsing
- High/Medium/Low filtering with an AI reason
- Original or no-body export modes
- Custom report title
- Word and Markdown export

## Prompt Studio

Prompt Studio manages the `filter` and `summary` prompt presets in
`prompts/*.json`.

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
