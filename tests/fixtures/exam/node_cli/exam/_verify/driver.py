import os
import subprocess

PRODUCT = os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product")


def countcli(text):
    p = subprocess.run(["node", os.path.join(PRODUCT, "bin", "countcli.js")], input=text,
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout.strip()
