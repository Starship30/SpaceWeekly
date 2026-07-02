from bs4 import BeautifulSoup
from bs4.element import Tag

from models.article import Article
from models.news import News
from parsers.cleaning import clean_body

MIN_BODY_LENGTH = 200


def parse(news: News, html: str) -> Article:
    """Parse an unknown news page into an Article."""
    soup = BeautifulSoup(html, "html.parser")
    article = _find_article(soup)
    body = _extract_body(article)

    if len(body) < MIN_BODY_LENGTH:
        body = news.summary

    return Article(
        news=news,
        body=body,
        parser="Generic",
    )


def _find_article(soup: BeautifulSoup) -> Tag | None:
    selectors = [
        "article",
        "main",
        ".article-body",
        ".entry-content",
        ".post-content",
        ".content",
    ]

    for selector in selectors:
        article = soup.select_one(selector)

        if article is not None:
            return article

    return _div_with_most_paragraphs(soup)


def _div_with_most_paragraphs(soup: BeautifulSoup) -> Tag | None:
    best_div = None
    best_count = 0

    for div in soup.find_all("div"):
        count = len(div.find_all("p"))

        if count > best_count:
            best_div = div
            best_count = count

    return best_div


def _extract_body(article: Tag | None) -> str:
    if article is None:
        return ""

    paragraphs = article.find_all("p")

    if not paragraphs:
        return clean_body(article.get_text("\n", strip=True))

    lines = []

    for paragraph in paragraphs:
        text = paragraph.get_text(" ", strip=True)

        if text:
            lines.append(text)

    return clean_body("\n".join(lines))
