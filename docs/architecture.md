# Ascendant Architecture

Ascendant is a local browser application backed by reusable Python modules.
The browser uses native JavaScript modules; no
JavaScript build step or additional web framework is required.

## Directory Map

```text
Ascendant/
    bilibili_ds/             Shared Python application code
        config.py           Project paths and defaults
        state.py            Process-wide request rate
        storage.py          JSON persistence helpers
        accounts.py         Credentials and account cache operations
        client.py           Network configuration, pacing, cleanup
        fetch_context.py    Per-collection pacing, cancellation and progress callbacks
        browser.py          Shared Chrome opening without dashboard imports
        videos.py           Video detail requests and response handling
        selection.py        Sorting, parsing, and filters
        analysis.py         Statistics and ratios
        weekly.py           Weekly cohort averages and engagement eligibility
        sampling.py         Filter validation and random draws from eligible candidates
        plotting.py         Headless PNG rendering
        distributed/        RAM-only coordinator, protocol, shared node fetching and worker
        node/               Lightweight local node web GUI and command entry point
        web/                Local HTTP interface
            __main__.py     Supervisor or worker startup
            server.py       HTTP server lifecycle
            routes.py       Endpoint dispatch and HTTP responses
            http.py         JSON request parsing
            assets.py       Template rendering and public asset lookup
            accounts.py     Browser account workflows
            creators.py     Browser creator selection and storage adapter
            videos.py       Browser fetching and selection
            weekly.py       Weekly source validation and collection
            sampling.py     Bounded search collection and refreshed detail metrics
            nodes.py        Local-only distributed service administration
            distributed_dataset.py  Normal Dataset collection through a fetching node
            actions.py      Browser analysis, ratios, and plotting
            serializers.py Browser-facing fields and result formats
            plots.py       Browser plot rendering and listings
            progress.py    Progress updates
            state.py       Transient browser state
            reload.py      Development supervisor
    templates/
        dashboard.html      Dashboard markup
    static/
        css/dashboard.css   Existing responsive styles
        js/
            app.js          Initialization, navigation, action bindings
            api.js          JSON HTTP requests
            constants.js    Metric names and labels
            state.js        Browser-only state
            ui.js           Common formatting and action feedback
            selection.js    Selection form values
            progress.js     Operation progress polling
            batch.js        Mission builder, server queue and retained results
            dev_reload.js  Source-change refresh and form restoration
            views/          Accounts, creators, videos, and plots
        vendor/             ECharts 5.6.0 and selected Lucide 1.8.0 modules
    tests/
        unit/               Calculations, serialization, asset validation
        integration/        Storage, mocked APIs, entry points, HTTP, reload
    scripts/
        mac_launcher.sh       Shared Mac environment checks, setup and launching
        preview_dashboard.py  Sample-data dashboard, no Bilibili requests
    docs/
        architecture.md     This guide
    requirements.txt        Direct Python dependencies
    requirements-node.txt   Fetching-node dependencies without Matplotlib
    web_server.py           Compatibility web launcher
    web_reload.py           Compatibility supervisor imports
    setup-main.command      One-time macOS main-app environment setup
    setup-node.command      One-time macOS node environment setup
    start-main.command      macOS main launcher with stable Python and live interface
    start-node.command      macOS fetching-node GUI launcher
    start-web.command       macOS development launcher with source reload
    objects/creators.json         Existing saved Creator identities
    .runtime/               Existing private caches and generated plots
```

## Dependency Direction

```text
Browser modules -> HTTP routes -> Web workflows -> Shared Python modules
Shared Python modules -> Bilibili API / local storage
```

New code should import its owning module directly, not the compatibility launchers.
The node's selection adapter reuses `web/videos.py` to keep the Dataset's existing
number, date and metric selection rules identical. That module has no chart or
HTTP server imports; loading it on a node does not load Matplotlib.

Web-specific creator selection rules and result formats live in `web/`.

## State and Files

- `config.py` is the source of truth for paths. Source paths are anchored to the
  checkout, not the current working directory. `BILIBILI_RUNTIME_DIR` can set a
  Mac-local runtime directory. Main/development command launchers share interpreter
  and runtime selection: default `~/Library/Application Support/BilibiliDataScience/runtime`,
  or an existing checkout sign-in cache when the default has none. Node launchers
  retain the Mac-local default; no credentials are copied.
