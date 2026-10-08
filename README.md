# SpaceWeekly

[Download](https://github.com/Starship30/SpaceWeekly/releases/download/v2.4.6/SpaceWeekly.exe)

SpaceWeekly v2.4.6 AI Workflow Edition is an AI Aerospace News Workspace.

It collects aerospace RSS feeds and Space.com web news, parses articles, screens news importance,
generates content only for retained items, and exports Word/Markdown weekly
reports.

## v2.4.6 Modern Workspace and Date Ranges

- Provider presets fill official Base URLs; Custom supports OpenAI-compatible endpoints
- Models are discovered asynchronously after entering the provider API key, with manual model entry retained
- Claude uses native model discovery and Messages requests
- Rounded dropdown menus and selected rows share the workspace style
- Modern light/dark workspace cards, settings sidebar, and segmented date selection
- Isolated DLL resolution during packaging prevents incompatible ICU libraries from PATH
- Packaged startup check: `python tests/check_packaged.py`
- Application version is maintained in `config.app_version()`
- News supports recent-day presets or an explicit date range
- Launches support past/next week, past/next 30 days, or an explicit date range
- Custom launch dates include the full end day in the selected display timezone
- Quick range and custom dates are mutually exclusive; reversed dates are rejected
- News dates are filtered before the reading limit; supports ISO/RFC and updated dates
- Space.com pagination skips newer items until the selected date range
- Removes outdated token estimation and Max Tokens documentation

## v2.4.5 Space.com Pagination Fix

- Applies the configured RSS reading limit across Space.com listing pages
- Stops at the requested number of unique articles, with no detail-page requests
- Supports cancellation between pages and preserves fetched articles on a later-page error
- Logs incomplete results when the listing ends or pagination fails before the limit
- Direct `fetch()` / `fetch(limit=0)` calls retain single-page behavior; the UI uses 1–500

## v2.4.4 Space.com Web Provider

- Fetches Space.com news directly from its news listing instead of empty RSS feeds
- Reuses the existing Article pipeline and SQLite URL deduplication
- Normalizes URLs and dates, isolates parsing failures, and avoids detail-page requests
- Existing Space.com RSS configurations automatically use the web provider

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

The tester renders prompts locally with a sample article and does not refetch RSS.

## AI Providers

The AI layer supports OpenAI-compatible interfaces and the native Claude Messages API. The GUI supports:

- DeepSeek
- OpenAI
- Gemini
- Claude
- OpenRouter
- SiliconFlow

Choose a provider in Settings to fill its Base URL, then enter that provider's API Key.
The model selector loads the server's model list in the background and remains editable.
Choose Custom for an OpenAI-compatible gateway with a manually entered Base URL.
Temperature, timeout, and retry count remain configurable.

Endpoint references: [DeepSeek](https://api-docs.deepseek.com/en/),
[OpenAI](https://developers.openai.com/api/reference/resources/models/methods/list),
[Gemini](https://ai.google.dev/gemini-api/docs/openai),
[Claude](https://platform.claude.com/docs/en/api/models/list),
[OpenRouter](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties),
[SiliconFlow](https://docs.siliconflow.cn/docs/userguide/quickstart).

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
