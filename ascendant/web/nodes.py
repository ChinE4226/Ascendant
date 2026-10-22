"""Local dashboard controls for the separate node connection service."""

from urllib.parse import parse_qs

from ascendant.distributed.coordinator import COORDINATOR
from ascendant.distributed.protocol import json_request, local_operator, integer
from ascendant.web.http import read_json_body


def node_route(handler, parsed, method):
    if not parsed.path.startswith('/api/nodes'):
        return False
    try:
        local_operator(handler)
        path = parsed.path
        if method == 'GET' and path == '/api/nodes':
            result = COORDINATOR.snapshot()
        elif method == 'GET' and path == '/api/nodes/result':
            result = COORDINATOR.result(parse_qs(parsed.query).get('id', [''])[0])
        elif method == 'POST':
            json_request(handler)
            data = read_json_body(handler)
            if path == '/api/nodes/service':
                if data.get('action') == 'start':
                    result = COORDINATOR.start(integer(data.get('port', 8010), 'Connection port', 65535))
                elif data.get('action') == 'stop':
                    COORDINATOR.stop()
                    result = COORDINATOR.snapshot()
                else:
                    raise ValueError('Choose start or stop for node connections.')
            elif path == '/api/nodes/pairing':
                result = COORDINATOR.new_pairing()
            elif path == '/api/nodes/action':
                COORDINATOR.node_action(data.get('id'), data.get('action'))
                result = COORDINATOR.snapshot()
            elif path == '/api/nodes/tasks':
                result = COORDINATOR.create_job(data)
            elif path == '/api/nodes/tasks/action':
                COORDINATOR.task_action(data.get('id'), data.get('action'))
                result = COORDINATOR.snapshot()
            else:
                handler.send_error_json(404, 'Not found.')
                return True
        else:
            handler.send_error_json(404, 'Not found.')
            return True
        handler.send_json(result)
    except (ValueError, OSError) as exc:
        handler.send_error_json(getattr(exc, 'status', 400), str(exc))
    return True
