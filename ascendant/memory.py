"""Current process resident memory, without collecting data or adding dependencies."""

import ctypes
from datetime import datetime, timezone
from functools import lru_cache
import os
from pathlib import Path
import sys


class _MachTaskBasicInfo(ctypes.Structure):
    # Layout and flavor from macOS SDK mach/task_info.h (MACH_TASK_BASIC_INFO).
    _fields_ = [('virtual_size', ctypes.c_uint64), ('resident_size', ctypes.c_uint64),
                ('resident_size_max', ctypes.c_uint64), ('user_time', ctypes.c_int32 * 2),
                ('system_time', ctypes.c_int32 * 2), ('policy', ctypes.c_int32),
                ('suspend_count', ctypes.c_int32)]


@lru_cache(maxsize=1)
def _mach_library():
    library = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    library.task_info.argtypes = [ctypes.c_uint32, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint32)]
    library.task_info.restype = ctypes.c_int32
    return library


def _darwin_rss():
    library = _mach_library()
    task = ctypes.c_uint32.in_dll(library, 'mach_task_self_').value
    info = _MachTaskBasicInfo()
    count = ctypes.c_uint32(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int32))
    result = library.task_info(task, 20, ctypes.cast(ctypes.byref(info), ctypes.POINTER(ctypes.c_int32)), ctypes.byref(count))
    if result != 0:
        raise OSError('Process memory measurement is unavailable.')
    return info.resident_size


def process_memory():
    """RSS is current resident bytes, never peak usage; None means unavailable."""
    rss = None
    try:
        if sys.platform == 'darwin':
            rss = _darwin_rss()
        elif sys.platform.startswith('linux'):
            rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return {'pid': os.getpid(), 'rss_bytes': rss,
            'sampled_at': datetime.now(timezone.utc).isoformat()}
