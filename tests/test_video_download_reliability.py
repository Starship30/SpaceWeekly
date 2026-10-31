"""Bounded retries and candidate fallback without hiding platform errors."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from models.video_download import VideoSearchResults
from services import video_download as video
from services.cancellation import CancellationToken, TaskCancelledError
from tests.test_video_download_demo import candidate, WINDOW

URL = 'https://api.bilibili.com/x/web-interface/view'


def response(body=b'complete'):
    result = MagicMock()
    result.__enter__.return_value = result
    result.iter_content.return_value = [body]
    return result


class RequestReliabilityTests(unittest.TestCase):
    def test_timeout_retried_once_and_still_fails_after_second_attempt(self):
        session = MagicMock()
        session.get.side_effect = [requests.Timeout('temporary'), response()]
        with patch.object(video.time, 'sleep'):
            self.assertEqual(video._get(session, URL, lambda: None), 'complete')
        self.assertEqual(session.get.call_count, 2)
        session.reset_mock()
        session.get.side_effect = requests.Timeout('persistent')
        with patch.object(video.time, 'sleep'), self.assertRaises(requests.Timeout):
            video._get(session, URL, lambda: None)
        self.assertEqual(session.get.call_count, 2)

    def test_partial_failed_body_is_discarded_before_retry(self):
        session = MagicMock()
        partial = response()
        def chunks(*args):
            yield b'partial'
            raise requests.ConnectionError('interrupted')
        partial.iter_content.side_effect = chunks
        session.get.side_effect = [partial, response()]
        with patch.object(video.time, 'sleep'):
            self.assertEqual(video._get(session, URL, lambda: None), 'complete')
        partial.__exit__.assert_called_once()

    def test_platform_and_certificate_errors_are_not_retried(self):
        for error in (requests.HTTPError('HTTP 412'), requests.exceptions.SSLError('certificate')):
            session = MagicMock()
            session.get.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                video._get(session, URL, lambda: None)
            self.assertEqual(session.get.call_count, 1)

    def test_cancel_during_backoff_stops_retry(self):
        session = MagicMock()
        session.get.side_effect = requests.Timeout('temporary')
        token = CancellationToken()
        with patch.object(video.time, 'sleep', side_effect=lambda _: token.cancel()):
            with self.assertRaises(TaskCancelledError):
                video._get(session, URL, token.check)
        self.assertEqual(session.get.call_count, 1)

    def test_permission_errors_distinguish_timeout_network_platform_and_bad_json(self):
        http_response = requests.Response()
        http_response.status_code = 412
        cases = [(requests.Timeout('too slow'), 'timeout'),
                 (requests.ConnectionError('offline'), 'network'),
                 (requests.HTTPError('HTTP 412', response=http_response), 'platform')]
        for failure, expected in cases:
            with self.subTest(code=expected), patch.object(video, '_get', side_effect=failure):
                with self.assertRaises(video.VideoDownloadError) as error:
                    video._check_saving_allowed(candidate().url, lambda: None)
                self.assertEqual(error.exception.code, expected)
                self.assertIn('Saving-permission', error.exception.detail)
        with patch.object(video, '_get', return_value='<html>challenge</html>'):
            with self.assertRaises(video.VideoDownloadError) as error:
                video._check_saving_allowed(candidate().url, lambda: None)
        self.assertEqual(error.exception.code, 'permission')


class DemoReliabilityTests(unittest.TestCase):
    def test_parse_timeout_retries_once_with_longer_deadline_then_downloads(self):
        info = candidate()
        progress = []
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([info])), \
             patch.object(video, 'parse_video', side_effect=[video.VideoDownloadError('timeout'), info]) as parse, \
             patch.object(video, 'preferred_format', return_value=info.formats[0]), \
             patch.object(video, 'download_video', return_value=Path('video.mp4')) as download:
            result = video.demo_download('mission', WINDOW, Path('workspace'), on_progress=progress.append)
        self.assertEqual(result, Path('video.mp4'))
        self.assertEqual(parse.call_count, 2)
        self.assertEqual(parse.call_args.kwargs, {'timeout': 180})
        self.assertIn('retrying', [event.phase for event in progress])
        download.assert_called_once()

    def test_parse_retry_is_bounded_across_three_candidates_and_keeps_diagnostic(self):
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([candidate()]*4)), \
             patch.object(video, 'parse_video', side_effect=video.VideoDownloadError('timeout', 'slow metadata')) as parse, \
             patch.object(video, 'download_video') as download:
            with self.assertRaises(video.VideoDownloadError) as error:
                video.demo_download('mission', WINDOW, Path('workspace'))
        self.assertEqual(parse.call_count, 4)  # Three candidates plus one retry per task.
        self.assertEqual(error.exception.code, 'demo_candidates')
        self.assertIn('parsing', error.exception.detail)
        self.assertIn('slow metadata', error.exception.detail)
        download.assert_not_called()

    def test_download_failure_tries_next_candidate_and_reports_stage(self):
        first, second = candidate('first'), candidate('second')
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([first, second])), \
             patch.object(video, 'parse_video', side_effect=[first, second]), \
             patch.object(video, 'preferred_format', return_value=first.formats[0]), \
             patch.object(video, 'download_video', side_effect=[video.VideoDownloadError('backend', 'read timed out'),
                                                               Path('second.mp4')]) as download:
            self.assertEqual(video.demo_download('mission', WINDOW, Path('workspace')), Path('second.mp4'))
        self.assertEqual(download.call_count, 2)
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([first])), \
             patch.object(video, 'parse_video', return_value=first), \
             patch.object(video, 'preferred_format', return_value=first.formats[0]), \
             patch.object(video, 'download_video', side_effect=video.VideoDownloadError('backend', 'read timed out')):
            with self.assertRaises(video.VideoDownloadError) as error:
                video.demo_download('mission', WINDOW, Path('workspace'))
        self.assertIn('downloading', error.exception.detail)

    def test_platform_rejection_does_not_retry_candidates(self):
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([candidate()]*3)), \
             patch.object(video, 'parse_video', side_effect=video.VideoDownloadError('platform', 'HTTP 412')) as parse:
            with self.assertRaises(video.VideoDownloadError) as error:
                video.demo_download('mission', WINDOW, Path('workspace'))
        self.assertEqual(error.exception.code, 'platform')
        self.assertIn('parsing', error.exception.detail)
        parse.assert_called_once()

    def test_cancel_before_parse_retry_or_during_download_propagates(self):
        token = CancellationToken()
        def timeout(*args, **kwargs):
            token.cancel()
            raise video.VideoDownloadError('timeout')
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([candidate()])), \
             patch.object(video, 'parse_video', side_effect=timeout) as parse:
            with self.assertRaises(TaskCancelledError):
                video.demo_download('mission', WINDOW, Path('workspace'), cancel_check=token.check)
        parse.assert_called_once()
        info = candidate()
        with patch.object(video, 'search_videos', return_value=VideoSearchResults([info]*3)), \
             patch.object(video, 'parse_video', return_value=info), \
             patch.object(video, 'preferred_format', return_value=info.formats[0]), \
             patch.object(video, 'download_video', side_effect=TaskCancelledError) as download:
            with self.assertRaises(TaskCancelledError):
                video.demo_download('mission', WINDOW, Path('workspace'))
        download.assert_called_once()

    def test_backend_http_rejection_is_classified_as_platform(self):
        for status in (403, 412, 429, 404):
            code = f"print('ERROR: HTTP Error {status}: failure'); raise SystemExit(1)"
            with self.subTest(status=status), self.assertRaises(video.VideoDownloadError) as error:
                video._run_process([sys.executable, '-c', code], lambda: None, lambda line: None, timeout=5)
            self.assertEqual(error.exception.code, 'backend' if status == 404 else 'platform')

    def test_process_timeout_keeps_safe_backend_diagnostics(self):
        code = "import time; print('metadata wait https://example.test/?token=SECRET', flush=True); time.sleep(30)"
        with self.assertRaises(video.VideoDownloadError) as error:
            video._run_process([sys.executable, '-c', code], lambda: None, lambda line: None, timeout=.8)
        self.assertEqual(error.exception.code, 'timeout')
        self.assertIn('metadata wait', error.exception.detail)
        self.assertNotIn('SECRET', error.exception.detail)


if __name__ == '__main__':
    unittest.main()
