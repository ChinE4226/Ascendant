"""Upstream error codes, recovery advice and response redaction."""

import unittest

import httpx
from ascendant.errors import NetworkException, ResponseCodeException

from ascendant.errors import BilibiliRequestError, check_detail_rejection, public_error_message, request_error_message


class RequestErrorTests(unittest.TestCase):
    def test_http_and_api_rejections_keep_distinct_codes_without_raw_responses(self):
        for exc, identifier in ((NetworkException(412, '<html>private cookie</html>'), 'HTTP 412'),
                                (ResponseCodeException(-412, 'private cookie', {'token': 'secret'}), 'API code -412'),
                                (NetworkException(429, 'secret'), 'HTTP 429')):
            with self.subTest(identifier=identifier):
                message = public_error_message(exc)
                self.assertIn(identifier, message)
                self.assertIn('lower collection requests per second', message)
                self.assertNotIn('private cookie', message)
                self.assertNotIn('secret', message)

    def test_authentication_and_transport_failures_have_specific_guidance(self):
        self.assertIn('Sign in on the fetching Mac', request_error_message(NetworkException(401, 'secret')))
        self.assertIn('timed out', request_error_message(httpx.ReadTimeout('secret URL')))
        self.assertIn('internet connection', request_error_message(httpx.ConnectError('secret URL')))
        self.assertEqual(public_error_message(ValueError('Invalid sample size.')), 'Invalid sample size.')

    def test_detail_rejections_retain_codes_and_ignore_invalid_code_types(self):
        with self.assertRaises(BilibiliRequestError) as raised:
            check_detail_rejection({'bvid': 'BV1xx411c7mD', 'detail_error_status': 412, 'detail_error_code': -412})
        self.assertEqual((raised.exception.status, raised.exception.code), (412, -412))
        self.assertIn('BV1xx411c7mD', str(raised.exception))
        self.assertIn('HTTP 412', str(raised.exception))
        self.assertIn('API code -412', str(raised.exception))
        for code in ([], {}, True, '412'):
            check_detail_rejection({'detail_error_code': code, 'detail_error_status': code})

    def test_untrusted_request_context_is_not_echoed_in_detail_errors(self):
        with self.assertRaises(BilibiliRequestError) as raised:
            check_detail_rejection({'detail_error_status': 412,
                                    'detail_error_operation': 'https://example.com/?token=secret',
                                    'detail_error_credentials': {'cookie': 'secret'}})
        self.assertNotIn('secret', str(raised.exception))
        self.assertNotIn('example.com', str(raised.exception))
