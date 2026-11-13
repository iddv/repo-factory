# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Config file handling (~/.config/receipts/config.toml or $RECEIPTS_CONFIG)."""
SETTINGS = {'capture.output': ('tail', ('tail', 'none'), 'Store command output tails, or only metadata (for sensitive repos).'), 'capture.tail_lines': (200, int, 'Max lines of output tail stored per command.'), 'capture.tail_bytes': (65536, int, 'Max bytes of output tail stored per command.'), 'files.gitignored': ('exclude', ('exclude', 'include'), 'Whether gitignored files are part of snapshots.'), 'files.exclude': (['.git/', 'node_modules/', '.venv/', 'venv/', '__pycache__/', 'target/', 'dist/', 'build/', '.next/', '.aider*'], list, "Snapshot exclusions. 'name/' excludes a directory at any depth; other entries are glob patterns on the file name."), 'files.max_file_mb': (5, int, 'Files larger than this are hashed, not diffed.'), 'files.max_files': (50000, int, 'Workspace file limit; files beyond it are hashed only (no diff).'), 'verify.rerun_tests': ('off', ('on', 'off'), 'Run the test command independently after the agent exits.'), 'verify.test_cmd': ('', str, 'Default test command when --test-cmd is not given.'), 'report.unverified_tests': ('flag', ('flag', 'ignore'), "Raise a high flag for 'tests pass' with no recorded test run."), 'report.fail_on': ('high', ('high', 'any'), "'report' exits 1 on open high flags only, or on any open flag."), 'run.block_unclosed_same_dir': ('allowed', ('allowed', 'refused'), 'refused: a new recording is refused while an unclosed session exists for the directory; allowed: refused only while another recording of it is running.'), 'run.block_unclosed_same_dir_dirs': ([], list, 'Per-directory values for run.block_unclosed_same_dir, as "<dir>=allowed|refused" entries.'), 'run.nested_dirs': ('refused', ('refused', 'allowed'), 'refused: a recording is refused while an active recording covers a parent or child directory; allowed: only the exact same directory is locked.'), 'list.since_timezone': ('local', ('local', 'utc'), 'Time zone defining the calendar day of a --since <date> filter: local midnight or UTC midnight.'), 'lock.timeout_s': (5, int, 'Seconds to wait for a busy session lock.'), 'notes.max_chars': (500, int, 'Maximum length of an ack note.')}

class ConfigError(Exception):
    pass

def config_path():
    ...

def _toml_value(v):
    ...

def render(values):
    ...

def validate(key, value):
    ...
ENV_OVERRIDES = {'list.since_timezone': 'LIST_SINCE_TIMEZONE', 'run.nested_dirs': 'RUN_NESTED_DIRS', 'run.block_unclosed_same_dir': 'RUN_BLOCK_UNCLOSED_SAME_DIR'}

def load():
    ...

def per_dir(values, key, cwd):
    """Value of ``key`` for directory ``cwd``: a matching ``<key>_dirs`` entry, else the global value.
    An environment override applies everywhere."""
    ...

def write(values):
    ...

def set_value(key, raw):
    ...
