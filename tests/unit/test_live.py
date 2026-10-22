"""Source categories are independent and ignore runtime/credentials."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ascendant import config
from ascendant.web.live import source_versions


class SourceRevisionTests(unittest.TestCase):
    def test_css_ui_python_and_runtime_revisions_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('static', 'templates', 'ascendant', '.runtime'):
                (root / folder).mkdir()
            (root / 'static/app.js').write_text('initial')
            (root / 'static/app.css').write_text('initial')
            (root / 'templates/dashboard.html').write_text('initial')
            (root / 'ascendant/app.py').write_text('initial')
            with patch.object(config, 'PROJECT_ROOT', root), patch.object(config, 'STATIC_DIR', root / 'static'), patch.object(config, 'TEMPLATES_DIR', root / 'templates'):
                original = source_versions(fresh=True)
                (root / '.runtime/private.json').write_text('not public')
                self.assertEqual(source_versions(fresh=True), original)
                (root / 'static/app.css').write_text('CSS changed')
                css = source_versions(fresh=True)
                self.assertNotEqual(css['css_revision'], original['css_revision'])
                self.assertEqual(css['ui_revision'], original['ui_revision'])
                self.assertEqual(css['python_revision'], original['python_revision'])
                (root / 'static/app.js').write_text('JS changed')
                js = source_versions(fresh=True)
                self.assertNotEqual(js['ui_revision'], css['ui_revision'])
                self.assertEqual(js['python_revision'], css['python_revision'])
                (root / 'templates/dashboard.html').unlink()
                self.assertNotEqual(source_versions(fresh=True)['ui_revision'], js['ui_revision'])
                (root / 'ascendant/app.py').write_text('Python changed')
                self.assertNotEqual(source_versions(fresh=True)['python_revision'], js['python_revision'])
