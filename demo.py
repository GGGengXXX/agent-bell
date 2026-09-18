#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess

PORT = 18765
SOUND = "/System/Library/Sounds/Glass.aiff"


def show_agent_finished_popup():
    """Open a visible, dismissible macOS dialog in the logged-in user session."""
    subprocess.Popen([
        "osascript",
        "-e",
        'display dialog "Agent finished" with title "Agent Bell" '
        'buttons {"OK"} default button "OK" with icon note',
    ])


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/ding":
            self.send_response(404)
            self.end_headers()
            return

        subprocess.Popen(["afplay", SOUND])
        show_agent_finished_popup()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

print(f"Agent Bell listening on 127.0.0.1:{PORT}")

server.serve_forever()
