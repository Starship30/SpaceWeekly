import requests

headers = {

    "User-Agent": (

        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

        "AppleWebKit/537.36 (KHTML, like Gecko) "

        "Chrome/137.0.0.0 Safari/537.36"

    )

}


def download(url):

    response = requests.get(

        url,

        headers=headers,

        timeout=10

    )

    return response.text