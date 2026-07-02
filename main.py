from feeds.cneos import get_news
from downloader.client import download
from parsers.jpl import parse

news = get_news()

print(f"共获取 {len(news)} 条新闻\n")

for item in news:

    print("=" * 80)

    print(item.title)
    print(item.published)

    print()

    print(item.summary)

    print()

    print(item.url)

    print()

    if "jpl.nasa.gov" in item.url:

        html = download(item.url)

        body = parse(html)

        print(body[:1000])

        print()