- `state.py` owns the shared process request rate.
- `web/state.py` owns browser creator selection, progress, and QR login state.
- `web/dataset.py` retains one RAM-only dataset keyed by Creator, account ID, and fetch
  selection. Explicit refresh replaces it only on success. A nonblocking lock
  rejects overlapping video operations. `reuse_only` requests never fetch silently.
  Local view filters run on a copy and do not mutate the retained collection.
  It also holds four bounded sampling cohorts in `COHORTS`, under a separate metadata
  lock, using opaque collection IDs. Weekly/random collectors retain trusted raw
  valid rows after successful collection. `collection_id` actions reuse those rows
  independently of creator selection or account, without replacing `CURRENT`.
  Unknown IDs and refresh requests for cohorts fail explicitly; follower ratios
  are rejected for cohorts to avoid using an unrelated selected creator's count.
  Cohort plot snapshots name the source and support the existing explicit PNG export.
  `/api/collections` returns provenance metadata, not credentials or uploaded rows.
  `/api/workspace-data` reconstructs the creator table and exact fetch selection,
  plus the newest retained report of each sampling kind. It only serializes trusted
  RAM rows; it neither fetches metrics nor writes files, and uses `Cache-Control:
  no-store`. Report summaries share the four-cohort retention bound; serialized
  video rows are rebuilt on demand rather than duplicated in retained reports.
  Startup restores those tables, and reconnects to progress if collection is running.
  Creator restoration checks account and creator context; raw credential/API fields
  never appear in the restore response. Stopping or restarting Python clears RAM.
  `static/js/collections.js` manages per-browser source selection, separate cohort
  filters and requested/checked/eligible/valid/active count summaries. Source changes
  clear stale overview, ratio, chart and unusual-value displays. Creator refresh
  remains available on Data. Missions can fetch an independent creator or copy any
  loaded collection for processing without refetching.
  Collection metadata tracks requested, examined, skipped and shortfall counts.
  `web/videos.py` pages forward from the requested publication position until it
  collects End − Start + 1 unique videos with six valid metrics or runs out of
  available videos. It retains only valid rows. Date/metric ranges retain their
  boundaries. Request pacing applies to replacement requests too; summary errors
  and recognized server rejection codes abort the refresh.
- `web/distributed_dataset.py` dispatches normal Dataset selections to an idle
  compatible fetching node and imports successful results into `dataset.CURRENT`.
  The usual progress, dataset lock and later analysis workflows still apply.
  Automatic is the default, including requests without `fetch_source`. It uses
  this Mac whenever no compatible node is available, the node queue is full, or
  a selection cannot be represented by the node protocol. Explicit local/remote
  overrides remain available. `cancel_unstarted_selection` cancels a queued
  selection atomically with assignment before falling back locally; a claimed
  collection is never retried locally after failure. Separate manual collection
  controls are collapsed under Advanced collections on the Nodes page.
  `fetch_context.py` scopes cancellation, pacing and node progress to a collection.
  Dataset and Settings pacing controls update the same validated 0.1–4 rate.
  Selection units transmit this rate; workers cap it at their local setting.
  `web/parallel.py` uses the `DETAIL_BATCHER` context to keep creator pagination
  on main while sharing ordered detail batches between main and idle pacing-capable
  nodes. Each participant receives its fraction of the rate. Subsequent batches
  append to one bounded RAM job; completed leases remain valid for delivery retries.
  Invalid rows trigger replacement batches; rejection cancels active work and leaves
  the retained dataset unchanged. Automatic retains one-Mac routing; Parallel is explicit.
  `distributed/network.py` discovers active Thunderbolt Bridge IPv4 addresses from
  local interface metadata, cached for three seconds in RAM. Production listener
  startup binds only the bridge address and fails if no bridge is available.
  Worker HTTP/HTTPS control connections bind their source to the local bridge IP,
  disable proxies/redirects and never fall back to Wi-Fi. This avoids ambiguous
  link-local routes while each Mac's Bilibili client keeps its own normal internet
  routing. The GUI lists only the bound bridge URL; node health checks verify the
  chosen port before consuming a code. An address change requires listener restart
  and pairing again; no OS network or routing settings are changed by the app.
- `distributions.py` contains pure quantile, histogram, IQR, quality, and engagement
  calculations. `static/js/views/analysis.js` renders tables and inline SVG charts
  without PNG generation or disk writes.
- `web/plots.py` keeps at most 20 unsaved plot snapshots in memory, keyed by
  opaque IDs. Plot generation never writes PNGs. `POST /api/plots/save` exports
  the referenced snapshot on demand and returns the existing file on retries.
  A lock protects the snapshot cache and serializes matplotlib exports.
