from bs4 import BeautifulSoup
from bs4.element import Tag

from models.article import Article
from models.news import News


def parse(news: News, html: str) -> Article:
    """Parse a NASA Science page into an Article."""
    soup = BeautifulSoup(html, "html.parser")
    article = _find_article(soup)
    body = _extract_body(article)

    return Article(
        news=news,
        body=body,
        parser="NASA Science"
    )


def _find_article(soup: BeautifulSoup) -> Tag | None:
    selectors = [
        "article",
        "main article",
        "main",
        "div.entry-content",
        "div.article-content",
        "div.content",
    ]

    for selector in selectors:
        article = soup.select_one(selector)

        if article is not None:
            return article

    return None


def _extract_body(article: Tag | None) -> str:
    if article is None:
        return ""

    paragraphs = article.find_all("p")
    body = []

    for paragraph in paragraphs:
        text = paragraph.get_text(strip=True)

        if text:
            body.append(text)

    return "\n".join(body)
