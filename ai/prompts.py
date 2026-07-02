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


def build_analysis_prompt(
    article: Article,
    summary_prompt: str = "",
    translation_prompt: str = "",
    category_prompt: str = "",
    score_prompt: str = "",
    filter_prompt: str = "",
) -> str:
    """Build a full intelligent analysis prompt."""
    return f"""
请分析下面的航天新闻，并只返回 JSON，不要输出 Markdown。
除 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词外，全部使用简体中文。

你需要返回：
- summary：中文摘要
- translation：正文中文译文
- keywords：中文关键词数组
- categories：分类数组，可多选
- importance：高、中、低
- score：0 到 100 的新闻价值评分
- keep：true 或 false，是否值得进入周报
- reason：保留或忽略的原因

可选分类：
火箭与发射、卫星、月球、火星、太阳、行星科学、小行星、深空探测、宇宙学、天文学、
空间站、载人航天、探测器、地球观测、空间科学、商业航天、航天政策、科普、娱乐、其它。

评分标准：
重大航天任务、重大科学发现、首次事件、重大政策、重大事故、重要论文评分高。
普通更新、摄影作品、每日天象、娱乐内容、广告、重复报道评分低。

自定义摘要 Prompt：
{summary_prompt}

自定义翻译 Prompt：
{translation_prompt}

自定义分类 Prompt：
{category_prompt}

自定义评分 Prompt：
{score_prompt}

自定义筛选 Prompt：
{filter_prompt}

新闻标题：
{article.news.title}

RSS 摘要：
{article.news.summary}

正文：
{article.body}
""".strip()
