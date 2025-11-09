import http.server, socketserver, urllib.parse

def start_server(push_ai_caption, learn_func, port=8089, shutdown_event=None, token=None):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def _get_token(self):
            # Header takes precedence, then query ?token=
            hdr = self.headers.get('X-MS-Token', '')
            if hdr:
                return hdr.strip()
            _, _, query = self.path.partition('?')
            params = urllib.parse.parse_qs(query)
            return ' '.join(params.get('token', [''])).strip()

        def _auth(self):
            if not token:
                return True
            return self._get_token() == str(token)

        def do_GET(self):
            if not self._auth():
                self.send_response(401); self.end_headers(); self.wfile.write(b"Unauthorized"); return

            path, _, query = self.path.partition('?')
            params = urllib.parse.parse_qs(query)

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
            elif path == '/learn':
                topic = ' '.join(params.get('topic', [''])).strip()
                if topic:
                    learn_func(topic)
                else:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"Missing ?topic="); return
            elif path == '/search':
                q = ' '.join(params.get('q', [''])).strip()
                if q:
                    learn_func(q)
                else:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"Missing ?q="); return
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b"404 - Not Found"); return

            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, fmt, *args):
            # Quieter logs
            return

    class ReuseTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        httpd = ReuseTCPServer(("", port), Handler)
        httpd.timeout = 0.5  # so handle_request() returns regularly
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
