import os
import sys

sys.path.insert(0, os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product"))

from app import Tally  # noqa: E402


def tally():
    return Tally()
