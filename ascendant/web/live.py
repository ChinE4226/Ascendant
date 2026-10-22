"""Read source revisions for browser updates; no generated files or data refreshes."""
import hashlib
from threading import Lock
import time

from ascendant import config


def revision(paths):
    records = []
    for path in paths:
        try:
            stat = path.stat()
            if path.is_file():
                records.append((str(path), stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            pass
    return hashlib.sha256(repr(sorted(records)).encode()).hexdigest()[:24]


def python_paths(root):
    return [root / 'web_server.py', root / 'web_reload.py', *(root / 'ascendant').rglob('*.py')]


LOADED_PYTHON_REVISION = revision(python_paths(config.PROJECT_ROOT))
_lock = Lock()
_cached = None
_cached_at = 0
_cached_paths = None


def source_versions(*, fresh=False):
    global _cached, _cached_at, _cached_paths
    paths = (config.PROJECT_ROOT, config.TEMPLATES_DIR, config.STATIC_DIR)
    with _lock:
        now = time.monotonic()
        if fresh or _cached is None or paths != _cached_paths or now - _cached_at > .3:
            _cached = {
                'ui_revision': revision([*config.TEMPLATES_DIR.rglob('*.html'), *config.STATIC_DIR.rglob('*.js')]),
                'css_revision': revision(config.STATIC_DIR.rglob('*.css')),
                'python_revision': revision(python_paths(config.PROJECT_ROOT)),
                'loaded_python_revision': LOADED_PYTHON_REVISION,
            }
            _cached_at, _cached_paths = now, paths
        return dict(_cached)
