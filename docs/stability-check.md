# Stability check — 2026-10-19

The local checks passed after fixing cancellation of active background tracking
requests. These results describe a short offline workload, rather than an
assessment of continuous production use or Bilibili availability.

## Fixed shutdown behavior

Previously, `TrackingWorker.stop()` set a stop flag but an active request did not
observe it. A slow creator scan or video request could continue after the stop
request and outlive the worker's join timeout. An offline regression reproduced
an active request continuing after the stop flag was set.

The tracking worker now monitors the flag during collection and cancels the
active async task. Service `finally` blocks close HTTPX clients and release both
collection locks. Tests for an active video request and an active creator scan
verify that shutdown leaves no cached clients, partial observations/releases, or
misleading upstream error records.

## Measured offline resource run

The run used eight worker threads, two warmup rounds and 60 measured rounds,
lasting 73.52 seconds including warmup. All runtime/account paths and the SQLite
database were temporary. Bilibili requests used HTTPX `MockTransport` and never
contacted Bilibili. Dashboard requests used the real HTTP server on loopback.

| Check | Result |
| --- | --- |
| Measured transport operations | 24,000 across independent event loops |
| Transport outcomes | 4,800 each: success, HTTP 412, timeout, malformed JSON, cancellation |
| Actual local dashboard requests | 24,000; all HTTP 200 |
| Routes exercised | Health, tracking revision, memory diagnostics |
| SQLite observations written | 6,200, including warmup; expected row count matched |
| SQLite integrity check | `ok` |
| Cached event loops | Zero after every round and shutdown |
| Client/loop weak references | All released after each transport round |
| Live threads at round boundaries | Two: main and HTTP server |
| Live threads after server shutdown | One: main |
| Open file descriptors | Six during each measured round; five after server shutdown |
| Resident memory after warmup | 83.33 MiB |
| Resident memory during measured rounds | 79.64–85.55 MiB |
| Resident memory after shutdown | 81.09 MiB |
| Traced Python allocations after warmup / shutdown | 157.93 / 221.22 KiB |
| Per-round HTTP latency, 95th percentile | 1.97–8.78 ms |

No client, event-loop, thread or file-descriptor accumulation was detected in
this run. RSS remained within a small range. Traced allocations include the
benchmark's retained round results, and RSS also includes native allocations;
these measurements do not establish zero memory growth for every workload.

Raw samples and summary: [stability-results.json](stability-results.json).

## Other validation

- The full Python suite passed: 269 tests in 104.73 seconds. The additional
  creator-scan shutdown regression was then added; both shutdown regressions
  passed in a separate targeted run.
- Six JavaScript error-handling tests passed.
- The existing Chrome performance checks passed: bounded pagination, full-cohort
  analysis, responsive layouts, tracking revision changes, and recovery after a
  failed tracking-status refresh. No page errors were reported.
- `pip check` found no broken dependencies.

The workload used small fixture records. It does not establish large-dataset
memory limits, overnight stability, real HTTPX TLS/connection-pool behavior under
unreliable internet, or successful authenticated Bilibili access. The previously
observed HTTP 412 remains an upstream-access issue.

## Repeat

From the repository root:

```sh
.venv/bin/python -m unittest discover -s tests -t . -q
node --test tests/unit/api_errors.mjs
.venv/bin/python tests/stress/check_stability.py --rounds 60
```

The stress script uses only existing project dependencies and Python's standard
library. It prints JSON samples and a final report. Counts can be adjusted with
`--operations`, `--requests`, `--writes`, and `--workers`. Run it separately from
a production process when comparing latency or memory. It needs permission to
bind a local port and does not use saved accounts or modify the project's data.
