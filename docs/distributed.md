# Ascendant main app and fetching nodes

The source project can run as a main dashboard or as a lightweight fetching node
with its own local web GUI. Double-click launchers are included for both roles;
no distribution package is needed.
Python 3.10 or newer is required. No node database, dataset cache, or report files
are created. Queues, pairing credentials and results are kept in process memory.

## Install and connect

1. On the main Mac, open the project folder. Double-click `setup-main.command`
   once to create its Python environment and install dependencies; skip this if
   the checkout already has a working environment. Double-click
   `start-main.command` to start the dashboard in Google Chrome at
   `http://127.0.0.1:8000`.
2. Open **Workspace → Nodes**. Choose an unused connection port (default 8010)
   and click **Start node connections**. This starts a separate listener on the
   Mac's Thunderbolt Bridge address; it exposes only the node protocol, not the dashboard
   or account controls. If macOS asks whether Python may accept incoming
   connections, allow it for this service.
3. Copy or clone the source project on each fetching Mac. Do not copy another
   Mac's `.venv`, `venv`, `.runtime`, or personal `objects/creators.json` contents.
   Double-click `setup-node.command` once, then `start-node.command`.
   Its GUI opens in Google Chrome at
   `http://127.0.0.1:8011`. The node installs HTTPX and its dependencies;
   its setup does not install Matplotlib and its runtime does not load the main
   dashboard or chart library.
   If a node is already running, the launcher opens its existing GUI and preserves
   its pairing and active task. If another app occupies port 8011, a new node uses
   the next free port and opens that address in Chrome.
4. In that GUI, enter a node name, a main-Mac connection URL from the Nodes page,
   a fresh pairing code, and pacing from 0.1 to 4 requests/second (default 1).
   Use the main Mac's numeric Thunderbolt Bridge IP. `127.0.0.1` works only when
   both processes are on the same Mac. Click **Pair / Connect**.
5. Each code is valid for five minutes and pairs one node. Use **New pairing
   code** before pairing another Mac. A node keeps its credential in memory and
   automatically reconnects after temporary network interruptions.

Before pairing, use **Check connection** in the node GUI. This checks the actual
listener and supported features without consuming the code. The main Nodes page
shows the bound Thunderbolt Bridge address and a **Copy address** button, including
self-assigned `169.254.x.x` addresses. The listener does not accept Wi-Fi connections.
If the cable is unplugged, the page reports its address unavailable; replugging
at the same IP preserves pairings/tasks. If the bridge IP changes, stop/start the
connection service and re-pair at its new address. A changed
connection port must also change the URL entered on the fetching Mac.

## Connect through a Thunderbolt cable

Connect the two Macs using a Thunderbolt cable and Thunderbolt-capable ports.
In macOS **System Settings → Network → Thunderbolt Bridge**, check that each
Mac has a bridge IPv4 address and the link is active. A self-assigned address is
usable for the direct link; the app no longer hides these addresses.

Start connections on main, then copy the **Thunderbolt Bridge** address into
the node GUI. Use its numeric IP, including the current listener port, instead
of the Mac hostname: hostname resolution can select Wi-Fi. Run **Check connection**,
then pair with a fresh code. If moving a paired node from Wi-Fi, wait until its
task finishes, disconnect in the node GUI, and pair again at the bridge URL.

The existing HTTP node protocol runs over the cable; Bluetooth pairing is not
used. Main binds its listener to the active bridge IPv4 address. Node HTTP/HTTPS
connections bind their source to the local bridge IP, so overlapping link-local
routes cannot silently select Wi-Fi. A missing bridge fails explicitly; there is
no Wi-Fi fallback. Tasks, heartbeats and results take the direct bridge route, while Bilibili
requests use each Mac's normal internet route. Keep Internet Sharing disabled
and leave the bridge's router/gateway blank if configuring addresses manually.
Different Wi-Fi names alone do not prove separate public internet addresses.
No internet routes or network settings are changed automatically by the app.

New setup commands use `~/Library/Application Support/BilibiliDataScience/.venv`
on each Mac. Both `start-main.command` and `start-web.command` choose the same
interpreter and account runtime. The default is a local `runtime` directory beside
the environment. If that main runtime has no cached sign-in and the checkout's
`.runtime` does, both main launchers reuse the checkout cache without copying it.
Nodes always default to their Mac-local runtime. `BILIBILI_RUNTIME_DIR` overrides
the selection. Keep legacy `.venv`, `venv` and `.runtime` folders out of a source-only
iCloud sync. The two small files in `docs/coordination/` are the shared chat notes;
each chat writes only its own file. Runtime connections and results use the Thunderbolt cable.

## Fetch from the usual page

Once a node is paired, choose the Creator and collection range on **Dataset**
and press **Fetch / Refresh** as usual. Automatic is the default: it uses an idle
compatible node, or this Mac if none is available (absent, busy, paused, offline,
incompatible, or the node queue is full). It chooses again on every refresh.
**This Mac only** and **Connected node only** remain explicit overrides; the
latter reports unavailable nodes instead of falling back. The Dataset status
shows which Mac actually fetched the rows.

For simultaneous collection, choose **Parallel · this Mac + ready nodes**. The
main Mac reads creator list pages; main and nodes fetch different video details
concurrently, then assemble one ordered working dataset. Invalid rows are replaced
within the same collection. At least one idle node with `videos` and `pacing`
capabilities is required. The usual bounded number/time/metric selections apply.
Dataset operations remain serialized because they replace one shared dataset;
Parallel shares that operation across Macs rather than starting competing refreshes.

