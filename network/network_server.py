import http.server
import json
import os
import socketserver
import sys
import time
import urllib.parse


def start_server(
    push_ai_caption, port=8089, shutdown_event=None, learn_func=None, search_func=None
):
    """
    HTTP control server with simple token auth.
    Endpoints:
      GET /hello
      GET /say?text=...
      GET /learn?topic=...
      GET /search?q=...
      GET /health
    Auth:
      - Header:  X-MS-Token: <token>
      - OR query param: ?token=<token>
    """
    TOKEN = os.environ.get("MS_HTTP_TOKEN", "")  # loaded by systemd from .env
    learn_func = learn_func or (lambda topic: None)
    search_func = search_func or (lambda query: None)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "MachineSpiritHTTP/1.0"

        def _get_params(self):
            path, _, query = self.path.partition("?")
            return path, urllib.parse.parse_qs(query)

        def _auth_ok(self, params):
            hdr = self.headers.get("X-MS-Token", "")
            qp = (params.get("token") or [""])[0]
            provided = hdr or qp
            return bool(TOKEN) and (provided == TOKEN)

        def _send(self, code=200, body=b"OK", content_type="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if isinstance(body, (dict, list)):
                raw = json.dumps(body).encode("utf-8")
                self.wfile.write(raw)
            elif isinstance(body, str):
                self.wfile.write(body.encode("utf-8"))
            else:
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            # Cleaner journal output
            sys.stdout.write(
                "%s - - [%s] %s\n"
                % (
                    self.address_string(),
                    time.strftime("%d/%b/%Y %H:%M:%S"),
                    fmt % args,
                )
            )
            sys.stdout.flush()

        def do_GET(self):
            path, params = self._get_params()

            # Public health check (no token needed)
            if path == "/health":
                return self._send(
                    200, {"status": "ok", "time": time.time()}, "application/json"
                )

            # Auth for everything else
            if not self._auth_ok(params):
                return self._send(401, b"Unauthorized")

            if path == "/hello":
                push_ai_caption("Hello, servant of the Omnissiah.")
                return self._send(200, b"OK")

            if path == "/say":
                text = " ".join(params.get("text", [""])).strip()
                if not text:
                    return self._send(400, b"Missing ?text=")
                push_ai_caption(text)
                return self._send(200, {"ok": True, "echo": text}, "application/json")

            if path == "/learn":
                topic = " ".join(params.get("topic", [""])).strip()
                if not topic:
                    return self._send(400, b"Missing ?topic=")
                try:
                    learn_func(topic)
                    return self._send(
                        200,
                        {"queued": True, "type": "learn", "topic": topic},
                        "application/json",
                    )
                except Exception as e:
                    return self._send(
                        500, {"queued": False, "error": str(e)}, "application/json"
                    )

            if path == "/search":
                q = " ".join(params.get("q", [""])).strip()
                if not q:
                    return self._send(400, b"Missing ?q=")
                try:
                    search_func(q)
                    return self._send(
                        200,
                        {"queued": True, "type": "search", "query": q},
                        "application/json",
                    )
                except Exception as e:
                    return self._send(
                        500, {"queued": False, "error": str(e)}, "application/json"
                    )

            return self._send(404, b"404 - Not Found")

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        httpd = ReusableTCPServer(("", int(port)), Handler)
        httpd.timeout = 0.5
        print(f"Machine Spirit serving at port {port}")
        while shutdown_event is None or not shutdown_event.is_set():
            httpd.handle_request()
    except Exception as e:
        print(f"HTTP server error: {e}")
    finally:
        try:
            httpd.server_close()
            print("HTTP server closed.")
        except Exception:
            pass
