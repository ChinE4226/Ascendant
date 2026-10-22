"""Asset confinement, template escaping, and web serialization."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ascendant import config
from ascendant.web import assets, serializers, state


class WebAssetTests(unittest.TestCase):
    def test_symlink_cannot_escape_static_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            static.mkdir()
            (root / "private.js").write_text("private")
            (static / "leak.js").symlink_to(root / "private.js")
            (static / "allowed.js").write_text("public")
            with patch.object(config, "STATIC_DIR", static):
                self.assertIsNone(assets.static_file("leak.js"))
                self.assertIsNone(assets.static_file("../private.js"))
                self.assertEqual(assets.static_file("allowed.js"), (static / "allowed.js").resolve())

    def test_reload_token_is_escaped_for_html_attribute(self):
        with patch.object(state, "RELOAD_TOKEN", '\"><script>alert(1)</script>'):
            html = assets.dashboard_html().decode()
        self.assertIn("&quot;&gt;&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_serialization_keeps_zero_metrics_and_omits_cover(self):
        result = serializers.serialize_single_video({"bvid": "BV1xx411c7mD", "pic": "private-cover", "stat": {"view": 0}, "duration": 3661})
        self.assertEqual(result["views"], 0)
        self.assertEqual(result["duration"], "1:01:01")
        self.assertNotIn("pic", result)
        self.assertNotIn("cover", result)
        self.assertEqual(serializers.extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD/"), "BV1xx411c7mD")
