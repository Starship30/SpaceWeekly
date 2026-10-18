import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from models.article import Article
from resources import resource_path

PROMPTS_DIR = resource_path("prompts")
DEFAULT_TITLE_RULE = '根据文章内容概括一个简单明了的标题。保留关键事实、型号和任务名称，不夸张，不添加原文没有的信息。'
PROMPT_TYPES = ["filter", "summary", "title"]
VARIABLES = ["title", "body", "summary", "source", "published", "url"]


@dataclass
class PromptPreset:
    name: str
    summary: str
    filter: str
    title: str = DEFAULT_TITLE_RULE


def load_presets() -> list[PromptPreset]:
    """Load all prompt preset JSON files."""
    _ensure_prompt_dir()
    presets = []

    for path in sorted(PROMPTS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        presets.append(_preset_from_dict(data))

    return presets


def save_preset(preset: PromptPreset) -> Path:
    """Save a prompt preset as JSON."""
    _ensure_prompt_dir()
    filename = _safe_filename(preset.name) + ".json"
    path = PROMPTS_DIR / filename
    path.write_text(
        json.dumps(asdict(preset), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return path


def default_preset() -> PromptPreset:
    """Return the default prompt preset."""
    presets = load_presets()

    for preset in presets:
        if preset.name.lower() == "default":
            return preset

    return PromptPreset("Default", "", "")


def find_preset(name: str) -> PromptPreset:
    """Find a prompt preset by name, falling back to Default."""
    for preset in load_presets():
        if preset.name == name:
            return preset

    return default_preset()


def prompt_value(settings_value: str, preset: PromptPreset, prompt_type: str) -> str:
    """Return an explicit setting prompt or the selected preset prompt."""
    if settings_value:
        return settings_value

    return str(getattr(preset, prompt_type, ""))


def render_prompt(template: str, article: Article) -> str:
    """Render prompt variables using an article."""
    values = {
        "title": article.news.title,
        "body": article.body,
        "summary": article.news.summary,
        "source": article.news.source,
        "published": article.news.published,
        "url": article.news.url,
    }
    rendered = template

    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)

    return rendered


def _ensure_prompt_dir() -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)


def _preset_from_dict(data: dict[str, object]) -> PromptPreset:
    return PromptPreset(
        name=str(data.get("name", "Custom")),
        summary=str(data.get("summary", "")),
        filter=str(data.get("filter", "")),
        title=str(data.get("title") or DEFAULT_TITLE_RULE),
    )


def _safe_filename(name: str) -> str:
    safe = "".join(character if character.isalnum() else "_" for character in name)

    return safe.strip("_") or "prompt"
