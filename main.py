import logging

from ai.deepseek import summarize
from database.sqlite import get_articles, initialize_database, save_article
from downloader.client import download
from exporters.markdown import export_markdown
from feeds.cneos import get_news
from logging_config import setup_logging
from models.ai_summary import AISummary
from parsers.registry import get_parser

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    ai_summaries: dict[str, AISummary] = {}

    initialize_database()
    logger.info("Fetch RSS")
    news_list = get_news()

    for news in news_list:
        parser = get_parser(news.url)

        if parser is None:
            logger.warning("No Parser %s", news.url)
            continue

        html = download(news.url)
        article = parser(news, html)
        logger.info("Parse %s", article.parser)

        try:
            ai_summary = summarize(article)
        except Exception as exc:
            logger.warning("AI Summary Failed %s", exc)
        else:
            logger.info("AI Summary Success")
            ai_summaries[article.news.url] = ai_summary

        save_status = "SAVE" if save_article(article) else "SKIP"
        logger.info("Save SQLite %s", save_status)

    logger.info("Export Markdown")
    export_markdown(get_articles(), ai_summaries)
    logger.info("Markdown Exported")

if __name__ == "__main__":
    main()
