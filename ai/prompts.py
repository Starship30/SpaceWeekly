from models.article import Article


def build_summary_prompt(article: Article) -> str:
    """Build the Chinese AI summary prompt."""
    return f"""
请阅读下面的航天新闻，并只返回 JSON，不要输出 Markdown。

除 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词外，
所有内容必须使用简体中文。

JSON 字段：
- summary：中文摘要，150 到 250 字
- keywords：关键词数组，3 到 8 个，必须使用中文
- category：中文分类，例如 发射、深空探测、行星防御、空间站、商业航天、天文学
- importance：重要程度，只能是 高、中、低

新闻标题：
{article.news.title}

RSS 摘要：
{article.news.summary}

正文：
{article.body}
""".strip()


def build_translation_prompt(article: Article) -> str:
    """Build the Chinese full-text translation prompt."""
    return f"""
请将下面的英文航天新闻正文翻译为简体中文。

要求：
- 保留 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词
- 不要添加原文没有的信息
- 按原文段落换行
- 只输出中文译文，不要输出 Markdown

标题：
{article.news.title}

正文：
{article.body}
""".strip()
