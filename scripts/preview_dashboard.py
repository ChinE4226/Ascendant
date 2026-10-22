"""Local-only layout preview with sample data; never calls Bilibili or writes accounts."""

import argparse
import asyncio
import io
import os
import tempfile
import atexit
from datetime import datetime, timedelta
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
preview_data = tempfile.TemporaryDirectory(prefix='bilibili-layout-preview-')
atexit.register(preview_data.cleanup)
os.environ['BILIBILI_TRACKING_DB'] = str(Path(preview_data.name) / 'tracking.sqlite3')
from ascendant.distributions import analyse_dataset
from ascendant.weekly import summarize_weekly_items
from ascendant.sampling import parse_sample_options, sample_candidates
from ascendant.plotting import plt
from ascendant.web.assets import dashboard_html
from ascendant.web.http import read_json_body
from ascendant.web.routes import BilibiliDataScienceHandler
from ascendant.web.serializers import VIDEO_FIELDS, extract_bvid, serialize_video
from ascendant.web.server import DashboardHTTPServer
from ascendant.web.weekly import parse_weekly_source
from ascendant.web import dataset
from ascendant.web.actions import execute_video_action
from ascendant.web import creators, distributed_dataset


async def fetch_mission_preview(payload):
    """Mission integration fixture with distinct targets; never contacts Bilibili."""
    await asyncio.sleep(.3)
    creator = creators.selected_creator()
    selection = payload['selection']
    count = min(3, selection.get('end', 3) - selection.get('start', 1) + 1)
    rows = [{'title': f"Creator {creator['uid']} · video {index + 1}",
             'bvid': f'BV1xx411c{7 if creator["uid"] == "123456" else 8}m{chr(68 + index)}', 'pubdate': 1788307200 + index * 86400,
             'owner': {'mid': int(creator['uid']), 'name': creator['name']},
             'stat': {'view': int(creator['uid']) + index * 100, 'like': 10, 'reply': 2, 'favorite': 3, 'coin': 4, 'share': 1}}
            for index in range(count)]
    return rows, f'First {count} videos', 500, {'requested': count, 'examined': count, 'skipped_invalid': 0, 'skipped_duplicates': 0}


distributed_dataset.fetch_workspace_items = fetch_mission_preview


VIDEO = {
    "title": "A longer sample video title: comparing camera details and everyday recording quality",
    "owner_name": "Sample uploader", "owner_mid": 123456, "published_time": "2026-09-18 12:30:00",
    "duration": "12:45", "category": "Technology", "bvid": "BV1xx411c7mD", "aid": 170001,
    "page_count": 1, "width": 1920, "height": 1080, "views": 1200034,
    "likes": 24001, "replies": 864, "favorites": 9375, "coins": 3072, "shares": 0,
    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
    "description": "Sample description for layout verification.\n" + "LongUnbrokenText" * 15,
}
VIDEOS = [dict(VIDEO, title=f"{index + 1}. {VIDEO['title']}") for index in range(24)]
CREATOR = {"name": "Sample uploader", "uid": "123456"}
POINTS = [{"title": f"Video {i + 1}: sample metric history", "label": (datetime(2026, 3, 1) + timedelta(days=i * 3)).strftime("%Y-%m-%d %H:%M:%S"),
           "value": 18000 + ((i * 7919) % 43000) + i * 300} for i in range(64)]
figure, axis = plt.subplots(figsize=(13, 7))
axis.plot([point["label"] for point in POINTS], [point["value"] for point in POINTS])
axis.set_xlabel("Published time")
axis.set_ylabel("Views")
figure.tight_layout()
buffer = io.BytesIO()
figure.savefig(buffer, format="png")
plt.close(figure)
PNG = buffer.getvalue()


