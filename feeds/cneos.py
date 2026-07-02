# feeds/cneos.py

import feedparser
from bs4 import BeautifulSoup

from models.news import News

RSS_URL = "https://cneos.jpl.nasa.gov/feed/news.xml"


def get_news():

    feed = feedparser.parse(RSS_URL)

    news_list = []

    for item in feed.entries:

        # 跳过 news.js
        if item.link.endswith(".js"):
            continue

        summary = BeautifulSoup(
            item.summary,
            "html.parser"
        ).get_text(strip=True)

        news = News(
            source="CNEOS",
            title=item.title,
            published=item.published,
            summary=summary,
            url=item.link
        )

        news_list.append(news)

    return news_list