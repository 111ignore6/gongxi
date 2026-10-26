"""Manual browser QA: serve real assets while status is temporarily unavailable.

python tests/browser_status_proxy.py --upstream http://127.0.0.1:4288
Create data/browser-validation/allow-api.flag to restore /api/status.
This helper is not part of the product launcher.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', default='http://127.0.0.1:4288')
    parser.add_argument('--port', type=int, default=4289)
    args = parser.parse_args()
    marker = Path(__file__).resolve().parents[1] / 'data/browser-validation/allow-api.flag'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/status' and not marker.exists():
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"detail":"Controlled initial API outage for browser QA"}')
                return
            try:
                request = urllib.request.Request(args.upstream + self.path)
                with urllib.request.urlopen(request, timeout=5) as response:
                    body = response.read()
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.headers.get('Content-Type', 'application/octet-stream'))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            except urllib.error.HTTPError as error:
                self.send_response(error.code)
                self.end_headers()
            except OSError:
                self.send_response(502)
                self.end_headers()

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