class PreviewHandler(BilibiliDataScienceHandler):
    request_frequency = 4.0
    saved = False
    simulated_http_error = None
    simulated_failure = None

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith(('/api/nodes', '/api/missions')) or path in {'/api/collections', '/api/dev-version', '/api/workspace-data', '/api/memory'}:
            super().do_GET()
            return
        if path == "/":
            self.send_bytes(dashboard_html(), "text/html; charset=utf-8")
            return
        if path.startswith("/static/") or path == "/favicon.ico":
            super().do_GET()
            return
        if path == "/plots/sample.png":
            self.send_bytes(PNG, "image/png")
            return
        fixtures = {
            "/api/health": {"memory_usage_version": 1, "mission_queue_version": 1, "workspace_restore_version": 1, "chart_export_version": 3, "collection_analysis_version": 1, "account": "Layout preview (sample data)", "selected_creator": CREATOR, "request_frequency": self.request_frequency},
            "/api/creators": {"creators": [CREATOR, {"name": "Another uploader with a longer name", "uid": "987654"}]},
            "/api/accounts": {"accounts": [{"name": "Sample account", "uid": "555555", "id": "sample", "source": "qr", "active": True}]},
            "/api/plots": {"plots": [{"url": "/plots/sample.png", "name": "Sample_views_by_published_time.png", "size": len(PNG)}] if self.saved else []},
            "/api/progress": {"message": self.simulated_failure or "Completed.", "running": False, "count": 0 if self.simulated_failure else 24},
            "/api/account-detail": {"name": "Sample account", "mid": 555555, "level": 5, "coins": 34, "following": 120, "follower": 17, "sign": "Sample account signature"},
            "/api/creator-detail": {"selected": CREATOR, "profile": {"name": CREATOR["name"], "mid": 123456, "sign": "Long profile text " * 15, "upstat": {"archive": {"view": 123456789}, "likes": 654321}}, "relation": {"follower": 100000, "following": 45}, "video_total": 500},
        }
        if path in fixtures:
            self.send_json(fixtures[path])
        else:
            self.send_error_json(404, "Preview endpoint not available.")

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith(('/api/nodes', '/api/missions')) or path == '/api/collections/release':
            super().do_POST()
            return
        data = read_json_body(self)
        if path == '/api/video-action' and self.simulated_http_error is not None:
            from ascendant.errors import request_reason
            type(self).simulated_failure = f'Video collection stopped. {request_reason(status=self.simulated_http_error)}'
            self.send_error_json(400, self.simulated_failure)
            return
        if path == "/api/request-frequency":
            from ascendant.distributed.protocol import frequency
            try:
                from ascendant import state as settings
                type(self).request_frequency = frequency(data.get('value'))
                settings.REQUEST_FREQUENCY = self.request_frequency
                self.send_json({'request_frequency': self.request_frequency})
            except ValueError as exc:
                self.send_error_json(400, str(exc))
        elif path == "/api/video-lookup":
            try:
                extract_bvid(data.get("video"))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            self.send_json({"video": VIDEO})
        elif path == "/api/random-sample":
            try:
                options = parse_sample_options(data)
                raw = [{"title": f"Sample video {i + 1}", "bvid": f"random-{i}", "pubdate": int(datetime(2026, 9, 1).timestamp()) + i * 3600,
                        "owner": {"mid": i % 4, "name": f"Creator {i % 4 + 1}"},
                        "stat": {field["stat_key"]: (i + 1) * 100 if field["field"] == "views" else i * 2 for field in VIDEO_FIELDS}}
                       for i in range(options["pool_size"])]
                sampled, summary = sample_candidates(raw, options)
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            summary["sampling"].update({"checked": len(raw), "pages": (len(raw) + 19) // 20})
            s = summary['sampling']
            report = {**summary, 'started_at': '2026-10-03T00:00:00Z', 'collected_at': '2026-10-03T00:00:01Z'}
            meta = dataset.retain_collection(sampled, kind='random', label=f"Random sample · {options['keyword']}",
                started_at='2026-10-03T00:00:00Z', collected_at='2026-10-03T00:00:01Z', scope=s, report=report,
                collection={'requested': s['sample_size'], 'examined': s['candidates'], 'eligible': s['eligible'],
                            'skipped_invalid': s['invalid'], 'skipped_duplicates': s['duplicates'],
                            'collection_filtered': s['filtered_out'], 'shortfall': s['shortfall']})
            self.send_json({**summary, "started_at": "2026-10-03T00:00:00Z", "collected_at": "2026-10-03T00:00:01Z",
                "dataset": meta, "videos": [{**serialize_video(row), "creator": row["owner"]["name"]} for row in sampled]})
        elif path == "/api/weekly-analysis":
            try:
                issue = parse_weekly_source(data.get("source"))
            except ValueError as exc:
                self.send_error_json(400, str(exc))
                return
            raw = [{"title": f"Weekly video {i + 1}", "bvid": f"weekly-{i}", "pubdate": int(datetime(2026, 9, 1).timestamp()) + i * 86400,
                    "owner": {"mid": i % 4, "name": f"Creator {i % 4 + 1}"},
                    "stat": {field["stat_key"]: (i + 1) * 100 if field["field"] == "views" else i * 2 for field in VIDEO_FIELDS}}
                   for i in range(24)]
            _, summary = summarize_weekly_items(raw)
            report = {**summary, 'issue': {'number': issue, 'name': f'Weekly issue {issue}', 'subject': 'Sample popular videos',
                'url': f'https://www.bilibili.com/v/popular/weekly?num={issue}'},
                'started_at': '2026-10-03T00:00:00Z', 'collected_at': '2026-10-03T00:00:01Z'}
            meta = dataset.retain_collection(raw, kind='weekly', label=f'Weekly popular · Weekly issue {issue}',
                started_at='2026-10-03T00:00:00Z', collected_at='2026-10-03T00:00:01Z', scope={'number': issue}, report=report,
                collection={'requested': 24, 'examined': 24, 'skipped_invalid': 0, 'skipped_duplicates': 0, 'shortfall': 0})
            self.send_json({**summary, "issue": {"number": issue, "name": f"Weekly issue {issue}", "subject": "Sample popular videos",
                "url": f"https://www.bilibili.com/v/popular/weekly?num={issue}"},
                "started_at": "2026-10-03T00:00:00Z", "collected_at": "2026-10-03T00:00:01Z",
                "dataset": meta, "videos": [{**serialize_video(row), "creator": row["owner"]["name"]} for row in raw]})
        elif path == "/api/video-action":
            if data.get('collection_id'):
                try:
                    self.send_json(asyncio.run(execute_video_action(data)))
                except ValueError as exc:
                    self.send_error_json(400, str(exc))
                return
            meta = {"count": 24, "uid": CREATOR['uid'], "source_kind": 'creator', "source_label": CREATOR['name'],
                    "selection": "Sample selection", "started_at": "2026-09-30T00:00:00Z", "collected_at": "2026-09-30T00:00:10Z", "reused": not data.get("refresh")}
            raw = [{'title': row['title'], 'bvid': row['bvid'], 'pubdate': int(datetime(2026, 9, 18, 12, 30).timestamp()),
                    'stat': {field['stat_key']: row[field['field']] for field in VIDEO_FIELDS}} for row in VIDEOS]
            dataset.CURRENT = {'key': dataset.context_key(data), 'meta': meta, 'data': (raw, 'Sample selection', 24)}
            self.send_json({
                "videos": VIDEOS, "points": POINTS, "y_label": "Views", "plot_id": "sample", "selected_creator": CREATOR, "selection": "All videos",
                "mode": data.get("mode"), "numerator_total": 24001, "denominator_total": 1200034, "ratio": 0.02,
                "rows": [{"title": item["title"], "published_time": item["published_time"], "numerator": 24001, "denominator": 1200034, "ratio": 0.02} for item in VIDEOS],
                **analyse_dataset([{"title": item["title"], "bvid": f"sample-{i}",
                    "stat": {field["stat_key"]: (i + 1) * 100 if field["field"] == "views" else i * 2 for field in VIDEO_FIELDS}}
                    for i, item in enumerate(VIDEOS)]),
                "dataset": meta,
            })
        elif path == "/api/plots/save" and data.get("plot_id") == "sample":
            type(self).saved = True
            self.send_json({"name": "sample.png", "url": "/plots/sample.png"})
        else:
            self.send_error_json(400, "Sample error: this action is disabled in the layout preview.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument('--simulate-http-error', type=int, choices=(401, 403, 412, 429, 500),
                        help='Simulate a Bilibili rejection for dataset actions without making network requests.')
    args = parser.parse_args()
    PreviewHandler.simulated_http_error = args.simulate_http_error
    server = DashboardHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"Sample layout preview: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
