REQUEST_TIMEOUT = 10
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
}


def download(url):
    import requests

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.encoding = _detect_encoding(response)

    return response.text


def _detect_encoding(response) -> str:
    encoding = response.encoding

    if not encoding or encoding.lower() == "iso-8859-1":
        apparent_encoding = response.apparent_encoding

        if apparent_encoding:
            return apparent_encoding

    return encoding or "utf-8"
