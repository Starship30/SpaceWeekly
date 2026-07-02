from bs4 import BeautifulSoup


def parse(html):

    soup = BeautifulSoup(

        html,

        "html.parser"

    )

    article = soup.find(

        "div",

        attrs={

            "itemprop": "articleBody"

        }

    )

    if article is None:

        return ""

    paragraphs = article.find_all("p")

    body = []

    for p in paragraphs:

        body.append(

            p.get_text(strip=True)

        )

    return "\n".join(body)