- `static/js/state.js` owns browser display state. It contains no credentials.
- Account credentials, cookies, QR images, and plots live under the configured
  runtime directory (legacy `.runtime/` for direct CLI startup). Creator identities are stored in `objects/creators.json`
  under the `creators` collection key; the terminology rename preserves existing entries.
- `/static/` serves only `.js` and `.css` files confined to the static directory.
  Traversal paths and symlinks outside that directory are rejected. Private
  runtime files are not exposed as a general static directory.

The application is a local, single-user tool. Process-wide request settings and
progress are not isolated by browser session; this is not a multi-user service.

## Entry Points and Reload

Double-click `setup-main.command` once on macOS to install the main dependencies
when no working environment exists. `setup-node.command` installs HTTPX
and its dependencies without Matplotlib. Both use a local Python
environment under `~/Library/Application Support/BilibiliDataScience/.venv`
and disable pip's download cache; neither builds a distribution archive. Main
startup may reuse a working legacy checkout environment. Node setup creates its
own local environment instead of modifying an iCloud-synced environment.

Double-click `start-main.command` to open the dashboard in Google Chrome, with a
foreground Terminal window, live interface updates and Python restarts disabled. `start-node.command`
starts the fetching node and opens its local web GUI in Chrome. Press Control+C
in the corresponding Terminal window to stop the app. The server runs independently of ChatGPT. Closing the
Terminal window and confirming termination also stops it.
Repeating the node launcher opens the existing node GUI without resetting its
connection or task. An unrelated app on the requested port causes startup to try
the next free port, with the actual address printed in Terminal.

`start-web.command`, `python -m bilibili_ds.web`, and `python web_server.py` start
the same dashboard with development reload enabled. Press Control+C in the
launcher window to stop both the server and watcher.

The web entry point starts the supervisor before importing application modules,
so a syntax error in application code does not kill the watcher. It watches
Python under `bilibili_ds/` and the root compatibility scripts.
Each worker uses a fresh bytecode-cache namespace and disables cache writes.

`web/live.py` caches metadata-only source signatures separately for CSS, HTML/JS
and Python. Templates embed initial interface revisions and the process token.
The standalone `dev_reload.js` module polls revisions even if `app.js` fails to parse.
It swaps stylesheets after successful loading and refreshes HTML/JS only while idle.
`performAction` exposes busy state on the document to coordinate foreground actions.
Safe control values, active page/source and scroll survive via temporary tab storage;
passwords, transient pairing codes and dataset rows are never stored there.
Interface updates retain server RAM state and restore collected tables/reports.
Browser-rendered analysis and charts must be rerun on the retained collection.
Stable main reports pending Python changes; development
mode restarts on Python edits, clearing RAM and connections. Changes to the saved
Creator list refresh that list without a server restart.
Changes to the supervisor itself require restarting the launcher.

## Interactive Plots

`static/js/views/plots.js` owns the ECharts instance, chart controls, hover details,
visible-range Y-axis scaling, and manual saving. Valid dated points are sorted
chronologically and use a true time axis; undated points use a category axis.
ResizeObserver handles layout and tab changes. Exports from the download icon
capture the current browser chart; Saved Plots contains full-range Python PNGs.
The browser legend sits below the canvas; downloads append it as an in-memory footer.
Saved PNGs reserve a separate figure footer for the legend.
Raw and log-value renderers use zero-based axes with 1/2/5-based tick intervals; performance-index axes permit negative values. MA5/MA10/MA20
use full trailing windows over valid plotted points, with nulls before the first
complete window. Zooming scales against visible raw and MA values without
recomputing windows. Save requests include `ma_periods`; the server validates and
normalizes these, and caches exports by axis mode, MA windows, and additional
`indicators`, value mode, anomaly markers and chart style. The health response exposes
`chart_export_version: 2`, preventing an older running backend from silently saving
a raw chart when the browser displays an index. `bilibili_ds/indicators.py` and `static/js/indicators.js` implement the
same pure trailing calculations: seeded EMA, full-window median, and relative
performance against the preceding 20 observations. Zero baselines produce gaps.
Relative20 uses a separate ECharts instance with bidirectional zoom synchronization;
PNG downloads combine both panels in memory and Python exports share their X-axis.
The main plot keeps its height when the relative panel is enabled.
The vendored modules require no frontend build or external CDN access. Preserve
upstream headers and `docs/licenses/` when updating them.

