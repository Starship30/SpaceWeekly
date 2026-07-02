from collections.abc import Callable

from models.article import Article
from models.news import News
from parsers.cneos import parse as parse_cneos
from parsers.generic import parse as parse_generic
from parsers.jpl import parse as parse_jpl
from parsers.science import parse as parse_science

Parser = Callable[[News, str], Article]
PARSERS: dict[str, Parser] = {
    "cneos.jpl.nasa.gov": parse_cneos,
    "jpl.nasa.gov": parse_jpl,
    "science.nasa.gov": parse_science,
}


def get_parser(url: str) -> Parser:
    """Return the parser registered for a URL, falling back to Generic."""
    for domain, parser in PARSERS.items():
        if domain in url:
            return parser

    return parse_generic
