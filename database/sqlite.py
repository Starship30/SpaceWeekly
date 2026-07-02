import sqlite3
from contextlib import closing
from datetime import datetime

from config import DATABASE_PATH
from models.article import Article
from models.news import News


def initialize_database() -> None:
    """Create database tables if they do not already exist."""
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL,
                    parser TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )


def save_article(article: Article) -> bool:
    """Save one Article and return False when URL already exists."""
    with closing(_connect()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO articles (
                    source,
                    title,
                    published,
                    summary,
                    url,
                    body,
                    parser,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article.news.source,
                    article.news.title,
                    article.news.published,
                    article.news.summary,
                    article.news.url,
                    article.body,
                    article.parser,
                    _created_at(),
                ),
            )

            return cursor.rowcount == 1


def get_articles() -> list[Article]:
    """Return all saved articles."""
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            SELECT
                source,
                title,
                published,
                summary,
                url,
                body,
                parser
            FROM articles
            ORDER BY published DESC, id DESC
            """
        )

        return [_row_to_article(row) for row in cursor.fetchall()]


def get_articles_by_date(start_date: str, end_date: str) -> list[Article]:
    """Return articles saved between two dates in YYYY-MM-DD format."""
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            SELECT
                source,
                title,
                published,
                summary,
                url,
                body,
                parser
            FROM articles
            WHERE date(created_at) BETWEEN date(?) AND date(?)
            ORDER BY published DESC, id DESC
            """,
            (start_date, end_date),
        )

        return [_row_to_article(row) for row in cursor.fetchall()]


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_PATH)


def _created_at() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_article(row: tuple[str, str, str, str, str, str, str]) -> Article:
    news = News(
        source=row[0],
        title=row[1],
        published=row[2],
        summary=row[3],
        url=row[4],
    )

    return Article(
        news=news,
        body=row[5],
        parser=row[6],
    )
