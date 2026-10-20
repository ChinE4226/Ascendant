# Bilibili HTTPX migration

Ascendant now uses HTTPX directly. `bilibili-api-python` has been removed from both
requirements files and from the Mac launcher's dependency checks. No SDK runtime
is vendored or imported. The node needs only HTTPX and its dependencies; QR image
creation remains a lazy dependency of the main application's sign-in flow.

## Supported operations

| Workflow | Application implementation |
| --- | --- |
| Existing cookies and account files, login validation | `api/credentials.py` |
| Account profile, creator profile, uploads, follower counts | `api/user.py` |
| Video details and tracking snapshots | `api/video.py` |
| Signed keyword search and Beijing-time date filters | `api/search.py` |
| Weekly-popular issue | `api/hot.py` |
| QR generation, polling and credential extraction | `api/login.py` |
| WBI key loading, query normalization and signature | `api/signing.py` |
| HTTP lifecycle, pacing, response checks | `client.py` |

Existing account JSON fields and environment variables are unchanged. SESSDATA
encoding matches the previous credential behavior. No database or account-file
migration is required. Saved datasets, tracking timestamps and SQLite schema are
unchanged by this transport migration.

Clients and cookie jars are separate for each account and guest within each
operation's event loop. Every fetch closes and evicts its clients. QR polls can
run in separate loops; the QR object retains only the login key, URL and eventual
credential, not an HTTP client. Concurrent signed requests share their operation's
navigation key. Creator page access IDs and signing keys expire with the operation.

All adapter requests, including navigation and creator-page prerequisites, obey
the operation's pacing. Existing collection delays remain in place. Requests use
a 20-second HTTPX timeout, bounded connection pools, HTTPS with certificate
verification, and no redirects. Cookies are scoped to Bilibili domains. The adapter
accepts only its configured API/passport/space hosts. HTTPX's normal proxy and
certificate environment settings remain available.

HTTP failures and nonzero API codes retain only numeric identifiers and local
recovery advice. Reports include an allowlisted request label and session-cookie
presence, without exposing query strings or claiming the cookie is valid. HTTP
status and API code remain independent, including an API error inside HTTP 200.
412 does not establish the exact cause or prove a rate limit; 429 is reported
separately as too many requests. Network failures have no fabricated HTTP status.
No automatic retries, challenge solving, device activation or
fabricated interaction telemetry are added. A rejected request stops collection;
the scheduler's existing backoff behavior still applies to later scheduled checks.
Malformed metadata stops collection instead of silently producing an empty dataset.
Unavailable videos retain the existing skip/report behavior.

## Verification

Run the full offline suite with the project environment:

```sh
.venv/bin/python -m unittest discover -s tests -t . -q
```

After uninstalling the SDK, all 262 tests passed. `pip check` reported no broken
requirements, and `bilibili_api` was no longer importable. The main and node
launcher tests started their real local servers and verified clean shutdown.
After improving error reports, all 268 Python tests and six JavaScript error
handling tests passed, including distinct status/code handling and request-context
propagation through tracking history and dashboard HTTP responses.

The HTTP server tests need permission to bind localhost ports. Adapter tests use
real HTTPX clients with `MockTransport`, covering request parameters, WBI signatures,
cookies, account validation, QR state transitions, response contracts, transport
errors and no retry on 412/-412/-403. Lifecycle tests run 500 independent event loops
and verify released client/loop weak references. The full suite also covers main
and node launchers, HTTP routes, collection, tracking, distributed fetching and reload.

A guest-only live smoke check on 2026-10-19 fetched public video details and creator
follower counts. A subsequent error-report check confirmed HTTP 412 from the
creator video-list endpoint (`/x/space/wbi/arc/search`), with no API code, after
the creator-page and navigation prerequisites succeeded. The
check stopped immediately and verified an empty client cache. Live signed creator,
search and authenticated QR/account completion were therefore not established by
that check. They have offline transport/workflow coverage. Bilibili may require a
valid local account or reject requests independently of the Python transport.

## Existing environments and maintenance

Update dependencies with the appropriate requirements file. After updating the
source, remove the obsolete SDK in each local environment that used it:

```sh
/path/to/environment/bin/python -m pip uninstall bilibili-api-python
```

The project `.venv` is the environment validated for this migration. Other Macs
must update their source/environment separately. Do not copy account files or
virtual environments between Macs. Do not remove other packages merely because
the old SDK depended on them; they may be used independently.

The minimal static endpoint reference, original license and provenance are in
[`reference/bilibili-api-python-17.4.1/`](reference/bilibili-api-python-17.4.1/README.md).
When an endpoint changes, update the narrow adapter and its HTTPX contract tests.
