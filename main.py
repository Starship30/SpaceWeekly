from collections.abc import Callable

from downloader.client import download
from feeds.cneos import get_news
from models.article import Article
from models.news import News
from parsers.jpl import parse as parse_jpl
from parsers.science import parse as parse_science

Parser = Callable[[News, str], Article]


def get_parser(url: str) -> Parser | None:
    if "jpl.nasa.gov" in url:
        return parse_jpl

    if "science.nasa.gov" in url:
        return parse_science

    return None


def parse_article(news: News) -> Article | None:
    parser = get_parser(news.url)

    if parser is None:
        return None

    html = download(news.url)

    return parser(news, html)


def main() -> None:
    news_list = get_news()

    for news in news_list:
        article = parse_article(news)

        if article is None:
            continue

        print("=" * 80)
        print(article.news.title)
        print(article.news.published)
        print(article.body[:300])


if __name__ == "__main__":
    main()
