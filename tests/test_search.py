import unittest
from dataclasses import replace
from urllib.error import HTTPError
from unittest.mock import patch
from translator import search
from services.settings import default_settings


class SearchChecks(unittest.TestCase):
    def test_news_provider_disables_wikipedia_but_term_search_keeps_existing_fallback(self):
        settings=replace(default_settings(),web_search_provider='duckduckgo')
        self.assertIsInstance(search.create_search_provider(settings),search.FallbackSearchProvider)
        self.assertIsInstance(search.create_search_provider(settings,allow_wikipedia_fallback=False),search.DuckDuckGoSearchProvider)
        with self.assertRaises(search.SearchProviderError):
            search.create_search_provider(replace(settings,web_search_provider='wikipedia'),allow_wikipedia_fallback=False)

    def test_http_429_distinct_from_empty_results_and_challenge_page(self):
        for provider in [search.DuckDuckGoSearchProvider(),search.BraveSearchProvider('fake-test-key')]:
            with patch.object(search,'_http_get',side_effect=HTTPError('https://example.com',429,'rate limited',{},None)):
                with self.assertRaises(search.SearchRateLimitError):provider.search('sample')
        with patch.object(search,'_http_get',return_value='<form id="challenge-form"></form>'):
            with self.assertRaises(search.SearchRateLimitError):search.DuckDuckGoSearchProvider().search('sample')
        with patch.object(search,'_http_get',return_value='<html>No search results</html>'):
            with self.assertRaises(search.SearchProviderError) as caught:search.DuckDuckGoSearchProvider().search('sample')
        self.assertNotIsInstance(caught.exception,search.SearchRateLimitError)

    def test_ordinary_results_still_parse_without_wikipedia_request(self):
        html='<div class="result results_links"><a class="result__a" href="https://www.nasa.gov/test">NASA result</a><a class="result__snippet">A test announcement.</a></div>'
        settings=replace(default_settings(),web_search_provider='duckduckgo')
        with patch.object(search,'_http_get',return_value=html) as get:
            results=search.create_search_provider(settings,allow_wikipedia_fallback=False).search('NASA result')
        get.assert_called_once()
        self.assertEqual((results[0].title,results[0].url,results[0].official),('NASA result','https://www.nasa.gov/test',True))

if __name__=='__main__':unittest.main()