**Collection requests per second → Apply pacing** on Dataset and the Settings
control edit the same rate (0.1–4). Normal remote collections use the lower of
this rate and the node's local cap. Parallel divides the selected rate across
participants, then applies each node's local cap. Older nodes without `pacing`
support are excluded when they cannot honor the selected rate. Sync the source,
stop their Terminal process with Control+C, restart `start-node.command`, and
re-pair to load these capabilities. Double-clicking while a node is running only
opens its existing GUI; it does not reload Python or upgrade its capabilities.

If a selected node becomes unavailable before its first claim, Automatic cancels
that unstarted job and fetches locally. Once work starts, errors stop the refresh
and preserve the previous dataset; failed Bilibili requests are not retried on
another Mac. Analysis reuses a loaded dataset regardless of node availability.
The same failure rule applies to Parallel: stop remaining detail work and keep
the previous dataset. An HTTP 412 still means Bilibili rejected a request; matching
launcher environments removes a sign-in mismatch but cannot guarantee acceptance.

Number ranges support 1–500 valid videos with replacements for invalid metrics,
bounded by 2,500 examined candidates and 84 summary pages. A scan-limit shortfall
is shown explicitly. Published-time and metric ranges keep their existing
boundaries; remotely they support creators with at most 2,500 total videos.
Automatic uses this Mac for number ranges above 500 and legacy selection shapes.
Use number ranges or This Mac only for larger creator catalogs in time/metric mode.

Results enter the same working dataset used by Statistics, Ratios, Charts and
batch tasks. Analysis runs on the main Mac. Follower-count ratios still query the
main Mac's account for the current follower count. Sampling remains a local
workflow. Nodes is used to connect and manage Macs, and its manual collection
queue remains available inside the collapsed **Advanced collections** section
for links and separate reports. It is not required for Dataset or batch tasks.

The connection listener uses HTTP on the direct Thunderbolt link;
it does not provide transport encryption or public-internet hosting. Node
tokens authenticate requests but do not encrypt them. The local control GUIs
reject non-loopback clients and requests from another website's origin. Worker
requests never follow HTTP redirects or use environment HTTP proxies.

The setup commands install into a local Python environment with pip's download
cache disabled. They do not build archives or copy datasets. To start from a
terminal instead of double-clicking, run:

```sh
.venv/bin/python -B -m bilibili_ds.web --no-reload
.venv/bin/python -B -m bilibili_ds.node --open-browser
```

Keep each Terminal window open; press **Control+C** in it to stop that app.
Closing only the browser tab leaves the app running. `start-main.command`
keeps Python stable while interface edits refresh the browser automatically.
CSS/HTML/JavaScript updates preserve node connections and RAM datasets.
Use `start-web.command` for automatic Python restarts during development;
Python edits restart the main process and clear coordinator state and pairings.

## Assign collections

**Video links / BV IDs** accepts up to 1,000 unique videos, one per line or
separated by spaces/commas. The coordinator splits them into batches of 20, so
several fetching Macs can share one task. Select target nodes in the table or
leave every checkbox unchecked to use any eligible paired node. Offline or
paused targets remain queued until available. Removing an explicitly targeted
node does not silently redirect its work to an unselected Mac; cancel that task
and create it again with different targets.

**Creator · valid video count** assigns one creator collection to one eligible
node. Enter its UID, a start publication position, and up to 500 valid videos.
The node continues past invalid videos until it reaches that count, the source
ends, or the limit of 2,500 unique candidates / 84 summary pages is reached.
Incomplete metrics are excluded; zero values remain valid. Shortfalls are shown.

The main page shows node availability, progress, queued/running/completed tasks,
valid counts, invalid counts and results. **View results** calculates totals,
means, medians, ranges, P90 and equal-weight/pooled engagement ratios using the
existing analysis code. Reopen it to update a partial report. Distributed results
remain separate from the selected creator's local working dataset.

The main Mac can pause new assignments, resume a node, revoke its credential
with **Remove**, cancel a collection, and clear finished tasks from RAM. The
node GUI can pause after its current task, resume local fetching and disconnect
when no task/result submission is active. Its browser can close while the
process keeps working. Pairing settings and pacing are set when connecting.

Sign-in is optional. A node can use its own environment credential/local cookie
file or a cookie entered into its GUI. The entered cookie is held only in that
node's memory; it is cleared from the input after pairing and is never returned
to the coordinator. Keep credentials local to each Mac. Bilibili rejection stops
the task and pauses the
fetching node; there is no protection bypass or automatic retry of rejected
Bilibili requests.
Creator-list failures report the page, API code or HTTP status, transport/timeout
category and count collected before failure. Raw upstream responses are omitted.
The main error identifies the fetching Mac and keeps the task marked failed;
the previous working dataset remains available. The HTTPX adapter does not automatically retry WBI signing rejections.

## Recovery and limits

- Nodes poll for work and heartbeat while collecting; no incoming connection
  to a fetching Mac is needed.
- Work has a 60-second lease, renewed by heartbeats. A node is displayed offline
  after 20 seconds without contact. Expired work can be reassigned. Three expired
  attempts fail the task.
- Each attempt has a unique lease token. Results from an expired/canceled
  attempt are rejected. Repeating an already accepted completion is harmless.
- Pending results stay in the node's RAM and retry delivery after connection
  interruptions. Failed/canceled collections preserve previously accepted work.
- Twenty paired nodes and thirty retained tasks are supported. Clear finished
  tasks or remove unused nodes to release memory.
- Restarting the main app clears nodes, tasks and results. Restarting a node
  clears its pairing and any undelivered results. Pair again after a restart.
- Request pacing is per node, not a cluster-wide quota. The first version
  supports video batches and creator collections; weekly/random collection
  buttons continue to use the main app's local workflows.
