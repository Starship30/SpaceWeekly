from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from resources import resource_path

logger = logging.getLogger(__name__)


class AIResponseParseError(ValueError):
    """Raised after all safe JSON recovery strategies have failed."""


class AIEmptyResponseError(AIResponseParseError):
    """Raised when a provider returned no content at all."""


def request_json(
    prompt: str,
    provider_config,
    *,
    module_name: str,
    cancel_token=None,
    validator=None,
    max_attempts: int = 2,
    retry_base_delay: float = 0.25,
) -> dict[str, object]:
    """Request strict JSON with bounded parse/empty-response retries."""

    from ai.providers import complete

    raw_outputs: list[str] = []
    errors: list[str] = []

    attempts = max(1, min(int(max_attempts), 4))
    last_error: AIResponseParseError | None = None

    for attempt in range(attempts):
        request_prompt = prompt

        if attempt:
            previous_reason = errors[-1] if errors else "invalid JSON"
            request_prompt = (
                prompt
                + "\n\nYour previous response was rejected for this reason: "
                + previous_reason
                + ". Correct that specific problem. Return only the exact JSON "
                "object required by the schema, with valid escaping and no "
                "Markdown code fence."
            )

        content = complete(
            request_prompt,
            provider_config,
            cancel_token=cancel_token,
        )
        raw_outputs.append(content)

        try:
            data = parse_json_object(content)

            if validator is not None:
                validator(data)

            return data
        except AIResponseParseError as exc:
            last_error = exc
            errors.append(str(exc))
            reason = (
                "EMPTY_RESPONSE"
                if isinstance(exc, AIEmptyResponseError)
                else "INVALID_JSON"
            )
            label = "Translator" if module_name.startswith("translator") else "AI"
            logger.warning(
                "[%s] llm=FAIL attempt=%s error=%s detail=%s",
                label,
                attempt + 1,
                reason,
                exc,
            )

            if attempt + 1 < attempts:
                _cancelable_wait(
                    cancel_token,
                    min(2.0, max(0.0, retry_base_delay) * (2 ** attempt)),
                )

    raw_path = save_raw_response(
        raw_outputs,
        module_name=module_name,
        model=provider_config.model,
        prompt=prompt,
        errors=errors,
    )
    message = f"AI JSON 重试后仍解析失败；诊断已保存到 {raw_path}"

    if isinstance(last_error, AIEmptyResponseError):
        raise AIEmptyResponseError(message)

    raise AIResponseParseError(message)


def parse_json_object(content: str) -> dict[str, object]:
    """Strip wrappers, extract an object, and repair common JSON mistakes."""

    cleaned = _strip_code_fence(content)

    if not cleaned:
        raise AIEmptyResponseError("AI 返回内容为空")

    candidates = _repair_candidates(cleaned)
    last_error: json.JSONDecodeError | None = None

    for candidate in candidates:
        try:
            data = json.loads(candidate, strict=False)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

        if isinstance(data, dict):
            return data

    detail = str(last_error) if last_error is not None else "response is not an object"
    raise AIResponseParseError(f"AI 返回内容无法解析为 JSON：{detail}")


def save_raw_response(
    raw_outputs: list[str] | str,
    *,
    module_name: str,
    model: str,
    prompt: str = "",
    errors: list[str] | None = None,
) -> Path:
    """Persist safe AI diagnostics after the retry path is exhausted."""

    outputs = raw_outputs if isinstance(raw_outputs, list) else [raw_outputs]
    output_dir = resource_path("logs", "ai_raw")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"{module_name}_{timestamp}_{uuid4().hex[:6]}.log"
    sections = [
        f"time: {datetime.now().astimezone().isoformat()}",
        f"module: {module_name}",
        f"model: {model or '-'}",
        "",
        "prompt:",
        prompt or "-",
        "",
        "parse_errors:",
        "\n".join(errors or []) or "-",
    ]

    for index, output in enumerate(outputs, start=1):
        sections.extend(
            [
                "",
                f"raw_output_{index}:",
                str(output),
            ]
        )

    path.write_text("\n".join(sections), encoding="utf-8")
    logger.error(
        "AI JSON 重试失败，Prompt 和原始输出已保存：%s（模型：%s）",
        path,
        model or "-",
    )
    return path


def _strip_code_fence(content: str) -> str:
    text = str(content or "").strip().lstrip("\ufeff")
    fenced = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced:
        text = fenced.group(1).strip()

    first = text.find("{")
    last = text.rfind("}")

    if first >= 0 and last > first:
        return text[first:last + 1]

    return text


def _repair_candidates(text: str) -> list[str]:
    candidates = [text]
    normalized = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    candidates.append(normalized)
    closed = _close_unterminated_json(normalized)

    if closed not in candidates:
        candidates.append(closed)

    return candidates


def _close_unterminated_json(text: str) -> str:
    escaped = False
    in_string = False
    stack: list[str] = []

    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character in "[{":
            stack.append(character)
        elif character == "]" and stack and stack[-1] == "[":
            stack.pop()
        elif character == "}" and stack and stack[-1] == "{":
            stack.pop()

    repaired = text.rstrip()

    if escaped:
        repaired = repaired[:-1]

    if in_string:
        repaired += '"'

    while stack:
        repaired += "]" if stack.pop() == "[" else "}"

    return repaired


def _cancelable_wait(cancel_token, seconds: float) -> None:
    if seconds <= 0:
        return

    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if cancel_token is not None:
            cancel_token.check()

        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