The same pure modules calculate a fixed-cohort median-centred log performance index
and prior-20 log-space modified z-scores. Warmup, zero MAD and undated sequences
produce unavailable anomaly scores. Chart mode changes retain zoom and the original
raw points; transformed values are used only for display and appropriate overlays.
Relative20 continues to use raw values. `app.js` mirrors dataset provenance/filter
status in a compact Analysis summary. The Unusual values page derives its table
from the last generated chart without I/O; a new chart replaces this report.
Unsaved algorithms and plots remain in memory; only explicit export writes a file.

## Dataset Snapshots and Tracking

The two workflows have separate pages, browser modules, service modules, API
namespaces and processing algorithms, while sharing one SQLite file. See
[`tracking-workflow.md`](tracking-workflow.md) for behavior and formulas.

`bilibili_ds/tracking.py` owns transactions and additive schema migrations:
`tracking_schema.sql` (v1), `tracking_collections.sql` (v2),
`tracking_workflow.sql` (v3), and `tracking_revision.sql` (v4). The revision table
stores a database identity and counter. Triggers increment it transactionally for
tracking-related writes, including edits from another SQLite client; rollbacks do
not invalidate the page. `/api/tracking/revision` reads that single row, so idle
polls skip overview queries, full history analysis and DOM replacement.
Connections enable foreign keys, use WAL with a
five-second lock timeout, and close after commit/rollback. Initialization uses
thread and database write locks; unknown versions are rejected. The default is
`PROJECT_ROOT/data/tracking.sqlite3`, with a `BILIBILI_TRACKING_DB` override.

Dataset snapshots: `web/snapshots.py:capture_collection` copies the selected RAM
collection, or calls the normal dataset/sample collector for a fresh result.
Fresh collectors do not retain/evict RAM collections. `snapshot_batches` retains
scope and collection/save times; `snapshot_batch_items` links ordered immutable
rows. The whole batch commits atomically. Resaving identical loaded observations
reuses rows. `/api/snapshots`, `/collection`, `/batch`, `/export`, and `/backup`
serve `static/js/views/snapshots.js` under Workspace → Data → Dataset snapshots.

Tracking: `web/tracking.py` collects fresh BV metrics and scans creator releases.
Only `save_observation` enrolls rows in `tracking_samples`; `tracking_observations`,
`latest_tracking_observations` and `tracking_history` filter to that membership.
Collection batch writes never enroll rows. Migration v3 classifies existing
scheduled/tracker-linked observations and legacy single-video observations as
tracking; batch-only rows remain datasets. The shared raw `snapshots` table and
its old views remain for compatibility; neither tracking analysis nor its export
uses unfiltered raw history. No saved records are deleted or duplicated.

`creator_watches` has independent discovery and new-video intervals.
`creator_seen` records baseline uploads and later detected releases. The first
successful scan is a baseline, not a release alert. Later unseen uploads published
after that baseline get trackers in the same transaction as discovery. Existing
trackers preserve pause/status/interval. `creator_watch_errors` stores sanitized
failures with backoff. Checks fetch at most ten newest-first pages of 50 rows;
repeated scans deduplicate by BV ID and inspect whole pages before stopping.

The server-owned `TrackingWorker` selects the earliest due video or creator watch
and shares the network lock with foreground fetches; it never changes CURRENT,
COHORTS or dataset progress. Pausing during requests preserves paused schedules.
Busy locks defer work without errors, failed requests use at least a five-minute
retry, and missed checks are not backfilled. Main and computer must stay running.
One main process should own a tracking database.

`tracking_analysis.py` processes chronologically ordered observations of one BV
and selected metric. It computes signed deltas, actual-time interval rates, growth
percentages, rate changes, net change, video age and overall elapsed-time averages.
Missing values break adjacent comparisons. Counter decreases are flagged and
excluded from rate-change comparisons; raw negative deltas/rates are preserved.
Window-independent summaries use all observations before display truncation.
These calculations do not call dataset distribution, ratio or outlier algorithms.

`/api/tracking` reports monitors and releases; `/history` and `/export` expose
tracking-only observations; `/analysis` runs the time-series algorithm. POST
`/trackers`, `/update`, `/check`, `/creators`, `/creators/update`, `/creators/check`
and `/backup` manage the workflow. Legacy `/tracking/snapshot` and collection
routes remain aliases for compatibility. `static/js/views/tracking.js` lives at
Workspace → Tracking and displays metric/rate charts and histories. Both pages
poll only while visible and no foreground action is running. Database files are
never served as static assets; credentials and raw API responses are not stored.

## Extending the Project

