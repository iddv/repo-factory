import os
import sys

PRODUCT = os.environ.get("REPO_FACTORY_PRODUCT_DIR", "/work/product")


def count(text):
    sys.path.insert(0, PRODUCT)
    from wordlib import count as c
    return c(text)
