import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.request

PRODUCT = os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def server():
    port = free_port()
    p = subprocess.Popen([sys.executable, "-m", "srv", "--port", str(port)], cwd=PRODUCT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://localhost:{port}/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.05)
        yield port
    finally:
        p.terminate()
        p.wait()


def get(port, path):
    with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=3) as r:
        return r.status, r.read().decode()
