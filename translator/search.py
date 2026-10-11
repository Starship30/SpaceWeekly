from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib import error
from urllib import request
from urllib.parse import parse_qs, unquote, urlparse
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

OFFICIAL_DOMAINS = (
    "nasa.gov",
    "esa.int",
    "spacex.com",
    "blueorigin.com",
    "ulalaunch.com",
    "rocketlabusa.com",
    "arianespace.com",
    "roscosmos.ru",
    "isro.gov.in",
    "cnsa.gov.cn",
    "cas.cn",
    "jaxa.jp",
    "landspace.com",
    "spacepioneer.cc",
    "galactic-energy.cn",
    "orienspace.com",
)


class SearchProviderError(RuntimeError):
    pass


class SearchRateLimitError(SearchProviderError):
    """A search host requires waiting or interactive verification."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    official: bool = False


class SearchProvider(ABC):
    """Small provider boundary so search vendors remain replaceable."""

    name = "search"

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class DuckDuckGoSearchProvider(SearchProvider):
    name = "duckduckgo"

    def __init__(self, timeout: int = 10, user_agent: str = "") -> None:
        self.timeout = timeout
        self.user_agent = user_agent or "Mozilla/5.0 SpaceWeekly/2.4"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            body = _http_get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
        except (error.HTTPError, error.URLError, OSError) as exc:
            kind = SearchRateLimitError if isinstance(exc, error.HTTPError) and exc.code == 429 else SearchProviderError
            raise kind(f"DuckDuckGo request failed: {exc}") from exc

        if 'id="challenge-form"' in body or 'id="anomaly-form"' in body:
            raise SearchRateLimitError("DuckDuckGo requires interactive verification")
        parser = _DuckDuckGoParser()
        parser.feed(body)
        results: list[SearchResult] = []

        for title, raw_url, snippet in parser.results:
            url = _duckduckgo_target(raw_url)
            result = _make_result(
                title,
                url,
                snippet,
            )

            if result is not None:
                results.append(result)

            if len(results) >= max_results:
                break

        if not results:
            raise SearchProviderError("DuckDuckGo returned no usable results")

        return results


class BraveSearchProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str, timeout: int = 10) -> None:
        self.api_key = str(api_key or "").strip()
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not self.api_key:
            raise SearchProviderError("Brave Search API key is not configured")

        try:
            body = _http_get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20)},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout=self.timeout,
            )
            payload = json.loads(body)
        except (error.HTTPError, error.URLError, OSError, ValueError) as exc:
            kind = SearchRateLimitError if isinstance(exc, error.HTTPError) and exc.code == 429 else SearchProviderError
            raise kind(f"Brave Search request failed: {exc}") from exc

        raw_results = payload.get("web", {}).get("results", [])
        results = [
            result
            for item in raw_results
            if isinstance(item, dict)
            for result in [
                _make_result(
                    str(item.get("title") or ""),
                    str(item.get("url") or ""),
                    str(item.get("description") or ""),
                )
            ]
            if result is not None
        ]

        if not results:
            raise SearchProviderError("Brave Search returned no usable results")

        return results[:max_results]


class WikipediaSearchProvider(SearchProvider):
    """No-key auxiliary provider used only when a primary web search is unavailable."""

    name = "wikipedia"

    def __init__(self, timeout: int = 10, user_agent: str = "") -> None:
        self.timeout = timeout
        self.user_agent = user_agent or "SpaceWeekly/2.4"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            body = _http_get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": min(max_results, 20),
                    "format": "json",
                    "utf8": 1,
                },
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            payload = json.loads(body)
        except (error.HTTPError, error.URLError, OSError, ValueError) as exc:
            raise SearchProviderError(f"Wikipedia request failed: {exc}") from exc

        items = payload.get("query", {}).get("search", [])
        results = [
            SearchResult(
                title=str(item.get("title") or ""),
                url=(
                    "https://en.wikipedia.org/?curid="
                    + str(item.get("pageid") or "")
                ),
                snippet=_plain_text(str(item.get("snippet") or ""))[:800],
                official=False,
            )
            for item in items
            if isinstance(item, dict) and item.get("pageid")
        ]

        if not results:
            raise SearchProviderError("Wikipedia returned no usable results")

        return results[:max_results]


class FallbackSearchProvider(SearchProvider):
    def __init__(self, providers: list[SearchProvider]) -> None:
        self.providers = providers
        self.name = "+".join(provider.name for provider in providers)

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        errors: list[str] = []

        for provider in self.providers:
            try:
                return provider.search(query, max_results=max_results)
            except SearchProviderError as exc:
                errors.append(f"{provider.name}: {exc}")

        raise SearchProviderError("; ".join(errors) or "all search providers failed")


def create_search_provider(settings, *, allow_wikipedia_fallback=True) -> SearchProvider:
    provider = str(getattr(settings, "web_search_provider", "duckduckgo") or "duckduckgo")
    timeout = int(getattr(settings, "web_search_timeout", 10) or 10)

    if provider.casefold() == "brave":
        return BraveSearchProvider(
            getattr(settings, "web_search_api_key", ""),
            timeout=timeout,
        )

    if provider.casefold() == "duckduckgo":
        primary = DuckDuckGoSearchProvider(
            timeout=timeout, user_agent=getattr(settings, "user_agent", ""))
        if not allow_wikipedia_fallback:
            return primary
        return FallbackSearchProvider([primary, WikipediaSearchProvider(
            timeout=timeout, user_agent=getattr(settings, "user_agent", ""))])

    if provider.casefold() == "wikipedia":
        if not allow_wikipedia_fallback:
            raise SearchProviderError("Wikipedia cannot verify dated news announcements; choose DuckDuckGo or Brave")
        return WikipediaSearchProvider(
            timeout=timeout,
            user_agent=getattr(settings, "user_agent", ""),
        )

    raise SearchProviderError(f"Unsupported search provider: {provider}")


def search_aerospace_sources(
    provider: SearchProvider,
    term: str,
    *,
    max_results: int = 5,
    retry_count: int = 1,
) -> list[SearchResult]:
    """Search exact aerospace context and rank official sources first."""

    official_sites = " OR ".join(
        f"site:{domain}"
        for domain in (
            "nasa.gov",
            "esa.int",
            "spacex.com",
            "blueorigin.com",
            "ulalaunch.com",
            "rocketlabusa.com",
            "arianespace.com",
            "orienspace.com",
        )
    )
    queries = [
        f'"{term}" ({official_sites})',
        f'"{term}" rocket spacecraft launch aerospace',
    ]
    last_error: Exception | None = None

    for attempt in range(max(0, retry_count) + 1):
        combined: list[SearchResult] = []

        for query in queries:
            try:
                combined.extend(
                    provider.search(query, max_results=max_results * 2)
                )
            except SearchProviderError as exc:
                last_error = exc

        if combined:
            unique = {item.url: item for item in combined}
            ranked = sorted(
                unique.values(),
                key=lambda item: (
                    not item.official,
                    -_relevance_score(item, term),
                ),
            )
            return ranked[:max_results]

        logger.warning(
            "[Translator] web_search=FAIL provider=%s attempt=%s error=SEARCH_ERROR detail=%s",
            provider.name,
            attempt + 1,
            last_error or "no usable results",
        )

        if attempt < retry_count:
            time.sleep(min(2.0, 0.25 * (2 ** attempt)))

    raise SearchProviderError(str(last_error or "search failed")) from last_error


def _make_result(title: str, url: str, snippet: str) -> SearchResult | None:
    clean_url = str(url or "").strip()

    if not clean_url.startswith(("http://", "https://")):
        return None

    host = (urlparse(clean_url).hostname or "").casefold()
    official = any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)
    return SearchResult(
        title=" ".join(str(title or "").split())[:300],
        url=clean_url,
        snippet=" ".join(str(snippet or "").split())[:800],
        official=official,
    )


def _duckduckgo_target(url: str) -> str:
    parsed = urlparse(url)
    target = parse_qs(parsed.query).get("uddg", [""])[0]
    return unquote(target) if target else url


def _http_get(
    url: str,
    *,
    params: dict[str, object],
    headers: dict[str, str],
    timeout: int,
) -> str:
    api_request = request.Request(
        url + "?" + urlencode(params),
        headers=headers,
        method="GET",
    )

    with request.urlopen(api_request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str, str]] = []
        self.current: dict[str, object] | None = None
        self.result_depth = 0
        self.capture_title = False
        self.snippet_tag = ""

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        classes = str(attributes.get("class") or "").split()

        if self.current is None and tag == "div" and "result" in classes:
            self.current = {"title": [], "url": "", "snippet": []}
            self.result_depth = 1
            return

        if self.current is None:
            return

        if tag == "div":
            self.result_depth += 1

        if tag == "a" and "result__a" in classes:
            self.current["url"] = str(attributes.get("href") or "")
            self.capture_title = True

        if "result__snippet" in classes:
            self.snippet_tag = tag

    def handle_endtag(self, tag) -> None:
        if self.current is None:
            return

        if tag == "a":
            self.capture_title = False

        if tag == self.snippet_tag:
            self.snippet_tag = ""

        if tag == "div":
            self.result_depth -= 1

            if self.result_depth == 0:
                self.results.append(
                    (
                        " ".join(self.current["title"]),
                        str(self.current["url"]),
                        " ".join(self.current["snippet"]),
                    )
                )
                self.current = None

    def handle_data(self, data) -> None:
        if self.current is None:
            return

        text = str(data or "").strip()

        if not text:
            return

        if self.capture_title:
            self.current["title"].append(text)

        if self.snippet_tag:
            self.current["snippet"].append(text)


def _plain_text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _relevance_score(result: SearchResult, term: str) -> int:
    needle = str(term or "").casefold()
    title = result.title.casefold()
    snippet = result.snippet.casefold()
    score = 0

    if needle and needle in title:
        score += 6

    if needle and needle in snippet:
        score += 3

    for token in re.findall(r"[a-z0-9]+", needle):
        score += int(token in title) * 2 + int(token in snippet)

    return score
