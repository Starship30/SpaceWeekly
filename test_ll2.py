import json

from feeds.launches import get_launches
from feeds.launches import last_error
from feeds.launches import last_first_launch_json


def main() -> None:
    launches = get_launches()
    print(f"获取数量: {len(launches)}")

    error = last_error()

    if error:
        print(f"错误: {error}")

    raw_launch = last_first_launch_json()
    print("第一条完整 JSON:")
    print(json.dumps(raw_launch, ensure_ascii=False, indent=2, default=str))

    if not launches:
        return

    article = launches[0]
    print("转换后的 Article.title:")
    print(article.news.title)
    print("Article.summary:")
    print(article.news.summary)
    print("Article.body:")
    print(article.body)


if __name__ == "__main__":
    main()