Put reusable evaluation calculations in a new shared module, with unit tests.
Add browser-specific response handling in `web/` and presentation in
`static/js/views/`. Keep network calls and credentials on the Python side. Avoid
introducing imports from shared calculations back into the web layer.

## Dashboard Organization

Primary navigation groups Explore (Creators library, creator profile and single video), Workspace
(Data, Analysis, Tasks, Nodes), and Settings. Data groups creator datasets, sampling and dataset snapshots; Tracking has its own workspace group;
Analysis groups overview, charts, ratios, unusual values and saved charts. Group and
subview navigation both preserve state and remain available during ongoing actions.
`app.js` maps each tool to a section and remembers its last active view in memory.
Before switching panels or Sampling tabs, `ui.preservePageHeight()` reserves enough
minimum height in the main element to keep the current viewport reachable. Main
content disables scroll anchoring so replacing a panel does not shift the page.
The minimum is recalculated on each switch; content can grow naturally. A passive
scroll handler reduces the reserved floor when the user scrolls upward, keeping
the new viewport reachable. Returning to the top releases spare height immediately
without needing another navigation, restoring the page's natural scroll limit.
The collection/filter panel appears only on Dataset; other tools reuse its settings.
A successful fetch updates rows and collapses collection controls without changing
the active page. Navigation itself does not fetch data. Live reload restores
the active tool using the main element's `data-active-panel` attribute.

Quick creator switching uses an anchored dropdown on the Back button row with its own search input.
The Creators panel handles browsing and adding saved identities. Both lists use
`views/creators.js`, and selection updates do not reload unrelated sections.
`app.js` keeps a RAM-only menu history. The explicit Back button pops that history.
Outside clicks dismiss open dropdowns. Page changes retain dataset filters and
fetched results.
Global action feedback uses a fixed notice so progress and errors do not move
page content. Clipboard actions disable only their own button, restore keyboard
focus without scrolling, and keep address buttons intact during node polling.

The Tasks page builds independent missions through `/api/missions`: add, reorder,
remove, run/resume, stop and inspect results. `web/missions.py` owns a sequential
worker and up to 30 missions in server RAM. Each captures its creator, fetch range,
account identity, local filters and processing settings. A context-local creator
override isolates fetches from the global selected creator. Fetches retain their
own dataset in `dataset.MISSION_COLLECTIONS`; loaded inputs are copied at enqueue
time. This store shares the collection read APIs but is separate from the four-sample
eviction policy. Each mission holds at most 2,500 rows.

Steps run under `dataset.LOCK`. Analysis, charts and ratios reuse the mission's
collection; only an explicit snapshot step writes SQLite collection history.
Results and completed steps are retained. Failure stops later missions; stop waits
for the current step, and resume skips completed steps. Queue edits are disabled
during a run. The server worker survives browser navigation and disconnection;
restarting Python clears unsaved RAM. `batch.js` builds and polls the queue, while
`app.js` opens retained results through the existing analysis/plot renderers.

Sampling groups Random sample and Weekly popular in local tabs. Random sample
uses `POST /api/random-sample`; `web/sampling.py` searches video-only candidates
with the application-owned HTTPX adapter, at most `ceil(pool_size / 20)` search pages and at most
500 candidate entries. Every unique BVID gets paced detail enrichment; duplicate
entries need no repeated detail request. `sampling.py` rejects incomplete metrics,
applies inclusive metric boundaries and Beijing-time publication dates, and uses
a request-local seeded PRNG to sample uniformly without replacement across the
entire eligible pool. A shortfall is reported instead of silently increasing the
candidate limit. Search ranking limits the population represented by the sample.
The report includes candidate, detail, duplicate, invalid, filtered, eligible,
sampled and requested counts, seed and collection times. The shared cohort
renderer shows summaries and engagement ratios. Collection shares `dataset.LOCK`
and never modifies `dataset.CURRENT`; it stops on API rejection. Successful
results remain in the browser when navigating; a failed request retains the old
report. No new dataset, cache or report files are created.

Weekly popular uses `POST /api/weekly-analysis` with an issue number or a validated
Bilibili weekly-page URL. `web/weekly.py` calls the weekly endpoint through the HTTPX adapter;
it never requests an arbitrary user-provided URL. Complete list metrics avoid
per-video requests; incomplete entries use the existing paced detail fetcher.
It shares the video-operation lock but does not modify `dataset.CURRENT`.
`weekly.py` deduplicates the cohort, excludes invalid records, and calculates count
summaries and equal-weight versus pooled engagement ratios. The browser retains
the report when navigating or when a later fetch fails. No report is saved to disk.
