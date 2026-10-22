# Ascendant

Ascendant is a local Python web application for exploring Bilibili creators and videos.

Source repository: [ChinE4226/Ascendant](https://github.com/ChinE4226/Ascendant).

## Getting Started

Python 3.10 or newer is required; the existing environment was tested with Python
3.14. On macOS, double-click `setup-main.command` once to install the main app's
dependencies, then double-click `start-main.command`. If this checkout already
has a working Python environment, skip setup.

For a terminal-based setup, run these commands from the project directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-cache-dir -r requirements.txt
```

Start the dashboard:

```sh
python -B -m ascendant.web --no-reload
```

Open the address printed by the server, normally
[http://127.0.0.1:8000](http://127.0.0.1:8000). If that port is busy, the server tries
the next available port. The server is intended for local, single-user use.

The command `python web_server.py` also starts the same web application, with
development reload enabled.

### Start and Stop on macOS

1. Double-click `start-main.command`. Terminal opens and the dashboard opens in
   Google Chrome. Interface edits update automatically; Python restarts are disabled
   so editing a project file does not reset node work.
2. Keep that Terminal window open. The server runs independently of ChatGPT.
3. To stop, click the Terminal window and press **Control+C**.

Closing that Terminal window and confirming termination also shuts down the
server. Closing only the browser tab does not stop the server.

On a fetching Mac, use `setup-node.command` once and `start-node.command` to open
the node's local web GUI in Google Chrome. Setup installs into a local Python
environment and disables the pip download cache. No ZIP or distribution package
is needed.

New setup environments live in `~/Library/Application Support/BilibiliDataScience/.venv`
on each Mac. Main and development launchers select the same interpreter and runtime.
Account files default to that local application support folder. If it has no cached
sign-in but the main checkout's `.runtime` does, both main launchers reuse that
existing cache without copying credentials. The node always defaults to its local
runtime. `BILIBILI_RUNTIME_DIR` explicitly overrides either choice.
An existing working project environment remains usable;
leave legacy `.venv`, `venv` and `.runtime` folders out of a source-only sync.

The application was previously named Bilibili Data Science. Its repository and
project folder are named `Ascendant`, and its Python package is `ascendant`.
External scripts should import `ascendant`; direct module launch commands are
`python -m ascendant.web` and `python -m ascendant.node`. Launcher filenames and
the existing `BilibiliDataScience` application support directory retain their
names for compatibility. The package rename requires no data or account migration.

## Project Structure

```text
ascendant/          Shared Python logic, paths, and settings
    web/             HTTP routes, web workflows, and development reload
templates/           Dashboard HTML
static/
    css/             Responsive styles
    js/              Browser modules and feature views
    vendor/          Pinned local chart library and selected Lucide icons
tests/
    unit/            Calculations, validation, and serialization
    integration/     Storage, requests, launchers, HTTP, and reload
scripts/             Local development tools and sample-data preview
docs/                Architecture and maintenance guide
objects/             Saved Creator identities
.runtime/            Private account caches, QR images, and generated plots
requirements.txt     Python dependencies used by this checkout
setup-main.command   One-time macOS main-app environment setup
setup-node.command   One-time macOS fetching-node environment setup
start-main.command   macOS dashboard with live interface updates and stable Python
start-node.command   macOS fetching-node GUI launcher
start-web.command    macOS development launcher with source reload
web_server.py        Small compatibility web launcher
web_reload.py        Compatibility supervisor imports
```

See [the architecture guide](docs/architecture.md) for individual module
responsibilities, dependency direction, state ownership, and extension points.
The website imports shared Python modules directly. No frontend build step is required.

## Features

- QR sign-in, cached account selection, guest mode, and sign-out.
- Saved Creator selection and account or creator profile details.
- Temporary lookup of one video by its Bilibili URL or BV ID.
- Video selection by published-time position, date range, or metric range.
- Distribution summaries: valid/missing counts, mean, median, quartiles, P90, min/max,
  and IQR, with inline histograms, box plots, and explained outlier flags.
- Pooled and median per-video engagement ratios, duplicate-ID checks, and low-view flags.
- One reusable in-memory dataset with explicit fetching and local view-count filters.
- Per-video and aggregate ratios, including ratios involving follower counts.
- Interactive charts with crosshair details, zoom/pan, time ranges, line/bar views,
  a range slider, publication-time or equally spaced video-number axes, and zero-based Y-axis ticks.
- Optional MA5/10/20, EMA10/20, and rolling median5/10 overlays with hover values and PNG exports.
- Relative20 compares each video with its previous 20-video mean in a separate panel with linked zoom.
- Manual PNG saving, current-view downloads, saved-plot browsing, and fetching progress.

Aggregate division uses matched rows with a known nonnegative numerator and a
positive denominator. Excluded rows are counted. Followers are repeated for each
eligible video; follower counts are fetched separately when that ratio is requested.
Single-video lookup results stay only in the current page and are replaced by
the next lookup; no cover image is displayed.

Generating a plot does not save a file. **Save PNG** saves the full data range to
Saved Plots; the download icon exports the current chart view to your browser's
download location. Unsaved data is temporary: the server retains the latest 20
plot snapshots in memory, and restarting or source-reloading clears them.
Moving averages use full trailing windows of 5, 10, or 20 valid plotted videos,
ordered oldest to newest; these windows count videos, not days. The first N−1
points have no MA value. Overlays use the loaded selection in memory, keep their
values during zooming, and appear in both PNG save and download output.
EMA uses weight `2 / (N + 1)` and starts from the first N-video average. Rolling
medians use the middle value (or middle pair for even windows). Relative20 excludes
the current video from its baseline, needs 21 videos for its first value, and leaves
a gap when the baseline is zero. All indicators count valid plotted videos, not days.
Both chart panels appear in explicit PNG exports; no indicator computation writes files.
Range presets are relative to the newest selected video's publication date.
These are current metrics grouped by publication time, not historical snapshots
of a video's changing metrics. Raw and log-value Y-axes begin at zero; performance-index axes also include negative values when needed. The chart rescales to
the visible range using round tick intervals; ratios retain decimal precision.

Charts offer **Raw values**, **Log scale** (`log10(value + 1)`), and **Performance index**.
The index uses `100 + 25 × log2((value + 1) / (dataset median + 1))`; 100 is typical
within the selected cohort and approximately +25 represents doubling. The baseline
uses all valid chart values, stays fixed during zooming, and changes with a new
selection or metric. Index values are never clipped. Zero is valid; small cohorts
may have unstable baselines. Index trend lines use index values; log-view trend
lines are computed on raw values and then transformed. Relative20 always uses raw values.

**Mark unusual values** highlights potential outliers with amber diamonds.
In chronological order, each log-transformed value is compared with the preceding
20 plotted videos, excluding itself. The modified z-score is
`0.6745 × (ln(value + 1) − prior median) / prior MAD`; absolute scores above 3.5
are flagged. The first 20 observations, zero-MAD windows and undated sequences
have no score. **Analysis → Unusual values** lists flags for the most recently
generated chart, including raw values, indices and scores. It does not change
filters, delete rows or fetch data. Flags do not establish manipulation, and
cumulative metrics remain affected by video age. See the
[NIST method reference](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm).
Trend controls are collapsed under **Trend indicators**. PNG saves preserve value
mode, markers and line/bar style; downloads capture the current view in memory.
After updating an already running dashboard, reload the browser for these controls.
Restart main to enable the updated Save PNG renderer; a restart clears RAM data and
pairing, while browser refresh preserves them. Download current view works without a restart.

## Dashboard Navigation

- **Explore** groups the Creators library, creator profiles, and single-video lookup.
- **Workspace** has five destinations: Data, Analysis, Tracking, Tasks, and Nodes.
  Data contains Creator dataset, Sampling (Random sample / Weekly popular), and Dataset snapshots.
  Analysis contains Overview, Charts, Ratios & engagement, Unusual values, and Saved charts.
  Collection and local filters appear only on Creator dataset; analysis shows a compact dataset summary.
- **Settings** contains account, sign-in, and request controls.

The dashboard opens on Workspace. Fetching updates dataset rows and collapses
collection controls without changing the active page. Switching tools keeps your controls and results; returning
to a section restores its last view. Saved charts remain available without a fetch.
Collection and filter controls appear only on Dataset; Statistics, Ratios, and Charts
reuse its selection and filters. Back sits below the section description on every
page. Creator selection is part of dataset setup and the creator profile page;
analysis links back to dataset setup; Tasks captures separate creator settings for each mission.

Random samples and weekly reports now have **Use in Analysis**. This opens Overview
on the exact collected rows; Charts, Ratios, Unusual values and missions using loaded
rows use the same collection without fetching again. The **Analysis collection**
menu switches between the creator dataset and retained sampling collections.
Switching clears the previous source's analysis results. Your creator dataset stays
available, with its own local filters. Sampling collections have separate inclusive
view filters in the Analysis summary; run a tool to apply them.

The summary names the source and collection interval, and separates requested,
checked, valid/sampled and active row counts. For example, **100 requested →
102 checked → 100 valid → 86 after local filters**. Random samples also show the
eligible pool before the draw. Collection scope retains the issue or keyword,
search order, bounds and seed. Follower-based ratios require a creator dataset;
mixed-creator collections support the six video metrics.

The latest four sampling collections (at most 2,500 rows each) stay in server RAM.
Expired collections require an explicit recollection on Sampling; they never
silently refetch. Ordinary collection and analysis actions write no collection or
report files; explicit snapshots can persist a selected collection. Browser refresh or
reopening a tab restores the latest weekly and random reports from these retained
collections; restarting main clears them.

Use **Release from memory** on Dataset, beside a sampling result, or in the Analysis
collection controls to discard that loaded source and its temporary results.
Unsaved rows cannot be recovered without fetching again. Saved SQLite snapshots
and PNGs remain available, and other loaded collections stay in RAM. Missions own
independent copies: remove a mission in Tasks to release its dataset and results.

Fetch failures distinguish Bilibili HTTP status (such as `HTTP 412`) from API codes
(such as `API code -412`), show the affected page/video when available, and provide
sign-in, connection or pacing guidance. Collection stops on rejection and preserves
the previously loaded dataset; the app does not automatically repeat the fetch.
The application-owned HTTPX adapter does not retry API rejections, including WBI
`-403`, HTTP 412 and API `-412`. Video-detail timeouts, network failures and
server errors stop collection instead of being counted as invalid video metrics.
Error notices stay visible until
dismissed or the next action begins. Tracking saves safe failure messages separately
from observations and waits at least five minutes before retrying that target.
Raw upstream responses and credentials are excluded from these messages.
Reports identify the actual failed request, including creator-page or navigation
prerequisites, and whether a session cookie was supplied (without claiming it is
valid). HTTP status and API code remain separate: `HTTP 200, API code -412` is
different from `HTTP 412`. A 412 response alone does not establish the exact
cause or prove a rate limit. Unknown HTTP/API failures stop collection instead
of silently counting videos as unavailable; a confirmed 404 can still be skipped.

For an offline error-interface check, run
`python scripts/preview_dashboard.py --simulate-http-error 412` and click
**Fetch / Refresh**. This preview uses temporary data and makes no Bilibili requests.

Click the muted **Memory** control beside the account/request status to inspect current server RAM
(RSS), estimated RAM for each retained dataset, and cached mission/chart/task
counts in a popover without moving the page content. It refreshes every 10 seconds
while the tab is visible. The server number
includes all fetching, tracking, processing and libraries in that process; dataset
estimates are included in it, not extra memory. Dataset estimates sample large
lists and exclude derived results. Python may retain freed memory for reuse, so
releasing a dataset need not immediately reduce RSS. Browser JavaScript heap is
shown only when supported; full browser RAM requires Activity Monitor → Memory.
Updated fetching nodes report their process RAM through existing heartbeats.
Offline reports are marked as last reported; older nodes show unavailable. Node
GUI → Node activity also displays that node's process RAM. No combined software
total is claimed because browser/native/shared memory cannot be summed reliably
from the dashboard. SQLite history occupies disk and is not a RAM dataset.

## Dataset Snapshots

After fetching a creator dataset, weekly list or random sample, click **Save data
as a snapshot** beside the result to save that whole collection. Saving is
optional: continuing without clicking it keeps the data in RAM. The result
shows **Snapshot saved** after a successful save.

Open **Workspace → Data → Dataset snapshots**. Under **Collection snapshot**,
choose the creator dataset or a retained weekly/random sample, then choose:

- **Save fetched data** saves the whole selected collection already loaded in
  RAM, with its original collection time. It makes no network requests and saves
  no other loaded collections. Local analysis filters do not trim the snapshot.
- **Fetch new data** collects the selected source again and saves only that new
  result. Creator collection uses the current controls on Data, including the
  fetching source; samples use their original issue or search scope and seed.
  Existing RAM collections remain unchanged and are not saved by this action.

Use **View collection** under **Saved collection snapshots** to inspect a saved
collection or download its complete CSV. Collection and save times are separate.
Saving the same loaded data again creates another collection record but reuses
its observations, so it does not invent a new point in video history. Empty or
expired loaded collections must be collected first.

## Tracking workflow

Open **Workspace → Tracking**. This workflow has its own targets, schedules,
observation history and time-series algorithm. Dataset ranges, samples and local
analysis filters do not affect it; saving a dataset never adds a tracking point.

1. **Monitor a known video:** enter a video URL/BV ID and check interval, then
   **Start video tracking**. The first fresh check establishes its metric baseline.
2. **Watch for releases:** enter a creator UID/profile link, discovery interval,
   and the interval to use for new videos. **Start release watch** establishes an
   existing-upload baseline on its first successful scan. Later uploads with a
   publication time after that baseline automatically get video trackers.
3. **Collect over time:** the main server performs due checks. **Check now** and
   **Check releases now** run explicit checks without changing loaded datasets.
   Duplicate discoveries never create duplicate video trackers. Existing paused
   trackers remain paused, with their original interval.
4. **Analyze a video:** choose **Analyze**, select one of seven metrics, and view
   observed counts or interval change per hour against collection time. The report
   gives baseline/latest values, net change, elapsed hours, growth percentage,
   rates, rate changes and video age. CSV exports the complete observation history.
5. **Pause/resume independently:** pausing a creator stops release discovery;
   its video trackers keep their own schedules. Pausing a video retains its history.
   An already running check may finish. Resuming makes the next check due now.

Tracking compares the same video's cumulative counts at successive observation
times. It does not run dataset distributions or compare different videos by their
publication dates. Change/hour uses actual elapsed time, not the requested check
interval. Missing metrics break adjacent comparisons; decreases are retained and
flagged. Percentage growth from zero is unavailable. Rate changes are unavailable
across missing data or counter decreases. Rates are averages over each observed
interval, not instantaneous activity. No missing points are estimated/backfilled.

The browser can be closed, but the main app and computer must remain running.
While Tracking is visible, its five-second refresh checks a small database revision
counter first. Tables and selected history update only after data changes; manual
Refresh still reloads them. Restart the main app after this update to enable the
revision endpoint. Existing history migrates automatically without deletion.
Active watches resume after restart; overdue work gets one check rather than
inventing missed observations. Collection shares the network lock with foreground
operations. Failed video and creator checks are recorded separately and retry no
sooner than 5 minutes or their configured interval. Release discovery scans up to
500 newest uploads per check and reports an error if it cannot reach its baseline
within that bound. It detects published videos; it does not predict release dates
or recover releases removed before a successful check.

The database defaults to **`data/tracking.sqlite3` in this checkout**, independent
of the account runtime directory selected by the Mac launchers. Override it with
`BILIBILI_TRACKING_DB` if needed. These local database and backup files are ignored
by Git. No credentials, descriptions, images, or raw API responses are saved.
Ordinary lookups and dataset/analysis actions continue to use RAM; only explicit
snapshots and tracker checks save video metrics.

In **DB Browser for SQLite**, choose **Open Database**, select the file, then use
**Browse Data**. Tables are `videos`, `trackers`, `snapshots`, `collection_errors`,
`snapshot_batches` (one saved collection), and `snapshot_batch_items` (its ordered
video observations). The `collection_snapshot_rows` view joins collection details
and metrics for easy browsing. `tracking_observations`,
`latest_tracking_observations` and `tracking_history`
show only tracking checks. `creator_watches`, `creator_seen` and
`creator_watch_errors` retain discovery schedules, baselines, releases and failures.
The legacy `snapshots`, `latest_snapshots` and `snapshot_history` include the shared
raw observation store; use the dedicated views to keep the two workflows separate.
Snapshots preserve the observed title, creator, publication time,
views, likes, coins, favorites, replies, shares, and danmaku. Zero is valid, missing
or invalid counts are SQL `NULL`, and decreasing counts are retained. History
starts at your first successful observation; it cannot recover earlier metrics.

All `*_at` timestamps use ISO 8601 UTC; `published_at` is Unix seconds. The dashboard
displays Beijing time. For example, in DB Browser's **Execute SQL** tab:

```sql
SELECT bvid, datetime(collected_at, '+8 hours') AS collected_beijing,
       views, views_change, views_per_hour, likes, coins
FROM tracking_history
ORDER BY collected_at DESC;
```

To browse one whole collection, replace `1` with its ID from `snapshot_batches`:

```sql
SELECT label, bvid, title, views, likes, collected_at, saved_at
FROM collection_snapshot_rows
WHERE batch_id = 1
ORDER BY position;
```

Pause trackers before manually editing tables and take a backup first. The
**Back up database** action uses SQLite's backup API to include committed WAL
changes; copying only a database file during collection may omit those changes.
Do not edit or delete its `-wal` / `-shm` sidecar files. The app creates its
versioned schema automatically and upgrades existing history without deleting it;
to initialize without starting the dashboard:

```sh
python -B -m ascendant.tracking
```

## Mission Queue

Open **Workspace → Tasks** and add one mission per creator or loaded dataset.
Each mission captures its own name, creator UID, collection range, fetching source,
local view filters, chart settings, ratio settings and selected steps when added.
You can choose a saved creator or enter a different creator UID directly.

For example, add these three missions, then click **Run / resume queue**:

1. A: fetch → save snapshot → analyze.
2. B: fetch → generate plot.
3. C: fetch → calculate ratio.

Fetching B and C preserves A's whole dataset in RAM. Mission fetches also leave the
ordinary Creator dataset and selected creator unchanged. **Source** can instead
copy any already loaded creator dataset, sample or earlier mission dataset;
that copy survives replacement or expiration of its original source.

**View data & results** reopens any mission's retained rows and results. Open its
saved analysis, plot or ratios on the usual pages, or use its dataset for new
analysis. Retained mission datasets also appear in **Analysis collection** and
**Dataset snapshots**. Generating a plot does not save a PNG.

Only a checked **Save data as a snapshot** step writes the whole collection to
SQLite, at its original collection time. Analysis, plots and ratios apply that
mission's local filters; snapshots include all fetched rows.

Move missions up/down to change their execution order, duplicate settings to build
another mission, or remove a mission to release its retained data. Up to 30 missions
are retained without automatic eviction. Position ranges support 1–500 valid videos;
each mission can retain at most 2,500 rows. The account active when adding the mission
must remain active when running/resuming it.

The server runs the queue, so navigating away, refreshing or closing the browser
does not cancel it. **Stop after current step** pauses after the active operation
finishes. A failed step stops the queue and preserves earlier results; resume retries
that step and continues without repeating completed fetches or snapshots. Missions,
queue state and results stay in RAM until removed or the main app restarts. Explicit
SQLite snapshots remain available after restart.

## Random Video Samples

Open **Workspace → Data → Sampling → Random sample**. Enter a search keyword, choose
the candidate order, and set the sample size and candidate limit (up to 500).
Optional filters include a Bilibili category ID, publication dates in Beijing time,
and an inclusive range for any of the six metrics. Metric boundaries support
`k` and `m`, such as `10k`. Publication end dates include the whole day.

**Collect random sample** checks details across the bounded search pool, skips
duplicate IDs and incomplete metrics, applies filters, then draws uniformly
without replacement from all eligible candidates. Invalid entries do not count
toward the requested sample size. If the pool has too few eligible videos, the
report shows the actual count and shortfall; increase the candidate limit or
broaden the filters to collect more.

The report includes the pool scope, seed, collection interval, exclusions,
individual sampled videos, and the same performance and engagement averages
as Weekly popular. Reusing a seed repeats the draw only if the eligible pool
and its order are unchanged. Search results and their ranking define this pool;
it is not a uniform sample of all Bilibili. These are current accumulated metrics.
Requests use the configured pacing and stop if the server rejects collection.
The creator dataset is kept separately. Sample rows and report summaries stay in
server RAM and return after a browser refresh; no dataset or report files are written.

## Fetching Nodes on Other Macs

Connect the Macs with a Thunderbolt cable. **Workspace → Nodes** starts a listener
bound only to main's Thunderbolt Bridge address; copy that numeric URL into the
node GUI. Worker connections bind to their local bridge IP and never fall back
to Wi-Fi. Each Mac's normal internet connection still handles Bilibili requests.
An active bridge IPv4 address is required. See [Thunderbolt setup](docs/distributed.md#connect-through-a-thunderbolt-cable).

Pair a Mac once under **Workspace → Nodes**. Then return to **Dataset** and use
the usual **Fetch / Refresh** button. **Fetch on → Automatic** uses an idle
compatible node when ready, or this Mac when nodes are absent, busy, paused,
offline or incompatible. The choice is made again on each refresh. Choose
**Connected node only** to require remote collection, or **This Mac only** for
local collection. **Parallel · this Mac + ready nodes** shares one collection:
the main Mac reads the creator's list pages, then all participating Macs fetch
different video details concurrently. Automatic chooses one Mac per collection;
Parallel explicitly requires at least one ready node with the latest source.
The returned rows fill the usual in-memory working dataset;
Statistics, Ratios and Charts reuse it without another collection. Missions can copy
it into their own retained dataset or fetch a different creator independently.

Set **Collection requests per second → Apply pacing** on Dataset (0.1–4).
Settings shows the same value. Connected nodes honor this rate with their own
local cap; Parallel divides it across the participating Macs. It does not multiply
the selected rate by the number of nodes. Sync, stop and restart older node apps,
then re-pair, to advertise support for pacing and Parallel collections.
Invalid metric rows are replaced until the requested valid count is reached or
the scan limit is reached. A rejection stops collection and preserves the previous
dataset. Only one operation can replace the working dataset at a time.

Transient bottom notices dismiss after four seconds (eight seconds for errors).
Hovering holds a dashboard notice open. Live task progress stays visible while running.

The node GUI includes **Check connection**, which verifies the main listener
without consuming a pairing code. Copy the Thunderbolt Bridge address including the actual
port from the Nodes page. For chat coordination over iCloud, use
[MAIN_MAC.txt](docs/coordination/MAIN_MAC.txt) and
[NODE_MAC.txt](docs/coordination/NODE_MAC.txt), with one chat writing each file.

Open **Workspace → Nodes** to start the separate connection service, generate
one-use pairing codes, and pause/resume or remove nodes. Normal fetching stays
on Dataset; Tasks uses the same automatic routing. The optional **Advanced
collections** section holds the separate queue for links and separate reports.
Fetching Macs run a lightweight app with a local web GUI for pairing, pacing,
pausing and progress. Video links are split into batches across nodes; creator
collections target a valid video count on an assigned node. Returned results show
performance and engagement averages and remain separate from the working dataset.

Copy or clone the source project on each fetching Mac. Double-click
`setup-node.command` once, then `start-node.command`. The node setup installs
HTTPX and its dependencies without Matplotlib; running the node does not import the dashboard
or Matplotlib. Keep each Mac's environment and account files local instead of
copying `.venv`, `venv`, `.runtime`, or personal `objects/creators.json` contents.
See [distributed setup and recovery](docs/distributed.md) for the connection steps,
RAM-only state, supported tasks and direct Thunderbolt connection scope.

## Weekly Popular Averages

Open **Workspace → Data → Sampling → Weekly popular** and paste a Bilibili weekly page URL, such as
`https://www.bilibili.com/v/popular/weekly?num=393`, or enter its issue number.
**Fetch / Analyze issue** collects the list's metric data and shows totals, means,
medians, extrema and P90 for views, likes, replies, favorites, coins and shares.
Engagement includes both the average per-video ratio and the pooled ratio
(total interactions / total views). Zero-view videos are excluded from ratios.

Videos with incomplete list metrics get a detail request; invalid or unavailable
videos are skipped, and repeated IDs count once. The page reports included and
excluded counts and lets you inspect individual videos. It keeps the creator
dataset separately and does not apply its local filters to the weekly cohort.
Navigation retains the report in memory; only the fetch button recollects it.
Metrics describe accumulated performance observed during collection, rather than
performance earned during the issue's week or averages across all Bilibili videos.
No weekly dataset or report files are created.

## Working with a Dataset

Choose a Creator and selection, then click **Fetch / Refresh**. Listing, analysis,
division, and plotting reuse that collection in RAM. Changing the Creator, account,
or fetch selection requires another explicit fetch. The status shows the collection
interval: values are observed sequentially, not at one simultaneous instant.
A failed refresh keeps the previous dataset; summary-page failures are reported.
Refreshing or reopening the webpage restores its video table and fetch selection
from server RAM without collecting again. The latest retained weekly and random
reports also return. If collection is still running, the page reconnects to its
progress and shows the completed dataset. No fetched rows are saved in browser
storage or dataset files. Charts and analysis can be rerun on the restored data.
Collection skips videos without all six valid, nonnegative whole-number metrics
(views, likes, replies, favorites, coins, shares); zero remains valid.
For a number range, Start is the original publication position and End − Start + 1
is the target number of valid videos. Collection continues to older videos until
that target is met: requesting 1–100 with two invalid videos checks 102 and keeps 100.
Repeated video IDs do not count twice. If available videos run out, the status
reports the shortfall, alongside checked, skipped, and requested counts.
Date and metric selections skip invalid videos without extending their boundaries.
Server rejections stop collection and preserve the previous dataset.
Zero denominators can still prevent ratio plots even when all metrics are present.

Local minimum/maximum view filters are inclusive and run without network requests.
They only narrow the fetched selection; clear both to include every fetched row.
Creator dataset and sampling video tables show 50 rows per page. Previous/Next
changes only the displayed rows; analysis, chart calculations and snapshot saves
still use the whole selected collection. Included/skipped sampling lists are
rendered when expanded, and switching pages requires no network requests.
In Statistics, **Chart metric** updates the selected metric's summary, histogram,
box plot, and unusual values locally. Run **Analyze Dataset** first to enable it.
The all-metrics comparison remains available in a separate expandable table.

Quartiles and P90 interpolate at `(n - 1) * percentile`. Outliers are values outside
`Q1 - 1.5 * IQR` and `Q3 + 1.5 * IQR`, flagged only with at least four valid values.
Duplicates remain included and are reported. Missing, invalid, and negative counts
are excluded per metric; zero remains valid. Engagement ratios exclude zero views
and missing pairs. The fewer-than-100-views flag is descriptive and does not remove rows.
Pooled engagement weights videos by views; median engagement describes the typical
per-video ratio. Neither measures unique-user conversion or current growth.

Dataset reuse and analysis create no data, image, or cache files. A successful fetch
replaces the previous dataset; stopping or restarting Python clears it.
The dashboard shares one dataset across local browser tabs.

## Development Reload

Both main and development modes update the browser automatically when source changes:
CSS updates in place. HTML and JavaScript refresh the page after the current browser
task and collection finish. The active page, sampling tab, analysis source, form
values and scroll position are restored. Passwords and pairing codes are excluded.
The independent browser watcher can recover after fixing a syntax error in `app.js`.
Interface updates keep the Python process, RAM datasets/cohorts and node connections.
Browser-rendered analysis results need running again on the retained data.

Use `start-web.command` for automatic Python restarts too. Saving Python files under
`ascendant/` or root compatibility scripts restarts the development worker.
This clears its RAM datasets, coordinator state, in-progress requests and QR attempts.
The supervisor survives Python syntax errors and retries after the next save.
With `start-main.command`, Python edits show a restart notice and leave the backend
running. Changes to the supervisor itself require restarting the launcher.
Updating `objects/creators.json` refreshes the Creator list without a server restart.
Caches, plots, and virtual environments do not trigger restarts.
This refreshes edited source files, not Bilibili data; fetch data again explicitly
to get updated counts.

Disable automatic Python restarts or choose another port with:

```sh
python -m ascendant.web --no-reload --port 8010
```

The dashboard groups related tools under Explore, Workspace, and Settings.
Every page uses the same centered workspace. **Change creator** in dataset setup
or the creator profile opens a
searchable dropdown for quick switching. Select a creator to close it, or dismiss
it with Escape or a click outside. Add / manage creators opens the Creators tab
under Explore. Creator selection appears only on Creator dataset and Creator profile.

Use the Back button to return to the previously visited menu view. Clicking outside
an open dropdown closes it. Navigation
history is kept in memory and resets on reload; existing form values and results
stay available when switching views. Wide tables scroll within their own areas; charts resize to the viewport.

## Tests and Preview

Run the offline regression suite:

```sh
.venv/bin/python -m unittest discover -s tests -t . -v
```

Tests use temporary files and mocked Bilibili responses. HTTP and reload tests
open temporary localhost ports. They do not sign in or contact Bilibili.
See [stability checks and measured results](docs/stability-check.md) for the
repeatable offline resource soak and shutdown regression checks.

With Playwright and Chrome installed, run `node tests/browser/plots.cjs` for
offline chart interaction, manual-save, PNG-download, and responsive-layout checks.
Run `node tests/browser/navigation.cjs` to check that switching pages, Sampling
tabs and Back navigation preserve scroll position on desktop and mobile.
Run the analysis browser checks with `node tests/browser/analysis.cjs`. They verify
local chart changes, filter payloads, and desktop/mobile layout using sample data.
Run `node tests/browser/performance.cjs` for 50-row pagination, lazy video lists,
whole-collection analysis, keyboard focus and tracking revision checks.
Run `node tests/browser/batch.cjs` for multiple creator missions, retained datasets,
explicit saves, results, reload recovery and responsive Tasks layout. Python mission
integration tests also cover failure handling and stop/resume behavior.
Run `node tests/browser/nodes.cjs` for pairing through two node web GUIs,
distributed result collection, targeting, creator tasks, revocation and layout.

Set `PLAYWRIGHT_MODULE` to a Playwright package path when it is not in Node's
normal lookup path. Screenshots are written to a temporary directory.

Preview the dashboard using sample data only:

```sh
.venv/bin/python scripts/preview_dashboard.py
```

The preview defaults to [http://127.0.0.1:8012](http://127.0.0.1:8012);
`--port` selects a different port. Account-changing actions are disabled.

## Saved Data

Application data uses these locations and formats:

- `objects/creators.json`: permanent Creator identities only.
- `.runtime/accounts/`: cached account credentials.
- `.runtime/active_account.json`: active account selection.
- `.runtime/bilibili_credential.json`: legacy credential cache.
- `.runtime/bilibili_cookie.txt`: optional local cookie input.
- `.runtime/bilibili_qrcode.png`: current sign-in QR image.
- `.runtime/plots/`: explicitly saved PNG plots.

A Creator file has this format:

```json
{
  "creators": [
    {
      "name": "Example",
      "space": "https://space.bilibili.com/123456",
      "uid": "123456"
    }
  ]
}
```

`selected_uid` is kept in memory, not saved in this file. Credentials, cookies,
QR images, and fetched profile details do not belong in the Creator list.
Generated runtime files are ignored by Git. Do not commit credentials.

## Useful Endpoints

- `/`: dashboard.
- `/static/`: public CSS and JavaScript only.
- `/api/health`: selected Creator, account summary, and request rate.
- `POST /api/weekly-analysis`: collect a weekly issue and calculate cohort averages.
- `POST /api/random-sample`: collect a bounded, filtered keyword-search pool and return a random video sample with averages.
- `/api/nodes`: local-only node service, paired Macs and collection queue.
- `/api/nodes/result?id=...`: local-only received videos and cohort analysis.
- `POST /api/nodes/service`, `/api/nodes/pairing`, `/api/nodes/action`: local node connection controls.
- `POST /api/nodes/tasks`, `/api/nodes/tasks/action`: queue, cancel or clear distributed collections.
- `/api/creators`: saved Creator list.
- `/api/progress`: current operation progress.
- `/api/plots`: saved PNG plot list.
- `POST /api/video-lookup`: temporary video lookup using a `video` URL or BV ID.
- `POST /api/video-action`: listing, analysis, division, or plotting.
- `POST /api/plots/save`: explicitly save a generated plot using its `plot_id`.
- `/plots/<file>.png`: generated plot images.

## API Source and License

Bilibili access uses the application-owned HTTPX adapter in `ascendant/api/`;
`bilibili-api-python` is no longer a dependency. The endpoint subset from version
17.4.1 and its GPL-3.0 license are preserved under
[`docs/reference/bilibili-api-python-17.4.1/`](docs/reference/bilibili-api-python-17.4.1/README.md).
See [migration and validation notes](docs/httpx-migration.md) for maintenance and checks.
Interactive plots use locally bundled [Apache ECharts 5.6.0](https://echarts.apache.org/handbook/en/concepts/axis/).
Selected Lucide 1.8.0 icons are bundled locally. Upstream licenses and notices
are in `docs/licenses/`; there is no runtime CDN dependency.

GPL-3.0 license. See [LICENSE](LICENSE).

Copyright (c) 2026 ChinE4226

All rights reserved.
