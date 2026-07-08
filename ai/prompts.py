from models.article import Article

FIXED_CATEGORIES = [
    "火箭与发射",
    "卫星",
    "月球",
    "火星",
    "太阳",
    "行星科学",
    "小行星",
    "深空探测",
    "宇宙学",
    "天文学",
    "空间站",
    "载人航天",
    "探测器",
    "地球观测",
    "空间科学",
    "商业航天",
    "航天政策",
    "科普",
    "娱乐",
    "其它",
]


def build_summary_prompt(article: Article) -> str:
    """Build the Chinese AI summary prompt."""
    return f"""
请阅读下面的航天新闻，并只返回严格 JSON。
除 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词外，全部使用简体中文。

JSON 字段：
- summary：中文摘要，150 到 250 字
- keywords：中文关键词数组，3 到 8 个
- category：中文分类，必须从固定分类表选择
- importance：重要程度，只能是 高、中、低

固定分类表：
{", ".join(FIXED_CATEGORIES)}

新闻标题：{article.news.title}
RSS 摘要：{article.news.summary}
正文：{article.body}
""".strip()


def build_translation_prompt(article: Article) -> str:
    """Build the Chinese full-text translation prompt."""
    return f"""
请将下面的英文航天新闻正文翻译为简体中文。
要求：
- 保留 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词
- 不要添加原文没有的信息
- 按原文段落换行
- 只输出中文译文

标题：{article.news.title}
正文：{article.body}
""".strip()


def build_analysis_prompt(
    article: Article,
    summary_prompt: str = "",
    translation_prompt: str = "",
    category_prompt: str = "",
    score_prompt: str = "",
    filter_prompt: str = "",
) -> str:
    """Build a strict JSON intelligent analysis prompt."""
    return f"""
请分析下面的航天新闻，并只返回严格 JSON。
除 NASA、ESA、JPL、SpaceX、Falcon 9、Artemis 等专有名词外，全部使用简体中文。

必须返回以下 JSON 字段：
- summary：中文摘要
- translation：正文中文译文
- keywords：中文关键词数组
- categories：分类数组，可多选，但必须完全来自固定分类表
- importance：只能是 High、Medium、Low
- keep：true 或 false，是否值得进入周报
- reason：保留或忽略的原因

固定分类表：
{", ".join(FIXED_CATEGORIES)}

重要程度标准：
High：重大航天任务、重大科学发现、首次事件、重大政策、重大事故、重要论文。
Medium：普通但有阅读价值的任务更新、研究进展、机构动态、科普素材。
Low：日常更新、摄影作品、每日天象、娱乐内容、广告、重复报道。

如果自定义 Prompt 非空，优先遵循对应规则：
摘要 Prompt：{summary_prompt}
翻译 Prompt：{translation_prompt}
分类 Prompt：{category_prompt}
重要程度 Prompt：{score_prompt}
筛选 Prompt：{filter_prompt}

新闻标题：{article.news.title}
新闻来源：{article.news.source}
发布时间：{article.news.published}
链接：{article.news.url}
RSS 摘要：{article.news.summary}
正文：{article.body}
""".strip()
