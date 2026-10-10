"""
Loopback relay that lets IDM download hosts which reject IDM's own User-Agent (FileKeeper / dlproxy).

IDM is handed http://127.0.0.1:<port>/<token>/<filename>. When IDM asks for it, the relay fetches the real
URL with Chrome headers and the right Referer, and streams the bytes back. Range requests pass straight
through, so IDM keeps its multi-connection speed. If the upstream link has expired (401/403/404/410) the
relay asks `refresh` for a fresh direct URL once and retries.
"""
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from requests.adapters import HTTPAdapter

CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PASS_HEADERS = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges',
                'ETag', 'Last-Modified', 'Content-Disposition')
CHUNK = 256 * 1024


def needs_relay(item):
    """True for links IDM cannot fetch on its own because the host wants browser headers."""
    direct = item.get('direct') or ''
    return 'dlproxy' in direct or 'filekeeper' in direct or 'filekeeper' in (item.get('original') or '')


def referer_for(url):
    if 'dlproxy' in url or 'filekeeper' in url:
        return "https://filekeeper.net/download"
    return "https://fitgirl-repacks.site/"


class LocalRelay:
    def __init__(self, refresh=None, on_log=None):
        self.refresh = refresh          # callable(item) -> new direct URL or None
        self.on_log = on_log or (lambda msg, level='info': None)
        self._items = {}
        self._lock = threading.Lock()
        self._server = None
        self._active = 0
        self._session = requests.Session()
        self._session.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=64))
        self._session.mount('http://', HTTPAdapter(pool_connections=4, pool_maxsize=64))

    # -------------------------------------------------------------- public
    @property
    def port(self):
        return self._server.server_address[1] if self._server else None

    @property
    def active(self):
        return self._active

    @property
    def has_items(self):
        return bool(self._items)

    def start(self):
        if self._server:
            return
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                relay._serve(self, head=False)

            def do_HEAD(self):
                relay._serve(self, head=True)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)   # loopback only, free port
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def url_for(self, item):
        """Register a link and return the local URL IDM should download."""
        self.start()
        token = uuid.uuid4().hex
        with self._lock:
            self._items[token] = item
        name = urllib.parse.quote(item.get('filename') or 'file.bin')
        return f"http://127.0.0.1:{self.port}/{token}/{name}"

    # ------------------------------------------------------------ internals
    def _open_upstream(self, item, range_header):
        resp = None
        for attempt in (0, 1):
            url = item.get('direct') or item.get('original')
            headers = {'User-Agent': CHROME_UA, 'Referer': referer_for(url),
                       'Accept': '*/*', 'Accept-Encoding': 'identity'}
            if range_header:
                headers['Range'] = range_header
            resp = self._session.get(url, headers=headers, stream=True, timeout=(10, 30))
            if resp.status_code in (401, 403, 404, 410) and attempt == 0 and self.refresh:
                resp.close()
                fresh = self.refresh(item)
                if fresh and fresh != url:
                    item['direct'] = fresh
                    self.on_log(f"Refreshed expired link for {item.get('filename')}.")
                    continue
                resp = self._session.get(url, headers=headers, stream=True, timeout=(10, 30))
            break
        return resp

    def _serve(self, h, head):
        parts = h.path.split('/', 2)
        item = self._items.get(parts[1]) if len(parts) > 1 else None
        if not item:
            h.send_error(404)
            return
        with self._lock:
            self._active += 1
        resp = None
        try:
            resp = self._open_upstream(item, h.headers.get('Range'))
            h.send_response(resp.status_code)
            for name in PASS_HEADERS:
                if name in resp.headers:
                    h.send_header(name, resp.headers[name])
            if 'Content-Length' not in resp.headers:
                h.send_header('Connection', 'close')
                h.close_connection = True
            h.end_headers()
            if resp.status_code >= 400:
                self.on_log(f"{item.get('filename')}: host answered {resp.status_code}.", 'warn')
            if not head:
                for chunk in resp.iter_content(CHUNK):
                    h.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass    # IDM closed the connection (segment finished or paused)
        except requests.RequestException as exc:
            self.on_log(f"Relay error for {item.get('filename')}: {exc}", 'err')
            try:
                h.send_error(502)
            except OSError:
                pass
        finally:
            if resp is not None:
                resp.close()
            with self._lock:
                self._active -= 1
