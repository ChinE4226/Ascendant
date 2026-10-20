"""HTTPX session ownership across repeated operations; no network requests."""

import asyncio
import gc
import unittest
import weakref
from unittest.mock import AsyncMock, patch

import httpx

from bilibili_ds import client
from bilibili_ds.api import Credential


class ClientCleanupTests(unittest.TestCase):
    def setUp(self):
        # Use real clients with an offline transport, not real socket pools.
        real_client = httpx.AsyncClient
        self.factory = patch.object(client.httpx, 'AsyncClient', side_effect=lambda **kw: real_client(
            **kw, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'code': 0, 'data': {}}))))
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.addCleanup(lambda: self.assertEqual(client._sessions, {}))

    def test_repeated_operations_release_clients_and_event_loops(self):
        references = []

        async def check():
            session = client.get_session()
            references.append((weakref.ref(asyncio.get_running_loop()), weakref.ref(session.http)))
            await client.close_bilibili_client()
            self.assertTrue(session.http.is_closed)

        for _ in range(500):
            asyncio.run(check())
        gc.collect()
        self.assertTrue(all(loop() is None and session() is None for loop, session in references))

    def test_cleanup_does_not_create_an_unused_client(self):
        asyncio.run(client.close_bilibili_client())
        self.assertEqual(client._sessions, {})

    def test_other_loops_are_preserved_and_same_loop_can_fetch_again(self):
        other_loop = asyncio.new_event_loop()
        self.addCleanup(other_loop.close)
        async def create():
            return client.get_session()
        other = other_loop.run_until_complete(create())

        async def check():
            first = client.get_session()
            await client.close_bilibili_client()
            second = client.get_session()
            self.assertIsNot(first, second)
            self.assertFalse(second.http.is_closed)
            await client.close_bilibili_client()

        asyncio.run(check())
        self.assertFalse(other.http.is_closed)
        self.assertIn(other_loop, client._sessions)
        other_loop.run_until_complete(client.close_bilibili_client())

    def test_accounts_and_guest_have_separate_cookie_jars(self):
        async def check():
            a = client.get_session(Credential(sessdata='one'))
            b = client.get_session(Credential(sessdata='two'))
            guest = client.get_session()
            self.assertIsNot(a, b)
            self.assertEqual(a.http.cookies.get('SESSDATA'), 'one')
            self.assertEqual(b.http.cookies.get('SESSDATA'), 'two')
            self.assertNotIn('SESSDATA', guest.http.cookies)
            await client.close_bilibili_client()
            self.assertTrue(all(session.http.is_closed for session in (a, b, guest)))
        asyncio.run(check())

    def test_failed_close_still_closes_other_accounts_and_evicts_loop(self):
        async def check():
            a, b = client.get_session(), client.get_session(Credential(sessdata='two'))
            await a.http.aclose()
            with patch.object(a.http, 'aclose', new_callable=AsyncMock, side_effect=RuntimeError('close failed')):
                await client.close_bilibili_client()
            self.assertTrue(b.http.is_closed)
        asyncio.run(check())

    def test_cancelled_request_closes_transport_in_finally(self):
        async def check():
            session = client.get_session()
            async def cancel(*args, **kwargs):
                raise asyncio.CancelledError
            with patch.object(session.http, 'get', side_effect=cancel):
                try:
                    with self.assertRaises(asyncio.CancelledError):
                        await client.request_data('https://api.bilibili.com/offline')
                finally:
                    await client.close_bilibili_client()
            self.assertTrue(session.http.is_closed)
        asyncio.run(check())
