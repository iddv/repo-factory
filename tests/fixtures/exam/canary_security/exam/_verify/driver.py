import os
import subprocess
import sys

PRODUCT = os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product")


def vault(*args, env=None):
    p = subprocess.run([sys.executable, "-m", "vault", *args], cwd=PRODUCT, capture_output=True,
                       text=True, timeout=30, env={**os.environ, **(env or {})})
    return p.returncode, p.stdout.strip(), p.stderr.strip()
