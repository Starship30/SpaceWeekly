from models.article import Article


def build_summary_prompt(article: Article) -> str:
    """Build the prompt used by AI summary providers."""
    return f"""
请阅读下面的航天新闻，并只返回 JSON，不要输出 Markdown。

JSON 字段：
- summary：中文摘要，150 到 250 字
- keywords：关键词数组，3 到 8 个
- category：分类，例如 发射、深空探测、行星防御、空间站、商业航天、天文学
- importance：重要程度，只能是 Low、Medium、High

新闻标题：
{article.news.title}

RSS 摘要：
{article.news.summary}

正文：
{article.body}
""".strip()
