"""Local HTTP checks for routes, public assets, and shared settings."""

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bilibili_ds import config, state as settings
from bilibili_ds.web import assets, plots, routes, state, dataset, missions
from bilibili_ds.web.server import DashboardHTTPServer


class QuietHandler(routes.BilibiliDataScienceHandler):
    def log_message(self, *args):
        pass


class WebRouteTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("CREATORS_FILE", "COOKIE_FILE", "QRCODE_FILE", "LEGACY_CREDENTIAL_FILE", "ACTIVE_ACCOUNT_FILE", "ACCOUNTS_DIR", "PLOTS_DIR", "TRACKING_DB"):
            self.stack.enter_context(patch.object(config, name, root / name.lower()))
        self.stack.enter_context(patch.object(settings, "REQUEST_FREQUENCY", 4.0))
        self.stack.enter_context(patch.object(state, "SELECTED_UID", None))
        self.stack.enter_context(patch.object(state, "RELOAD_TOKEN", "test-token"))
        self.stack.enter_context(patch.object(routes, "account_records_summary", return_value=[]))
        self.server = DashboardHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.stack.callback(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, data=None, method=None):
        body = None if data is None else json.dumps(data).encode()
        request = Request(self.base + path, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_dashboard_and_every_browser_module_are_served(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'type="module" src="/static/js/app.js"', body)
        self.assertIn(b'content="test-token"', body)
        self.assertNotIn(b"<style>", body)
        for path in config.STATIC_DIR.rglob("*"):
            if not path.is_file() or path.suffix not in assets.STATIC_CONTENT_TYPES:
                continue
            with self.subTest(path=path):
                url = "/static/" + path.relative_to(config.STATIC_DIR).as_posix()
                status, headers, body = self.request(url)
                self.assertEqual(status, 200)
                self.assertEqual(body, path.read_bytes())
                self.assertEqual(headers["Content-Type"], assets.STATIC_CONTENT_TYPES[path.suffix])
                status, head_headers, head_body = self.request(url, method="HEAD")
                self.assertEqual(status, 200)
                self.assertEqual(head_headers["Content-Length"], str(len(body)))
                self.assertEqual(head_body, b"")

    def test_release_collection_route_clears_only_the_loaded_source(self):
        from tests.integration.test_tracking import info, TIME
        current = {'key': ('7', None, '{}'), 'data': ([info()], 'First video', 1),
                   'meta': {'count': 1, 'collected_at': TIME, 'uid': '7'}}
        with patch.object(dataset, 'CURRENT', current), patch.object(dataset, 'selected_creator', return_value={'uid': '7'}), patch.object(dataset.accounts, 'active_account_id', return_value=None):
            self.assertEqual(self.request('/api/collections/release', {'collection_id': 'creator', 'expected_collected_at': 'old'})[0], 400)
            self.assertIs(dataset.CURRENT, current)
            status, _, body = self.request('/api/collections/release', {'collection_id': 'creator', 'expected_collected_at': TIME})
            self.assertEqual((status, json.loads(body)), (200, {'released': 'creator'}))
            self.assertIsNone(dataset.CURRENT)
            self.assertEqual(self.request('/api/collections/release', {})[0], 400)

    def test_tracking_revision_route_changes_after_writes_and_is_stable_after_reads(self):
        status, _, body = self.request('/api/tracking/revision')
        self.assertEqual(status, 200)
        initial = json.loads(body)
        self.assertEqual(set(initial), {'revision'})
        self.assertEqual(json.loads(self.request('/api/tracking/revision')[2]), initial)
        self.request('/api/tracking/trackers', {'video': 'BV1xx411c7mD', 'interval_seconds': 600})
        updated = json.loads(self.request('/api/tracking/revision')[2])
        self.assertNotEqual(updated, initial)
        self.assertEqual(json.loads(self.request('/api/tracking')[2])['revision'], updated['revision'])
        self.assertEqual(json.loads(self.request('/api/tracking/revision')[2]), updated)

    def test_memory_route_reports_process_usage_without_returning_dataset_rows(self):
        with patch.object(dataset, 'CURRENT', None):
            status, _, body = self.request('/api/memory')
        report = json.loads(body)
        self.assertEqual(status, 200)
        self.assertGreater(report['server']['pid'], 0)
        self.assertGreater(report['server']['rss_bytes'], 0)
        self.assertIn('dataset_estimated_bytes', report)
        self.assertNotIn('data', report)

    def test_mission_routes_run_and_retain_independent_results(self):
        from collections import OrderedDict
        from tests.integration.test_tracking import info
        with ExitStack() as stack:
            for target, name, value in ((missions, 'MISSIONS', OrderedDict()), (missions, 'RUNNING', False),
                    (missions, 'STOP_REQUESTED', False), (missions, 'WORKER', None),
                    (dataset, 'MISSION_COLLECTIONS', OrderedDict())):
                stack.enter_context(patch.object(target, name, value))
            fetch = stack.enter_context(patch.object(missions.distributed_dataset, 'fetch_workspace_items',
                new_callable=AsyncMock, return_value=([info()], 'First video', 1, {'requested':1})))
            status, _, body = self.request('/api/missions/add', {'source':'fetch','creator_uid':'7',
                'selection':{'kind':'position','start':1,'end':1},'steps':['analysis']})
            self.assertEqual(status, 200)
            identity = json.loads(body)['missions'][0]['id']
            self.assertEqual(self.request('/api/missions/run', {})[0], 200)
            missions.WORKER.join(timeout=5)
            self.assertFalse(missions.WORKER.is_alive())
            fetch.assert_awaited_once()
            status, _, body = self.request('/api/missions/result?id='+identity)
            result = json.loads(body)
            self.assertEqual((status, result['state'], result['dataset']['uid']), (200,'completed','7'))
            self.assertEqual(result['videos'][0]['views'], 100)
            self.assertIn('analysis', result['results'])
            self.assertEqual(self.request('/api/missions/add', {'steps':['unknown']})[0],400)
            self.assertEqual(self.request('/api/missions/action',{'id':identity,'action':'remove'})[0],200)
            self.assertEqual(json.loads(self.request('/api/missions')[2])['missions'],[])

    def test_static_and_private_path_rejections(self):
        for path in ("/static/../README.md", "/static/%2e%2e/bilibili_ds/config.py", "/static/%2fetc/passwd",
                     "/static/missing.js", "/static/", "/static/.DS_Store", "/.runtime/bilibili_credential.json", "/bilibili_ds/config.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
                self.assertEqual(self.request(path, method="HEAD")[0], 404)

    def test_shared_request_rate_and_saved_creators(self):
        status, _, body = self.request("/api/request-frequency", {"value": 2})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"request_frequency": 2})
        self.assertEqual(settings.REQUEST_FREQUENCY, 2)
        self.assertEqual(json.loads(self.request("/api/health")[2])["request_frequency"], 2)
        self.assertEqual(self.request("/api/request-frequency", {"value": 0})[0], 400)
        self.assertEqual(self.request("/api/request-frequency", {"value": 8})[0], 400)
        status, _, body = self.request("/api/creators/add", {"name": "Example", "space": "https://space.bilibili.com/42"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["creator"]["uid"], "42")
        self.assertEqual(json.loads(self.request("/api/creators")[2])["selected_creator"]["uid"], "42")
        self.assertNotIn("selected_uid", json.loads(config.CREATORS_FILE.read_text()))

    def test_upstream_412_response_is_safe_and_does_not_replace_loaded_data(self):
        from bilibili_ds.errors import NetworkException
        original = {'data': 'already loaded dataset'}
        failure = NetworkException(412, '<html>private-cookie</html>').with_request_context('creator_videos', 'cookies')
        with patch.object(dataset, 'CURRENT', original), patch.object(routes, 'execute_video_action', new_callable=AsyncMock, side_effect=failure) as action:
            status, _, body = self.request('/api/video-action', {'action': 'list', 'refresh': True})
            self.assertIs(dataset.CURRENT, original)
        message = json.loads(body)['error']
        self.assertEqual(status, 400)
        self.assertIn('HTTP 412', message)
        self.assertIn('Request: Creator video list', message)
        self.assertIn('session cookie supplied (validity not checked)', message)
        self.assertIn('lower collection requests per second', message)
        self.assertNotIn('private-cookie', message)
        action.assert_awaited_once()

    def test_lookup_and_actions_use_expected_route_contracts(self):
        with patch.object(routes, "fetch_single_video", new_callable=AsyncMock, return_value={"bvid": "BV1xx411c7mD"}) as lookup:
            status, _, body = self.request("/api/video-lookup", {"video": "BV1xx411c7mD"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"video": {"bvid": "BV1xx411c7mD"}})
            lookup.assert_awaited_once_with("BV1xx411c7mD")
        with patch.object(routes, "execute_video_action", new_callable=AsyncMock, return_value={"count": 0}) as action:
            payload = {"action": "analysis", "selection": {"kind": "position", "start": 1, "end": 5}}
            self.assertEqual(self.request("/api/video-action", payload)[0], 200)
            action.assert_awaited_once_with(payload)
        self.assertEqual(self.request("/api/unknown", {})[0], 404)
        self.assertEqual(self.request("/api/selected-creator", {})[0], 400)

    def test_workspace_restore_is_read_only_and_not_browser_cached(self):
        snapshot = {'creator': {'videos': [{'bvid': 'A', 'views': 100}]}, 'reports': {}}
        with patch.object(dataset, 'workspace_data', return_value=snapshot), patch.dict(state.PROGRESS, running=True):
            status, headers, body = self.request('/api/workspace-data')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(body), {**snapshot, 'running': True})

    def test_tracking_routes_snapshot_history_export_and_backup(self):
        from bilibili_ds.web import tracking as service
        payload = {'video': 'BV1xx411c7mD', 'interval_seconds': 600}
        status, _, body = self.request('/api/tracking/trackers', payload)
        self.assertEqual(status, 200)
        tracker = json.loads(body)['tracker']
        self.assertEqual(self.request('/api/tracking/trackers', payload)[0], 400)
        self.assertEqual(self.request('/api/tracking/update', {'tracker_id': tracker['id'], 'status': 'paused'})[0], 200)
        with patch.object(service, 'fetch_info', new_callable=AsyncMock,
                          return_value={'bvid': payload['video'], 'title': 'Example', 'stat': {'view': 0, 'like': 0}}):
            status, _, body = self.request('/api/tracking/snapshot', {'video': payload['video'], 'tracker_id': tracker['id']})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['snapshot']['views'], 0)
        data = json.loads(self.request('/api/tracking')[2])
        self.assertEqual(data['counts']['snapshots'], 1)
        self.assertEqual(data['trackers'][0]['status'], 'paused')
        status, _, body = self.request('/api/tracking/history?bvid=BV1xx411c7mD')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['total'], 1)
        status, headers, body = self.request('/api/tracking/export?bvid=BV1xx411c7mD')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/csv'))
        self.assertIn('BV1xx411c7mD', body.decode('utf-8-sig'))
        self.assertEqual(self.request('/api/tracking/history?bvid=bad')[0], 400)
        self.assertEqual(self.request('/api/tracking/history?bvid=BV1xx411c7mD&limit=0')[0], 400)
        status, _, body = self.request('/api/tracking/backup', {})
        self.assertEqual(status, 200)
        self.assertTrue(Path(json.loads(body)['path']).is_file())
        self.assertEqual(self.request('/data/tracking.sqlite3')[0], 404)

    def test_random_sample_route_returns_collection_and_handles_errors(self):
        with patch.object(routes, 'fetch_random_sample', new_callable=AsyncMock, return_value={'sampling': {'sampled': 100}}) as sampling:
            payload = {'keyword': 'camera', 'sample_size': 100}
            status, _, body = self.request('/api/random-sample', payload)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['sampling']['sampled'], 100)
            sampling.assert_awaited_once_with(payload)
            sampling.side_effect = ValueError('Invalid sample')
            self.assertEqual(self.request('/api/random-sample', {})[0], 400)

    def test_creator_discovery_tracking_check_and_analysis_routes(self):
        from bilibili_ds.web import tracking as service
        from tests.integration.test_tracking import info, BVID
        status, _, body = self.request('/api/tracking/creators', {'creator': 'https://space.bilibili.com/7', 'video_interval_seconds': 120})
        self.assertEqual(status, 200)
        watch = json.loads(body)['watch']
        self.assertEqual(watch['uid'], '7')
        with patch.object(service, 'fetch_releases', new_callable=AsyncMock, return_value=[]):
            status, _, body = self.request('/api/tracking/creators/check', {'watch_id': watch['id']})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['baseline'])
        with patch.object(service, 'fetch_releases', new_callable=AsyncMock, return_value=[{'bvid': BVID, 'title': 'New release', 'created': 4070908800}]):
            status, _, body = self.request('/api/tracking/creators/check', {'watch_id': watch['id']})
        self.assertEqual(status, 200)
        tracker_id = json.loads(body)['releases'][0]['tracker_id']
        with patch.object(service, 'fetch_info', new_callable=AsyncMock, return_value=info(123)):
            status, _, body = self.request('/api/tracking/check', {'tracker_id': tracker_id})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['observation']['views'], 123)
        status, _, body = self.request(f'/api/tracking/analysis?bvid={BVID}&metric=likes')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['summary']['latest_value'], 0)
        self.assertEqual(json.loads(body)['metric'], 'likes')
        self.assertEqual(self.request('/api/tracking/creators/update', {'watch_id': watch['id'], 'status': 'paused'})[0], 200)
        self.assertEqual(json.loads(self.request('/api/tracking')[2])['creator_watches'][0]['status'], 'paused')
        for payload in ({'creator': 'https://example.com/7'}, {'creator': 'bad'}, {'creator': '7'}, {'creator': '8', 'interval_seconds': True}):
            self.assertEqual(self.request('/api/tracking/creators', payload)[0], 400)
        self.assertEqual(self.request('/api/tracking/check', {})[0], 400)
        self.assertEqual(self.request(f'/api/tracking/analysis?bvid={BVID}&metric=invalid')[0], 400)

    def test_whole_collection_snapshot_routes_and_scoped_csv(self):
        import csv
        import io
        from tests.integration.test_tracking import info, BVID, OTHER, TIME
        rows = [info(), {**info(200), 'bvid': OTHER}]
        meta = {'source_kind': 'creator', 'source_label': 'Creator 7', 'uid': '7', 'count': 2,
                'collected_at': TIME, 'selection': 'First 2', 'collection': {}}
        current = {'key': ('7', None, '{}'), 'data': (rows, 'First 2', 2), 'meta': meta}
        with patch.object(dataset, 'CURRENT', current), patch.object(dataset, 'context_key', return_value=('7', None, '{}')):
            status, _, body = self.request('/api/snapshots/collection', {'mode': 'loaded', 'collection_id': 'creator'})
        self.assertEqual(status, 200)
        batch = json.loads(body)['batch']
        self.assertEqual(batch['video_count'], 2)
        status, _, body = self.request(f"/api/snapshots/batch?id={batch['id']}")
        self.assertEqual(status, 200)
        self.assertEqual([row['bvid'] for row in json.loads(body)['videos']], [BVID, OTHER])
        status, headers, body = self.request(f"/api/snapshots/export?id={batch['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/csv'))
        exported = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        self.assertEqual([row['bvid'] for row in exported], [BVID, OTHER])
        self.assertEqual({int(row['batch_id']) for row in exported}, {batch['id']})
        self.assertEqual(len(json.loads(self.request('/api/snapshots')[2])['batches']), 1)
        self.assertEqual(json.loads(self.request('/api/tracking')[2])['counts']['observations'], 0)
        for invalid in ('', '0', 'bad', '999'):
            self.assertEqual(self.request(f'/api/snapshots/batch?id={invalid}')[0], 400)
            self.assertEqual(self.request(f'/api/snapshots/export?id={invalid}')[0], 400)
        self.assertEqual(self.request('/api/snapshots/collection', {'mode': 'loaded', 'collection_id': 'expired'})[0], 400)

    def test_weekly_analysis_route_returns_collection_and_handles_errors(self):
        with patch.object(routes, 'fetch_weekly_analysis', new_callable=AsyncMock, return_value={'counts': {'included': 50}}) as weekly:
            status, _, body = self.request('/api/weekly-analysis', {'source': '393'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['counts']['included'], 50)
            weekly.assert_awaited_once_with({'source': '393'})
            weekly.side_effect = ValueError('Invalid issue')
            self.assertEqual(self.request('/api/weekly-analysis', {'source': 'bad'})[0], 400)

    def test_plots_only_persist_after_explicit_save(self):
        points = [{"title": "Example", "label": "2026-09-01 12:00:00", "value": 12345}]
        plot_id = plots.prepare_plot({"name": "Example", "uid": "42"}, "all", "Views", "Views", points, collection_id='creator')
        self.assertFalse(config.PLOTS_DIR.exists())
        self.assertEqual(json.loads(self.request("/api/plots")[2]), {"plots": []})
        status, _, body = self.request("/api/plots/save", {"plot_id": plot_id})
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(len(list(config.PLOTS_DIR.glob("*.png"))), 1)
        self.assertTrue(self.request(saved["url"])[2].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(json.loads(self.request("/api/plots/save", {"plot_id": plot_id})[2]), saved)
        self.assertEqual(len(list(config.PLOTS_DIR.glob("*.png"))), 1)
        number_payload = {"plot_id": plot_id, "axis_mode": "number"}
        number_save = json.loads(self.request("/api/plots/save", number_payload)[2])
        self.assertNotEqual(number_save["name"], saved["name"])
        self.assertEqual(json.loads(self.request("/api/plots/save", number_payload)[2]), number_save)
        self.assertEqual(len(list(config.PLOTS_DIR.glob("*.png"))), 2)
        self.assertEqual(self.request("/api/plots/save", {"plot_id": plot_id, "axis_mode": "invalid"})[0], 400)
        ma_payload = {"plot_id": plot_id, "axis_mode": "number", "ma_periods": [5, 10]}
        ma_save = json.loads(self.request("/api/plots/save", ma_payload)[2])
        self.assertNotEqual(ma_save["name"], number_save["name"])
        self.assertEqual(json.loads(self.request("/api/plots/save", {**ma_payload, "ma_periods": [10, 5, 5]})[2]), ma_save)
        self.assertEqual(len(list(config.PLOTS_DIR.glob("*.png"))), 3)
        for invalid_periods in ([0], [True], ["5"], [1000000], "5"):
            self.assertEqual(self.request("/api/plots/save", {**ma_payload, "ma_periods": invalid_periods})[0], 400)
        indicator_payload = {**ma_payload, "indicators": ["ema10", "median5", "relative20"]}
        indicator_save = json.loads(self.request("/api/plots/save", indicator_payload)[2])
        self.assertNotEqual(indicator_save["name"], ma_save["name"])
        self.assertEqual(json.loads(self.request("/api/plots/save", {**indicator_payload, "indicators": ["relative20", "median5", "ema10", "ema10"]})[2]), indicator_save)
        self.assertEqual(len(list(config.PLOTS_DIR.glob("*.png"))), 4)
        for invalid in ([True], ["ema100"], "ema10", [20]):
            self.assertEqual(self.request("/api/plots/save", {**indicator_payload, "indicators": invalid})[0], 400)
        for invalid in (None, "", "expired", "../../unexpected.png", {"points": points}):
            self.assertEqual(self.request("/api/plots/save", {"plot_id": invalid})[0], 400)
