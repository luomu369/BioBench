"""HTTP regressions; no GPU, model downloads, or external API calls."""
import concurrent.futures
import http.client
import json
import os
import io
from contextlib import redirect_stderr
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webapp'))
import serve

class QuietHandler(serve.Handler):
    def log_message(self, *args):
        pass

class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.patches = [patch.object(serve, 'STATIC', cls.root),
                       patch.object(serve, 'SELECTION', cls.root / 'selection.json')]
        for p in cls.patches:
            p.start()
        cls.server = serve.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def request(self, body=b'{}', headers=None, path='/selection.json', method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_selection_roundtrip(self):
        self.assertEqual(self.request(b'{"chain":"A"}')[0], 200)
        self.assertEqual(json.loads((self.root / 'selection.json').read_text()), {'chain': 'A'})

    def test_invalid_json(self):
        for body in [b'{', b'\xff', b'[]', b'null', b'42', b'"text"']:
            with self.subTest(body=body):
                self.assertEqual(self.request(body)[0], 400)

    def test_invalid_lengths(self):
        for value in ['-1', 'abc', '+2']:
            with self.subTest(value=value):
                self.assertEqual(self.request(headers={'Content-Length': value})[0], 400)

    def test_oversize_body_rejected_before_read(self):
        self.assertEqual(self.request(headers={'Content-Length': '1000001'})[0], 413)

    def test_transfer_encoding_rejected(self):
        self.assertEqual(self.request(headers={'Transfer-Encoding': 'chunked'})[0], 400)

    def test_duplicate_length_rejected(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.putrequest('POST', '/selection.json')
            conn.putheader('Content-Length', '2')
            conn.putheader('Content-Length', '2')
            conn.endheaders(b'{}')
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            conn.close()

    def test_agent_routes_reject_arrays(self):
        for path in ['/agent', '/agent/session', '/agent/permission',
                     '/agent/auto-approve', '/stage-image']:
            with self.subTest(path=path):
                self.assertEqual(self.request(b'[]', path=path)[0], 400)

    def test_delete_rejects_invalid_body(self):
        self.assertEqual(self.request(b'[]', path='/task.json', method='DELETE')[0], 400)

    def test_concurrent_selections(self):
        bodies = [json.dumps({'selection': i}).encode() for i in range(24)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda body: self.request(body)[0], bodies))
        self.assertEqual(statuses, [200] * len(bodies))
        self.assertIn(json.loads((self.root / 'selection.json').read_text())['selection'], range(24))
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_huge_content_length(self):
        self.assertEqual(self.request(headers={'Content-Length': '9' * 5000})[0], 413)

    def test_zero_padded_content_length(self):
        self.assertEqual(self.request(headers={'Content-Length': '0' * 5000 + '2'})[0], 200)

    def test_delete_field_types_leave_state_unchanged(self):
        path = self.root / 'status.json'
        original = '{"jobs": []}'
        path.write_text(original)
        for field in ['group', 'structure']:
            for value in [123, [], {}, False]:
                with self.subTest(field=field, value=value):
                    status, body = self.request(json.dumps({field: value}).encode(),
                                                path='/task.json', method='DELETE')
                    self.assertEqual(status, 400)
                    self.assertIn(field, json.loads(body)['error'])
                    self.assertEqual(path.read_text(), original)

    def test_invalid_session_id_has_no_side_effect(self):
        with patch('agent.use_session') as use, patch('agent.new_session') as new:
            for value in [123, [], {}, False, '', '   ']:
                with self.subTest(value=value):
                    self.assertEqual(self.request(json.dumps({'id': value}).encode(),
                                                  path='/agent/session')[0], 400)
            use.assert_not_called()
            new.assert_not_called()

    def test_invalid_image_does_not_clear_backdrop(self):
        with patch('agent.set_stage_image') as setter:
            for value in [123, [], {}, False]:
                with self.subTest(value=value):
                    self.assertEqual(self.request(json.dumps({'image': value}).encode(),
                                                  path='/stage-image')[0], 400)
            setter.assert_not_called()

    def test_empty_image_still_clears_backdrop(self):
        with patch('agent.set_stage_image', return_value=None) as setter:
            self.assertEqual(self.request(b'{"image":""}', path='/stage-image')[0], 200)
            setter.assert_called_once_with('')

    def test_null_session_still_creates_conversation(self):
        with patch('agent.new_session') as new, patch('agent.history', return_value=[]), \
             patch('agent.current_session_id', return_value='new-session'), \
             patch('agent.session_busy', return_value=False):
            self.assertEqual(self.request(b'{"id":null}', path='/agent/session')[0], 200)
            new.assert_called_once_with()

    def test_missing_selection_request_is_empty(self):
        self.assertEqual(self.request(path='/select.json', method='GET'), (204, b''))

class GpuMonitorTests(unittest.TestCase):
    def test_optional_monitor_is_not_run_by_default(self):
        with patch.dict(os.environ, {}, clear=True), patch('serve.subprocess.run') as run:
            self.assertEqual(serve._gpu(), {})
            run.assert_not_called()

    def test_configured_monitor_returns_metrics(self):
        with patch.dict(os.environ, {'BIOBENCH_GPU_MONITOR': 'gpu-monitor'}), \
             patch('serve.shutil.which', return_value='/tools/gpu-monitor'), \
             patch('serve.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '40, 1024, 4096\n'
            self.assertEqual(serve._gpu(), {'util_percent': 40.0, 'mem_used_gb': 1.0,
                                          'mem_total_gb': 4.0, 'mem_percent': 25.0})
            self.assertEqual(run.call_args.args[0][0], 'gpu-monitor')
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_unavailable_monitor_is_hidden(self):
        with patch.dict(os.environ, {'BIOBENCH_GPU_MONITOR': 'missing'}), \
             patch('serve.shutil.which', return_value=None):
            self.assertEqual(serve._gpu(), {})

    def test_monitor_launch_failure_is_hidden(self):
        with patch.dict(os.environ, {'BIOBENCH_GPU_MONITOR': 'gpu-monitor'}), \
             patch('serve.shutil.which', return_value='/tools/gpu-monitor'), \
             patch('serve.subprocess.run', side_effect=OSError('not executable')):
            self.assertEqual(serve._gpu(), {})

class StartupTests(unittest.TestCase):
    def test_local_only_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(serve.parse_args([]).host, '127.0.0.1')

    def test_network_access_is_explicit(self):
        with patch.dict(os.environ, {'BIOBENCH_HOST': '0.0.0.0'}):
            self.assertEqual(serve.parse_args([]).host, '0.0.0.0')
            self.assertEqual(serve.parse_args(['--host', '127.0.0.1']).host, '127.0.0.1')

    def test_invalid_ports(self):
        for port in ['0', '-1', '65536', 'abc']:
            with self.subTest(port=port), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    serve.parse_args(['--port', port])
                self.assertEqual(exc.exception.code, 2)

    def test_valid_port_boundaries(self):
        for port in [1, 65535]:
            self.assertEqual(serve.parse_args(['--port', str(port)]).port, port)

if __name__ == '__main__':
    unittest.main()
