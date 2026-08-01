from __future__ import annotations

from dataclasses import dataclass

from ai.configuration import provider_config_from_settings
from ai.providers import test_connection
from services.settings import AppSettings


@dataclass
class StartupCheck:
    name: str
    ok: bool
    detail: str


def run_startup_checks(settings: AppSettings) -> list[StartupCheck]:
    """Run startup diagnostics. Intended to execute in a worker thread."""

    checks = [
        _import_check("RSS 模块", "feedparser"),
        _launch_check(settings),
        _export_check(),
        _category_check(),
        _ai_config_check(settings),
    ]

    if checks[-1].ok:
        checks.append(_api_check(settings))
    else:
        checks.append(StartupCheck("API 连接状态", False, "AI 配置不完整，未连接"))

    return checks


def _import_check(name: str, module: str) -> StartupCheck:
    try:
        __import__(module)
    except Exception as exc:
        return StartupCheck(name, False, str(exc))

    return StartupCheck(name, True, "可用")


def _launch_check(settings: AppSettings) -> StartupCheck:
    try:
        from feeds.launches import get_launches
        from feeds.launches import last_error

        if settings.launch_range != "disabled":
            get_launches(limit=1, launch_range=settings.launch_range)
            failure = last_error()

            if failure:
                return StartupCheck("Launch Library 2", False, failure)
    except Exception as exc:
        return StartupCheck("Launch Library 2", False, str(exc))

    detail = "模块可用" if settings.launch_range == "disabled" else "Endpoint 正常"
    return StartupCheck("Launch Library 2", True, detail)


def _export_check() -> StartupCheck:
    try:
        import docx  # noqa: F401

        from exporters.markdown import export_markdown  # noqa: F401
        from exporters.word import export_word  # noqa: F401
    except Exception as exc:
        return StartupCheck("导出模块", False, str(exc))

    return StartupCheck("导出模块", True, "Word / Markdown 可用")


def _category_check() -> StartupCheck:
    try:
        from services.news_categories import category_labels
        from services.news_categories import category_limit

        count = len(category_labels("zh_CN"))
        maximum = category_limit()
    except Exception as exc:
        return StartupCheck("新闻分类", False, str(exc))

    return StartupCheck(
        "新闻分类",
        True,
        f"{count} 个统一分类，每篇最多 {maximum} 个",
    )


def _ai_config_check(settings: AppSettings) -> StartupCheck:
    missing = []

    if not settings.deepseek_api_key:
        missing.append("API Key")

    if not settings.deepseek_base_url:
        missing.append("Endpoint")

    if not settings.deepseek_model and not settings.model_candidates:
        missing.append("模型")

    if missing:
        return StartupCheck("AI 配置", False, "缺少：" + "、".join(missing))

    return StartupCheck("AI 配置", True, f"当前模型：{settings.deepseek_model}")


def _api_check(settings: AppSettings) -> StartupCheck:
    try:
        result = test_connection(
            provider_config_from_settings(settings),
            settings.model_candidates,
        )
    except Exception as exc:
        return StartupCheck("API 连接状态", False, str(exc))

    return StartupCheck(
        "API 连接状态",
        True,
        f"{result.model}，{result.elapsed_ms} ms",
    )
