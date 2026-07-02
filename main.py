from collections.abc import Callable

from database.sqlite import get_articles, initialize_database, save_article
from downloader.client import download
from exporters.markdown import export_markdown
from feeds.cneos import get_news
from models.article import Article
from models.news import News
from parsers.jpl import parse as parse_jpl
from parsers.science import parse as parse_science

Parser = Callable[[News, str], Article]
PARSERS: dict[str, Parser] = {
    "jpl.nasa.gov": parse_jpl,
    "science.nasa.gov": parse_science,
}


def get_parser(url: str) -> Parser | None:
    for domain, parser in PARSERS.items():
        if domain in url:
            return parser

    return None


def main() -> None:
    initialize_database()
    news_list = get_news()

    for news in news_list:
        parser = get_parser(news.url)

        if parser is None:
            continue

        html = download(news.url)
        article = parser(news, html)

        if save_article(article):
            print("SAVE")
        else:
            print("SKIP")

    export_markdown(get_articles())


if __name__ == "__main__":
    main()
