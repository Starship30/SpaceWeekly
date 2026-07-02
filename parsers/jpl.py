from bs4 import BeautifulSoup
from bs4.element import Tag

from models.article import Article
from models.news import News
from parsers.cleaning import clean_body


def parse(news: News, html: str) -> Article:
    """Parse a JPL page into an Article."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find(
        "div",
        attrs={
            "itemprop": "articleBody"
        }
    )
    body = _extract_body(article)

    return Article(
        news=news,
        body=body,
        parser="JPL"
    )


def _extract_body(article: Tag | None) -> str:
    if article is None:
        return ""

    paragraphs = article.find_all("p")
    body = []

    for paragraph in paragraphs:
        text = paragraph.get_text(strip=True)

        if text:
            body.append(text)

    return clean_body("\n".join(body))
