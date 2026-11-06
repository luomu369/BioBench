"""HTTP regressions; no GPU, model downloads, or external API calls."""
import concurrent.futures
import http.client
import json
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

    def test_missing_selection_request_is_empty(self):
        self.assertEqual(self.request(path='/select.json', method='GET'), (204, b''))

if __name__ == '__main__':
    unittest.main()
