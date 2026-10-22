"""Per-operation pacing and progress overrides for a remote fetching worker."""

from contextvars import ContextVar

PROGRESS_CALLBACK = ContextVar('fetch_progress_callback', default=None)
REQUEST_DELAY = ContextVar('fetch_request_delay', default=None)
DETAIL_BATCHER = ContextVar('fetch_detail_batcher', default=None)
CANCELED = ContextVar('fetch_canceled', default=None)


def check_canceled():
    event = CANCELED.get()
    if event is not None and event.is_set():
        raise ValueError('Collection canceled because the task or connection ended.')
