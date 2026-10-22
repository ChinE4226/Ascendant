"""Exercise real HTTPX requests and the application adapter against offline responses."""

import asyncio
from contextlib import ExitStack
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import httpx

from ascendant import client, videos
from ascendant.api import Credential, hot, search, user, video
from ascendant.api.credentials import NAV_URL
from ascendant.api.login import QrCodeLogin, QrCodeLoginEvents
from ascendant.api.signing import mixin_key, sign_params
from ascendant.errors import BilibiliRequestError, InvalidResponseError, check_detail_rejection
from ascendant.fetch_context import REQUEST_DELAY

IMG = '7cd084941338484aae1ad9425b84077c'
SUB = '4932caff0ff746eab6f01bf08b70ac45'
NAV = {'isLogin': False, 'wbi_img': {'img_url': f'https://i0.hdslb.com/bfs/wbi/{IMG}.png',
                                   'sub_url': f'https://i0.hdslb.com/bfs/wbi/{SUB}.png'}}
INFO = {'bvid': 'BV1xx411c7mD', 'title': 'Example', 'stat': {'view': 42}}


class HttpxAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        token = REQUEST_DELAY.set(0)
        self.addCleanup(REQUEST_DELAY.reset, token)
        self.requests = []
        self.responses = {}
        real_client = httpx.AsyncClient
        self.stack.enter_context(patch.object(client.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
            **kw, transport=httpx.MockTransport(self.respond))))
        self.addAsyncCleanup(client.close_bilibili_client)

    def respond(self, request):
        self.requests.append(request)
        response = self.responses.get(request.url.path)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx.Response):
            return response
        if request.url.path == '/x/web-interface/nav' and response is None:
            return httpx.Response(200, json={'code': -101, 'data': NAV})
        if request.url.host == 'space.bilibili.com':
            return httpx.Response(200, text='<script id="__RENDER_DATA__" type="application/json">{"access_id":"example-webid"}</script>')
        return httpx.Response(200, json={'code': 0, 'data': response})

    async def test_creator_pagination_signing_and_details_use_selected_account(self):
        self.responses['/x/space/wbi/arc/search'] = {'page': {'count': 31}, 'list': {'vlist': [{'bvid': INFO['bvid']}]}}
        self.responses['/x/web-interface/view'] = INFO
        credential = Credential(sessdata='account-secret', dedeuserid='42')
        uploader = user.User(42, credential=credential)
        for page in (1, 2):
            summaries = await videos.fetch_creator_video_page(uploader, pn=page, ps=30, order=user.VideoOrder.PUBDATE)
            item = await videos.fetch_video_detail(summaries['list']['vlist'][0], credential)
            self.assertEqual(item['stat']['view'], 42)
        pages = [r for r in self.requests if r.url.path == '/x/space/wbi/arc/search']
        self.assertEqual([r.url.params['pn'] for r in pages], ['1', '2'])
        self.assertEqual(pages[0].url.params['w_webid'], 'example-webid')
        for request in pages:
            params = dict(request.url.params)
            signature = params.pop('w_rid')
            self.assertEqual(signature, hashlib.md5((urlencode(sorted(params.items())) + mixin_key(NAV)).encode()).hexdigest())
        self.assertEqual(sum(r.url.path == '/x/web-interface/nav' for r in self.requests), 1)
        self.assertEqual(sum(r.url.host == 'space.bilibili.com' for r in self.requests), 1)
        self.assertTrue(all('SESSDATA=account-secret' in r.headers['cookie'] for r in self.requests))

    async def test_search_filters_and_weekly_response_contracts(self):
        self.responses['/x/web-interface/wbi/search/type'] = {'result': [INFO]}
        self.responses['/x/web-interface/popular/series/one'] = {'config': {'number': 393}, 'list': [INFO]}
        await search.search_by_type(keyword='data science', time_start='2026-10-01', time_end='2026-10-03',
                                    video_zone_type=17, page=2, credential=Credential(sessdata='chosen'))
        params = self.requests[-1].url.params
        self.assertEqual((params['tids'], params['page'], params['pubtime_begin_s']), ('17', '2', '1790784000'))
        self.assertEqual((await hot.get_weekly_hot_videos(393))['list'], [INFO])
        self.assertEqual(self.requests[-1].url.params['number'], '393')
        self.assertEqual(self.requests[-1].url.params['web_location'], '333.934')
        self.assertIn('w_rid', self.requests[-1].url.params)
        self.assertNotIn('SESSDATA', self.requests[-1].headers.get('cookie', ''))

    async def test_rejections_and_transport_failures_stop_without_retry_or_secret_leaks(self):
        path = '/x/web-interface/view'
        for response, phrase in ((httpx.Response(412, text='private-cookie'), 'HTTP 412'),
                                 (httpx.Response(200, json={'code': -412, 'message': 'private-cookie'}), 'API code -412'),
                                 (httpx.Response(200, json={'code': -403}), 'API code -403'),
                                 (httpx.ReadTimeout('private-cookie'), 'timed out')):
            self.responses[path] = response
            before = len(self.requests)
            item = await videos.fetch_video_detail({'bvid': INFO['bvid']}, None)
            with self.assertRaisesRegex(BilibiliRequestError, phrase) as raised:
                check_detail_rejection(item)
            self.assertNotIn('private-cookie', str(raised.exception))
            self.assertEqual(len(self.requests), before + 1)

    async def test_invalid_responses_stop_collection_instead_of_becoming_empty_results(self):
        for response in (httpx.Response(200, text='<html>secret</html>'),
                         httpx.Response(200, json={'code': True, 'data': {}}),
                         httpx.Response(200, json={'code': 0, 'data': []}),
                         httpx.Response(200, json={'code': 0, 'data': {}})):
            self.responses['/x/web-interface/view'] = response
            item = await videos.fetch_video_detail({'bvid': INFO['bvid']}, None)
            with self.assertRaisesRegex(BilibiliRequestError, 'invalid response'):
                check_detail_rejection(item)
        self.responses['/x/space/wbi/arc/search'] = {'list': {'vlist': 'invalid'}}
        with self.assertRaises(InvalidResponseError):
            await user.User(42).get_videos()

    async def test_http_redirects_and_foreign_hosts_are_not_followed(self):
        self.responses['/x/web-interface/view'] = httpx.Response(302, headers={'Location': 'https://example.com/'})
        with self.assertRaisesRegex(BilibiliRequestError, 'HTTP 302'):
            await video.Video(bvid=INFO['bvid'], credential=Credential(sessdata='secret')).get_info()
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(ValueError):
            await client.request_data('https://example.com/', credential=Credential(sessdata='secret'))
        self.assertEqual(len(self.requests), 1)

    async def test_account_validation_and_profile(self):
        credential = Credential(sessdata='test', bili_jct='csrf', dedeuserid='42')
        self.assertFalse(await credential.check_valid())
        self.responses['/x/web-interface/nav'] = httpx.Response(200, json={'code': 0, 'data': {'isLogin': True}})
        self.assertTrue(await credential.check_valid())
        self.responses['/x/space/myinfo'] = {'mid': 42, 'name': 'Example'}
        self.assertEqual((await user.get_self_info(credential))['mid'], 42)
        self.assertNotIn('test', repr(credential))

    async def test_qr_states_and_url_cookie_parsing_with_cleanup(self):
        self.responses['/x/passport-login/web/qrcode/generate'] = {'url': 'https://passport.bilibili.com/login?key=test', 'qrcode_key': 'test'}
        login = QrCodeLogin()
        await login.generate_qrcode()
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'qrcode.png'
            login.save_qrcode(image)
            self.assertTrue(image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(client._sessions, {})
        for code, event in ((86101, QrCodeLoginEvents.SCAN), (86090, QrCodeLoginEvents.CONF), (86038, QrCodeLoginEvents.TIMEOUT)):
            self.responses['/x/passport-login/web/qrcode/poll'] = {'code': code}
            self.assertEqual(await login.check_state(), event)
            self.assertEqual(client._sessions, {})
        self.responses['/x/passport-login/web/qrcode/poll'] = {'code': 0,
            'url': 'https://passport.bilibili.com/?SESSDATA=one%2Ctwo&bili_jct=csrf&DedeUserID=42', 'refresh_token': 'refresh'}
        self.assertEqual(await login.check_state(), QrCodeLoginEvents.DONE)
        self.assertEqual(login.get_credential().sessdata, 'one%2Ctwo')
        self.assertEqual(login.get_credential().ac_time_value, 'refresh')
        self.assertEqual(client._sessions, {})
        self.responses['/x/passport-login/web/qrcode/poll'] = {'code': -412, 'url': 'private-cookie'}
        with self.assertRaisesRegex(BilibiliRequestError, 'API code -412'):
            await login.check_state()
        self.assertEqual(client._sessions, {})

    def test_signing_is_deterministic_and_filters_special_characters(self):
        self.assertEqual(mixin_key(NAV), 'ea1db124af3c7062474693fa704f4ff8')
        params = {'foo': "hello!'()*", 'w_rid': 'stale'}
        signed = sign_params(params, mixin_key(NAV), timestamp=1702204169)
        self.assertEqual(signed['foo'], 'hello')
        self.assertEqual(signed['wts'], '1702204169')
        self.assertNotEqual(signed['w_rid'], 'stale')
        self.assertEqual(params['w_rid'], 'stale')

    async def test_http_status_and_api_code_are_independent_and_never_default_to_412(self):
        url = 'https://api.bilibili.com/x/web-interface/view'
        path = '/x/web-interface/view'
        cases = ((httpx.Response(401, text='secret'), 401, None),
                 (httpx.Response(403, text='secret'), 403, None),
                 (httpx.Response(404, text='secret'), 404, None),
                 (httpx.Response(429, text='secret'), 429, None),
                 (httpx.Response(503, text='secret'), 503, None),
                 (httpx.Response(412, json={'code': -352, 'message': 'secret'}), 412, -352),
                 (httpx.Response(200, json={'code': -101, 'message': 'secret'}), 200, -101),
                 (httpx.Response(200, json={'code': -412, 'message': 'secret'}), 200, -412))
        for response, status, code in cases:
            with self.subTest(status=status, code=code):
                self.responses[path] = response
                before = len(self.requests)
                with self.assertRaises(BilibiliRequestError) as raised:
                    await client.request_data(url)
                error = raised.exception
                self.assertEqual((error.status, error.code), (status, code))
                self.assertIn(f'HTTP {status}', str(error))
                self.assertIn('Request: Video details', str(error))
                self.assertIn('Credentials: guest', str(error))
                self.assertNotIn('secret', str(error))
                if code is not None:
                    self.assertIn(f'API code {code}', str(error))
                if status != 412:
                    self.assertNotIn('HTTP 412', str(error))
                self.assertEqual(len(self.requests), before + 1)

    async def test_prerequisite_failure_names_the_actual_request_and_never_attempts_upload_api(self):
        self.responses['/42/dynamic'] = httpx.Response(412, text='<html>secret body</html>')
        with self.assertRaises(BilibiliRequestError) as raised:
            await videos.fetch_creator_video_page(user.User(42), pn=1, ps=30, order=user.VideoOrder.PUBDATE)
        message = str(raised.exception)
        self.assertIn('page 1', message)
        self.assertIn('Request: Creator page access ID', message)
        self.assertIn('HTTP 412', message)
        self.assertIn('does not establish the exact cause', message)
        self.assertNotIn('secret body', message)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, '/42/dynamic')

    async def test_navigation_failure_is_distinct_from_search_failure(self):
        self.responses['/x/web-interface/nav'] = httpx.Response(503, text='secret body')
        with self.assertRaises(BilibiliRequestError) as raised:
            await search.search_by_type(keyword='private-search-words')
        self.assertIn('Request: Account/navigation metadata', str(raised.exception))
        self.assertIn('HTTP 503', str(raised.exception))
        self.assertNotIn('HTTP 412', str(raised.exception))
        self.assertNotIn('private-search-words', str(raised.exception))
        self.assertEqual(len(self.requests), 1)

    async def test_transport_failure_has_context_without_fabricating_http_status(self):
        self.responses['/x/web-interface/view'] = httpx.ConnectError('https://example.invalid/?token=secret')
        credential = Credential(sessdata='secret-cookie')
        item = await videos.fetch_video_detail({'bvid': INFO['bvid']}, credential)
        self.assertIsNone(item['detail_error_status'])
        self.assertIsNone(item['detail_error_code'])
        with self.assertRaises(BilibiliRequestError) as raised:
            check_detail_rejection(item)
        message = str(raised.exception)
        self.assertIn('Request: Video details', message)
        self.assertIn('session cookie supplied (validity not checked)', message)
        self.assertNotIn('HTTP', message)
        self.assertNotIn('secret', message)

    async def test_other_http_and_unknown_api_errors_are_not_silently_skipped(self):
        for response in (httpx.Response(400, text='secret'),
                         httpx.Response(200, json={'code': -9999, 'message': 'secret'})):
            self.responses['/x/web-interface/view'] = response
            item = await videos.fetch_video_detail({'bvid': INFO['bvid']}, None)
            with self.assertRaises(BilibiliRequestError) as raised:
                check_detail_rejection(item)
            self.assertNotIn('HTTP 412', str(raised.exception))
            self.assertNotIn('secret', str(raised.exception))
