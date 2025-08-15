import os, http.server, socketserver, urllib.parse, hmac

def start_server(push_ai_caption, port=8089, shutdown_event=None):
    MS_TOKEN = os.environ.get("MS_HTTP_TOKEN", "").strip()

    def _authorized(headers, query_params):
        if not MS_TOKEN:
            return True  # if no token set, allow all (but you DID set one)
        supplied = headers.get("X-MS-Token", "")
        if not supplied:
            supplied = " ".join(query_params.get("token", [""]))
        try:
            return hmac.compare_digest(supplied, MS_TOKEN)
        except Exception:
            return False

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            path, _, query = self.path.partition('?')
            params = urllib.parse.parse_qs(query)

            if not _authorized(self.headers, params):
                self.send_response(401); self.end_headers()
                self.wfile.write(b"Unauthorized")
                return

            if path == '/hello':
                push_ai_caption("Hello, servant of the Omnissiah.")
            elif path == '/sad':
                push_ai_caption("The Machine Spirit mourns your sorrow.")
            elif path == '/say':
                text = ' '.join(params.get('text', [''])).strip()
                if text:
                    push_ai_caption(text)
                else:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"Missing ?text="); return
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b"404 - Not Found"); return

            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

    try:
        with socketserver.TCPServer(("", port), Handler) as httpd:
            httpd.timeout = 0.5
            print(f"Machine Spirit serving at port {port}")
            while shutdown_event is None or not shutdown_event.is_set():
                httpd.handle_request()
    except Exception as e:
        print(f"HTTP server error: {e}")
