import argparse

from . import __version__


def main(argv=None):
    p = argparse.ArgumentParser(prog="recall")
    p.add_argument("--version", action="version", version=f"recall {__version__}")
    p.parse_args(argv)
    return 0
