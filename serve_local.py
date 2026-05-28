#!/usr/bin/env python3
"""
serve_local.py — local testing helper
======================================
For testing the extension BEFORE deploying to GitHub Pages.

What it does:
  1. Runs build_news.py once (creates public/news.json)
  2. Serves the public/ folder at http://localhost:8765 with CORS enabled
  3. Re-builds news every 5 minutes in the background

Run:  python3 serve_local.py
Then in the extension, keep activeSource = 'local'.

Stop with Ctrl+C.
"""

import http.server
import socketserver
import threading
import time
import subprocess
import sys
from pathlib import Path

PORT = 8765
PUBLIC_DIR = Path(__file__).parent / 'public'
BUILD_SCRIPT = Path(__file__).parent / 'build_news.py'
REBUILD_EVERY_SEC = 300


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # quiet


def build_once():
    print('[build] running build_news.py ...')
    try:
        subprocess.run([sys.executable, str(BUILD_SCRIPT)], check=True)
    except subprocess.CalledProcessError as e:
        print(f'[build] failed: {e}')


def rebuild_loop():
    while True:
        time.sleep(REBUILD_EVERY_SEC)
        build_once()


def main():
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    build_once()

    t = threading.Thread(target=rebuild_loop, daemon=True)
    t.start()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), CORSHandler) as httpd:
        print(f'\n[serve] news.json available at http://localhost:{PORT}/news.json')
        print(f'[serve] rebuilding every {REBUILD_EVERY_SEC // 60} min. Ctrl+C to stop.\n')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n[serve] stopped.')


if __name__ == '__main__':
    main()
