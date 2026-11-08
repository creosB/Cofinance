"""Serve the built React dashboard and existing read-only API on one loopback origin.

  python -S -E -B -m ui.dashboard

Build first: cd frontend && npm ci && npm run build
The original ui.workbench remains independently available. Writes happen only through explicit,
same-origin, CSRF-checked actions (chat, model operations, watchlist, one-off news collection, labels,
and explicit conversation-memory actions: remember a note, forget one item or all of a conversation).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .workbench import ROOT, make_handler

DIST = ROOT / "frontend/dist"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; font-src 'self'; img-src 'self' data:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml"}


def make_dashboard_handler(dist: Path, trace_dir: Path, corpus_dir: Path, service=None):
    import secrets
    from urllib.parse import parse_qs
    from core.local_models import ModelError
    csrf = secrets.token_urlsafe(32)
    base = make_handler(trace_dir, corpus_dir)
    dist = dist.resolve()
    from qualify.label_store import LabelStore
    labels = LabelStore(corpus_dir)
    class Handler(base):
        def api_origin(self, write=False):
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origin = self.headers.get('Origin')
            site = self.headers.get('Sec-Fetch-Site')
            if host not in allowed or site not in (None, 'none', 'same-origin'):
                return False
            if origin and origin != 'http://' + host:
                return False
            return not write or (origin == 'http://' + host and secrets.compare_digest(self.headers.get('X-Cofinance-CSRF', ''), csrf))

        def interactive_get(self):
            parsed = urlsplit(self.path)
            if not service or parsed.path not in {'/api/state', '/api/conversation', '/api/job', '/api/labels', '/api/memory'}:
                return False
            if not self.api_origin():
                self.reply(403, {'error':'Local origin required.'})
                return True
            try:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if parsed.path == '/api/state' and not query:
                    self.reply(200, {**service.snapshot(), 'csrf':csrf})
                elif parsed.path == '/api/labels' and not query:
                    self.reply(200, labels.snapshot())
                elif parsed.path == '/api/memory' and set(query)=={'session_id'} and len(query['session_id'])==1:
                    self.reply(200, service.memory_view(query['session_id'][0]))
                elif parsed.path in {'/api/conversation','/api/job'} and set(query)=={'id'} and len(query['id'])==1:
                    value = service.store.session(query['id'][0]) if parsed.path=='/api/conversation' else service.store.job(query['id'][0])
                    self.reply(200, value)
                else:
                    self.reply(400, {'error':'Invalid local API query.'})
            except (ValueError, KeyError):
                self.reply(404, {'error':'Local record not found.'})
            return True

        def discard_body(self):
            # Drain a small declared body before an early rejection. Closing a socket with unread
            # data makes Windows send a reset, so the client would see an abort instead of the 4xx.
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return
            if 0 < length <= 16000 and not self.headers.get('Transfer-Encoding'):
                try:
                    self.connection.settimeout(5)
                    self.rfile.read(length)
                except OSError:
                    pass

        def do_POST(self):
            if not service:
                self.discard_body()
                self.send_error(501, 'Interactive endpoints are not enabled.')
                return
            if not self.api_origin(write=True):
                self.discard_body()
                self.reply(403, {'error':'Same-origin request and valid request token required.'})
                return
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                self.discard_body()
                self.reply(415, {'error':'JSON request required.'})
                return
            try:
                length = int(self.headers.get('Content-Length','-1'))
                if not 0 < length <= 16000:
                    self.reply(413, {'error':'Request is missing a valid length or exceeds the limit.'})
                    return
                self.connection.settimeout(10)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('A JSON object is required.')
                routes = {'/api/sessions':{'request_id'}, '/api/chat':{'session_id','query','request_id','model'},
                          '/api/jobs/cancel':{'id'}, '/api/models/action':{'action'}, '/api/models/select':{'model'},
                          '/api/watchlist':{'ticker'}, '/api/news/collect':{'request_id'},
                          '/api/labels':{'id','material','urgent','direction','trap','event_group','notes'},
                          '/api/memory/note':{'session_id','text','request_id'}, '/api/memory/forget':{'session_id','item_id','request_id'},
                          '/api/sessions/delete':{'session_id','request_id'}, '/api/sessions/clear':{'request_id'}}
                if self.path == '/api/labels' and set(body) == routes['/api/labels']:
                    self.reply(200, labels.save(body));return  # human labels only; no model call, no service lock
                if self.path not in routes:
                    self.reply(404, {'error':'Unknown local action.'});return
                if set(body) != routes[self.path]:raise ValueError('Unexpected request fields.')
                if self.path in {'/api/sessions/delete', '/api/sessions/clear'}:
                    # Not under the service-wide lock either: deleting memory copies can start the local embedder.
                    value = (service.delete_conversation(body['session_id'], body['request_id']) if self.path == '/api/sessions/delete'
                             else service.clear_conversations(body['request_id']))
                    self.reply(200, value);return
                if self.path.startswith('/api/memory/'):
                    # Explicit memory actions. Not under the service-wide lock: the first note can start the local embedder.
                    if self.path == '/api/memory/note':
                        row = service.add_note(body['session_id'], body['text'], body['request_id'])
                        value = {'item_id':row['item_id'], 'kind':'note', 'state':row['state'], 'tickers':json.loads(row['tickers']),
                                 'in_keyword':bool(row['keyword_id']), 'in_semantic':bool(row['semantic_id']), 'last_error':row['last_error']}
                    else:
                        value = service.forget(body['session_id'], body['item_id'], body['request_id'])  # item_id "*" = forget all
                    self.reply(200, value);return
                with service.lock,service.models.lock:
                    if self.path=='/api/sessions':value=service.store.create_session(body['request_id'])
                    elif self.path=='/api/chat':value=service.submit(**body)
                    elif self.path=='/api/jobs/cancel':value=service.stop(body['id'])
                    elif self.path=='/api/models/action':value=service.models.launch_task(body['action'])
                    elif self.path=='/api/models/select':value=service.models.select(body['model'])
                    elif self.path=='/api/news/collect':value=service.collect_news(body['request_id'])
                    else:value=service.store.add_watch(body['ticker'])
                self.reply(202 if self.path in {'/api/chat','/api/models/action','/api/news/collect'} else 200,value)
            except ModelError as exc:
                self.reply(409, {'error':str(exc)[:300]})
            except (ValueError,TypeError,KeyError) as exc:
                self.reply(400, {'error':str(exc)[:300]})
            except Exception:
                self.reply(500, {'error':'The local action failed. No automatic retry was made.'})
        def local_asset_request(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return (self.headers.get("Host") in allowed
                    and self.headers.get("Sec-Fetch-Site") != "cross-site"
                    and (not self.headers.get("Origin") or self.headers["Origin"] in {"http://" + h for h in allowed}))

        def send_file(self, path: Path, mime: str):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(dist) or path.stat().st_size > 5 * 1024 * 1024:
                self.reply(404, {"error": "asset unavailable"})
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Cofinance-Frontend", "react")
            self.send_header("X-Cofinance-Build", hashlib.sha256((dist / "index.html").read_bytes()).hexdigest())
            self.end_headers()
            self.wfile.write(data)

        def reply(self, status, payload, *, html=False):
            if html and status == 200:
                try:
                    self.send_file(dist / "index.html", "text/html; charset=utf-8")
                except OSError:
                    super().reply(503, {"error": "React build unavailable. Run frontend npm run build."})
                return
            super().reply(status, payload, html=html)

        def do_GET(self):
            if self.interactive_get():
                return
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/assets/"):
                if not self.local_asset_request():
                    self.reply(403, {"error": "local origin required"})
                    return
                if parsed.query or not re.fullmatch(r"/assets/[A-Za-z0-9_.-]+", parsed.path):
                    self.reply(404, {"error": "asset unavailable"})
                    return
                path = dist / "assets" / parsed.path.removeprefix("/assets/")
                if path.suffix not in MIME:
                    self.reply(404, {"error": "asset type unavailable"})
                    return
                try:
                    self.send_file(path, MIME[path.suffix])
                except OSError:
                    self.reply(404, {"error": "asset unavailable"})
                return
            super().do_GET()
    return Handler


def create_server(*, port: int = 8878, dist: Path | None = None, trace_dir: Path | None = None,
                  corpus_dir: Path | None = None, service=None):
    return ThreadingHTTPServer(("127.0.0.1", port), make_dashboard_handler(
        dist or DIST, trace_dir or ROOT / "data/traces", corpus_dir or latest_corpus(ROOT / "data"), service))


def latest_corpus(data: Path) -> Path:
    """Newest data/news_corpus_YYYYMMDD folder that has a manifest; the dated name sorts chronologically."""
    folders = sorted(p for p in data.glob("news_corpus_*") if (p / "manifest.json").is_file())
    return folders[-1] if folders else data / "news_corpus_20261107"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8878)
    args = parser.parse_args()
    if not (DIST / "index.html").is_file():
        parser.error("React build missing. Run npm ci and npm run build inside frontend/ first.")
    from core.chat_service import ChatService
    service = ChatService(ROOT)
    server = create_server(port=args.port, service=service)
    print(f"Cofinance React dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    print("React chat + local research API. Loopback-only. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
