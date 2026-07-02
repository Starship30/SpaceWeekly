from collections.abc import Callable

from models.article import Article
from models.news import News
from parsers.jpl import parse as parse_jpl
from parsers.science import parse as parse_science

Parser = Callable[[News, str], Article]
PARSERS: dict[str, Parser] = {
    "jpl.nasa.gov": parse_jpl,
    "science.nasa.gov": parse_science,
}


def get_parser(url: str) -> Parser | None:
    """Return the parser registered for a URL."""
    for domain, parser in PARSERS.items():
        if domain in url:
            return parser

    return None
