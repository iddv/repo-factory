# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Command line: start | demo | backup | restore | test"""
import sys

CONFIG_KEYS = {'PLOTKEEPER_HOST': '127.0.0.1', 'PLOTKEEPER_PORT': '8080', 'PLOTKEEPER_DATA_DIR': './data', 'PLOTKEEPER_SESSION_SECRET': '', 'PLOTKEEPER_LOG_LEVEL': 'INFO', 'PLOTKEEPER_TIMEZONE': ''}

def load_config():
    """Defaults < config file < environment variables."""
    ...

def server_timezone():
    ...

def open_db(cfg, quiet=False):
    ...

def session_secret(cfg):
    ...

def cmd_start(cfg, args):
    ...

def cmd_demo(cfg, args):
    ...

def cmd_backup(cfg, args):
    ...

def cmd_restore(cfg, args):
    ...

def cmd_test(cfg, args):
    ...

def main(argv=None):
    ...
if __name__ == '__main__':
    sys.exit(main())
