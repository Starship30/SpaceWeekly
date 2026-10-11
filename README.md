# SpaceWeekly

[Download V3.0.2 for Windows x64](https://github.com/Starship30/SpaceWeekly/releases/download/v3.0.2/SpaceWeekly_v3.0.2_Windows_x64.zip)

SpaceWeekly V3.0.2 AI Workflow Edition is an AI Aerospace News Workspace.

It collects aerospace RSS feeds, Space.com web news and Spaceflight News API articles, screens news importance,
generates content only for retained items, and exports Word/Markdown weekly
reports.

## V3.0.2 Video search and downloads

Open **Video assets → Video downloads** to search Bilibili or paste a BV/av/b23 link, inspect MP4 quality (highest downloadable quality selected by default) and download into the current Workspace's `video-downloads/`. Optional Bilibili QR login saves a Windows current-user encrypted session across windows and restarts; sign out deletes it. The download window reads JSON launch mission names, excludes news by default, supports a list of UP UIDs/space links and sorts retrieved videos oldest first. Mission searches default to the launch date and following day in UTC+8. After confirming usage rights, the All launches download Demo processes every JSON launch, tries up to three dated candidates and saves one permitted MP4 at the highest downloadable quality per task. Individual failures continue; cancel stops the batch and keeps completed videos. All videos in a batch share a folder named by the UTC+8 run date, with numbering for repeated batches; filenames use original video titles with collision numbering. The Windows ZIP includes SpaceWeekly.exe, yt-dlp.exe and QR support; extract the whole ZIP together. FFmpeg and ffprobe must be on PATH for merging and video encoding. Source users install `python -m pip install -r tools/requirements-video-download.txt`. See [3.0.2 release notes](RELEASE_NOTES_3.0.2.md). See [usage, implementation research and verification limits](tools/VIDEO_DOWNLOAD.md).

## V3.0.1 fixes (included in V3.0.2)

- News and launch MOV assets use separate `news` and `launches` folders, with `.news.mov` / `.launch.mov` filenames. Old files and manifests remain readable.
- The optional event-date check is enabled by default. It compares each retained report against dated official announcements from another organization, before database saves, summaries and exports. Unconfirmed in-range reports are retained and marked for review in the log; only evidenced out-of-range events are excluded. News searches do not fall back to Wikipedia; rate limits or verification pages pause further searches for the current batch. Turning it off keeps publication-date filtering. Search and configured AI are required and add time/API cost.
- The Network settings page exposes the existing DuckDuckGo / Brave search provider and masked Brave API key. A search key is separate from the AI key.
- Publication-only filtering excludes RSS entries with only an update date and compares timezone-aware dates as UTC calendar days.
- Prompt Studio is now **AI writing rules**: choose a step, describe requirements, preview with a fictional example, then apply. Preview runs locally without an AI call. Opening the editor preserves existing summary rules.

Event-date checking compares only retrieved announcements. AI matching and incomplete search results can miss earlier announcements; retained reports awaiting review may still cover old events; it does not guarantee the globally earliest event date. Background dates and new progress are handled separately. See [3.0.1 notes](RELEASE_NOTES_3.0.1.md).

## V3.0

- Spaceflight News API joins RSS and Space.com, with date pagination, per-source unlimited/custom limits and duplicate detection before AI processing
- The main dashboard contains independent news and launch date ranges; either collection can be disabled
- Optional UTF-8 JSON content snapshots reuse the current generation results, with localized titles, summaries, stable IDs and launch records
- Video assets creates news strips, launch cards and a minimal opener with custom name, date range, logo and saved template
- Selected news and launches are numbered independently without gaps; chronological order is configurable
- Opener logos support static motion presets, looping GIFs and videos that hold their final frame
- Windows EXE includes the desktop video modules and Pillow; video encoding still requires FFmpeg and ffprobe on PATH
- The UXP panel is an experimental offline option. Premiere 26/27 MOGRT text integration is **not host-verified**; the failed CEP experiment remains stopped and is not installed by this release

See [V3.0 release notes and validation limits](RELEASE_NOTES.md).

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
- News and launch date ranges are available in the main dashboard and stay in sync with Settings; either collection can be disabled independently
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

- RSS, web and API news source manager
- Dedicated parsers plus Generic Parser fallback
- Two-stage AI workflow: importance screening, then retained-news summaries
- Prompt Studio for editing, testing, importing, and exporting prompt presets
- Strict JSON AI output for stable parsing
- High/Medium/Low filtering with an AI reason
- Original or no-body export modes
- Custom report title
- Word and Markdown export

## News sources, limits and duplicates

Spaceflight News API v4 is available as a news source at
`https://api.spaceflightnewsapi.net/v4/articles/`. Enable **Spaceflight News API**
in the source list (new sources are initially unchecked). For an older workspace,
add that address through **Add → Source URL (RSS/API)**. The source uses the
official [date filters and pagination](https://api.spaceflightnewsapi.net/v4/docs/)
to collect the selected publication-date range, including the whole end day in UTC.
Original site names, dates, titles, summaries and URLs are retained; the API summary
is used as article content without additional detail-page requests.

The dashboard's **News limit per source** offers **Custom count** (including values
above 500) or **Unlimited within dates**. Existing positive `rss_limit` settings
retain their meaning; `0` now persists as unlimited. Each source filters dates
before applying the limit; merged results are deduplicated before AI screening.
Space.com also follows pages within the date range when unlimited is selected.
RSS can only supply entries present in its feed, so unlimited cannot recover
history the source does not expose. AI screening still applies after collection.

**Detect and skip duplicate news** defaults to enabled. It retains the first
article and logs later matches by normalized URL (ignoring tracking parameters,
fragments and HTTP/HTTPS/www variations), or the same normalized title across
different sources on the same UTC publication day. Meaningful URL query parameters,
numbers in titles and different publication days remain distinct. Original Article
objects and URLs are unchanged. This is per-run deduplication, without a database
migration or extra AI requests; different headlines covering the same event are
not automatically merged. The checkbox can disable this step.

An API first-page failure is isolated from other sources. A later-page network or
JSON failure preserves collected articles and logs that the result is incomplete.
Cancellation is checked between requests and articles; invalid pagination hosts
and repeated pagination URLs are rejected.

Validation: `python -m unittest discover -s tests -p "test_*.py"`.
Real API/JSON and Qt offscreen checks are recorded in
`output/news-source-trial/verification.json`: the 2026-10-01 through 2026-10-10
range returned 146 articles across two HTTP requests and produced a UTF-8 content
snapshot, without AI calls or SQLite changes. V3.0 packages the updated desktop app.

## Premiere offline MVP

Enable **Premiere content package (JSON)** in Settings → Export (off by default)
to export this run's selected news, localized titles and AI summaries into the
workspace output directory. The same JSON and UXP panel serve PR 26.x and PR 27.x;
actual MOGRT text support is checked on the first card. Neither host path has been
verified in Premiere here. See [installation, template contract, version limits
and manual acceptance](plugins/premiere-uxp/README.md).

## Offline video cards (experimental)

Generate transparent lower-third news strips (one-line title and scrolling summary)
and launch-task side panels from the same offline JSON without a Premiere plugin.
PNG MOV is the compact lossless default; ProRes 4444 remains available.
News and launches have separate display numbering and chronological sorting
(oldest first by default, newest first available in the UI or with `--order desc`). Launch text is localized
during content-package generation using the existing aerospace translator;
offline import/rendering does not call AI. Launch time labels omit UTC offsets.
Only checked items receive consecutive numbers within each type; unchecked
items have no number. Selection changes update preview numbers and invalidate
cached cards whose visible number changed, while retaining older MOV files.
Run `python -m tools.video_cards <snapshot.premiere.json> --seconds 16`
from the repository root. Unchanged cards are skipped; `--item-id` regenerates a
single stable news ID. Requires Pillow, FFmpeg/ffprobe and the local template fonts.
See [setup, regeneration, limits and Premiere acceptance](tools/VIDEO_CARDS.md).
The toolbar provides a **Video assets** window to select news/launches, edit text,
play a preview and generate MOV files in the background. **Create opener** makes
a minimal 7-second MP4 with a custom name, subtitle, local logo and date range
(latest 7 days by default). Logos can be static images, animated GIFs or local
MP4/MOV/M4V videos. Static logos offer fade, smooth zoom or rise presets; dynamic
logos keep their own animation. Videos play once and hold their last frame;
GIFs loop. The editor previews the first
frame; view the exported opener for playback. **Save template** restores name,
subtitle, logo and static motion; dates are set afresh each time. See the
[desktop workflow and validation scope](tools/VIDEO_CARDS_UI.md).
Prior ProRes news-strip and launch MOV playback has been confirmed by the user
in Premiere; the new PNG MOV and opener still need a host check.

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
