from bs4 import BeautifulSoup
from bs4.element import Tag

from models.article import Article
from models.news import News
from parsers.cleaning import clean_body


def parse(news: News, html: str) -> Article:
    """Parse a CNEOS news page into an Article."""
    soup = BeautifulSoup(html, "html.parser")
    article = _find_article(soup)
    body = _extract_body(article)

    return Article(
        news=news,
        body=body,
        parser="CNEOS",
    )


def _find_article(soup: BeautifulSoup) -> Tag | None:
    selectors = [
        "article",
        "main",
        "div#mainContent",
        "div#content",
        "div.content",
        "td.content",
    ]

    for selector in selectors:
        article = soup.select_one(selector)

        if article is not None:
            return article

    return soup.body


def _extract_body(article: Tag | None) -> str:
    if article is None:
        return ""

    paragraphs = article.find_all("p")
    body = []

    if not paragraphs:
        return clean_body(article.get_text("\n", strip=True))

    for paragraph in paragraphs:
        text = paragraph.get_text(" ", strip=True)

        if text:
            body.append(text)

    return clean_body("\n".join(body))
