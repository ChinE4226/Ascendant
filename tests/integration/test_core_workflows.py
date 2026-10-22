"""Offline integration checks for the extracted application modules."""

from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from ascendant.api import Credential, user

from ascendant import accounts, client, config, plotting, state, storage, videos
from ascendant.web import creators, state as web_state, videos as web_videos


ROOT = Path(__file__).resolve().parents[2]


class IsolatedFilesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("CREATORS_FILE", "COOKIE_FILE", "QRCODE_FILE", "LEGACY_CREDENTIAL_FILE", "ACTIVE_ACCOUNT_FILE", "ACCOUNTS_DIR", "PLOTS_DIR"):
            self.stack.enter_context(patch.object(config, name, self.root / name.lower()))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch.object(state, "REQUEST_FREQUENCY", 4.0))
        self.stack.enter_context(patch.object(web_state, "SELECTED_UID", None))

    def test_web_storage_selection_and_paths(self):
        storage.write_json(config.CREATORS_FILE, {"selected_uid": "2", "creators": [{"uid": 1}, {"uid": 2}]})
        self.assertEqual(creators.selected_creator()["uid"], "1")
        self.assertEqual(creators.select_creator_by_uid("2")["uid"], "2")
        self.assertEqual(web_state.SELECTED_UID, "2")
        creators.add_web_creator("Third", "https://space.bilibili.com/3")
        self.assertNotIn("selected_uid", storage.read_json(config.CREATORS_FILE))
        self.assertEqual(creators.selected_creator()["uid"], "3")
        self.assertEqual(len(creators.load_web_creators()), 3)
        self.assertEqual(config.PROJECT_ROOT, ROOT)

    def test_account_save_load_precedence_and_cleanup(self):
        credential = Credential(sessdata="cached", bili_jct="test", dedeuserid="42")
        record = accounts.account_record_from_credential(credential, name="Cached")
        accounts.save_account_record(record)
        self.assertEqual(accounts.active_account_id(), "42")
        self.assertEqual(accounts.credential_from_cache().sessdata, "cached")
        self.assertEqual(len(accounts.load_account_records()), 1)
        with patch.dict(os.environ, {"BILI_COOKIE": "SESSDATA=environment; DedeUserID=99"}):
            self.assertEqual(accounts.credential_from_env().sessdata, "environment")
            self.assertEqual(accounts.active_account_record()["id"], "42")
        self.assertEqual(accounts.clear_all_account_caches(), 3)
        self.assertEqual(accounts.load_account_records(), [])

    def test_legacy_account_cache_is_still_readable(self):
        storage.write_json(config.LEGACY_CREDENTIAL_FILE, {"sessdata": "old", "dedeuserid": "24"})
        self.assertEqual(accounts.credential_from_cache().sessdata, "old")
        self.assertEqual(accounts.active_account_record()["source"], "legacy")

    def test_request_rate_controls_network_pacing(self):
        state.REQUEST_FREQUENCY = 8
        self.assertEqual(client.request_delay_seconds(), 0.125)
        state.REQUEST_FREQUENCY = 2
        self.assertEqual(client.request_delay_seconds(), 0.5)

    def test_png_plot_uses_configured_output_directory(self):
        output = plotting.save_line_plot(
            selected_creator={"name": "Example", "uid": "42"}, selection_label="All videos",
            plot_label="Likes", y_label="Likes",
            x_values=[datetime(2026, 1, 1), datetime(2026, 1, 2)], y_values=[10, 11],
        )
        self.assertEqual(output.parent, config.PLOTS_DIR)
        self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(plotting.plt.get_fignums(), [])

    def test_web_png_plot_with_dates_and_fallback_labels(self):
        from ascendant.web.plots import save_web_plot_png

        for labels in (("2026-09-01 12:00:00", "2026-09-02 12:00:00"), ("First", "Second")):
            with self.subTest(labels=labels):
                name = save_web_plot_png(
                    {"name": "Example", "uid": "42"}, "All videos", "Views", "Views",
                    [{"title": "A", "label": labels[0], "value": 10}, {"title": "B", "label": labels[1], "value": 20}],
                )
                self.assertTrue((config.PLOTS_DIR / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(plotting.plt.get_fignums(), [])


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_detail_success_and_failure(self):
        with patch.object(videos.video, "Video") as video_type:
            video_type.return_value.get_info = AsyncMock(return_value={"title": "Full title", "stat": {"view": 5}})
            result = await videos.fetch_video_detail({"bvid": "BV-test", "created": 100}, None)
            self.assertEqual(result["stat"], {"view": 5})
            self.assertEqual(result["pubdate"], 100)
            video_type.return_value.get_info.side_effect = RuntimeError("offline")
            result = await videos.fetch_video_detail({"bvid": "BV-test"}, None)
            self.assertIn('RuntimeError', result['detail_error'])
            self.assertNotIn('offline', result['detail_error'])
            video_type.reset_mock()
            self.assertEqual((await videos.fetch_video_detail({}, None))["detail_error"], "No BVID available.")
            video_type.assert_not_called()

    async def test_pagination_truncates_and_uses_shared_rate(self):
        uploader = Mock()
        uploader.get_videos = AsyncMock(side_effect=[
            {"list": {"vlist": [{"bvid": "A"}, {"bvid": "B"}]}},
            {"list": {"vlist": [{"bvid": "C"}, {"bvid": "D"}]}},
        ])
        with patch.object(state, "REQUEST_FREQUENCY", 8), patch.object(web_videos.asyncio, "sleep", new_callable=AsyncMock) as sleep, patch.object(web_state, "PROGRESS", {}):
            result = await web_videos.fetch_web_video_summaries(
                uploader, 3, user.VideoOrder.PUBDATE,
                progress_label="Testing", start_percent=0, end_percent=100,
            )
        self.assertEqual([item["bvid"] for item in result], ["A", "B", "C"])
        self.assertEqual(uploader.get_videos.await_args_list[1].kwargs["pn"], 2)
        self.assertEqual(sleep.await_count, 2)
        sleep.assert_awaited_with(0.125)

    async def test_client_cleanup_after_account_error(self):
        credential = Mock()
        credential.has_sessdata.return_value = True
        credential.check_valid = AsyncMock(side_effect=RuntimeError("offline"))
        with patch.object(accounts, "configure_bilibili_client"), patch.object(accounts, "close_bilibili_client", new_callable=AsyncMock) as close:
            self.assertEqual(await accounts.load_account_detail(credential), (None, None, "offline"))
        close.assert_awaited_once()


class EntryPointTests(unittest.TestCase):
    def test_direct_script_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "ascendant", root / "ascendant", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(ROOT / "web_server.py", root / "web_server.py")
            env = {key: value for key, value in os.environ.items() if not key.startswith("BILI_")}
            env.update(MPLCONFIGDIR=str(root / "matplotlib"), XDG_CACHE_HOME=str(root / "cache"))
            result = subprocess.run(
                [sys.executable, str(root / "web_server.py"), "--help"], cwd=root.parent, env=env,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--no-reload", result.stdout)

    def test_both_web_launchers_offer_the_same_options(self):
        outputs = []
        for command in ([sys.executable, "web_server.py", "--help"],
                        [sys.executable, "-m", "ascendant.web", "--help"]):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--no-reload", result.stdout)
            outputs.append(result.stdout.split("options:", 1)[-1])
        self.assertEqual(outputs[0], outputs[1])

    def test_web_imports_shared_modules_without_terminal_dependency(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ascendant.web.server; import sys; "
             "assert not any(n == 'ascendant.cli' or n.startswith('ascendant.cli.') for n in sys.modules); "
             "assert 'main.main' not in sys.modules"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
