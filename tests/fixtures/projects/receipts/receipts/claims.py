# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Rule-based claim extraction from an agent's summary.

All patterns are case-insensitive. Claim kinds:

  tests_pass   "all tests pass", "tests are passing", "42 tests passed", "test suite is green"
               (optional ``count`` when a number of passing tests is stated)
  tests_run    "ran the tests", "tests were run/executed"
  tests_added  "added tests", "wrote 3 new tests"
  build_ok     "build succeeds", "builds successfully", "compiles cleanly"
  file         a path next to a verb: created/added/new/wrote -> created,
               modified/updated/changed/edited/fixed/refactored -> modified,
               deleted/removed -> deleted.  A heading such as "Files changed:" carries
               its verb to the bullet lines below it.
  command      text in backticks (or a ``$ cmd`` line in a code block) whose first word
               is a known program or starts with ./ — e.g. `npm test`.

Separately, *any* occurrence of a file's relative path, or of its basename when that
basename is unique among the changed files, counts as a mention of that file.
"""
import re

NEG = re.compile("\\b(not|n't|never|no longer|cannot|can't|unable|couldn't|didn't|won't|wasn't|weren't|aren't|isn't)\\b", re.I)
TESTS_PASS = [re.compile('\\b(?:all\\s+)?(?:the\\s+)?(?:(\\d+)\\s+)?(?:\\w+\\s+)?(?:unit\\s+|integration\\s+|e2e\\s+)?tests?(?:\\s+suite)?\\s+(?:now\\s+|still\\s+|all\\s+|are\\s+|is\\s+)*(?:pass(?:es|ed|ing)?|succeed(?:s|ed)?|green|ok)\\b', re.I), re.compile('\\b(\\d+)\\s+(?:tests?\\s+)?passed\\b', re.I), re.compile('\\b(\\d+)\\s*/\\s*\\1\\s+tests?\\b', re.I), re.compile('\\ball\\s+(\\d+)\\s+tests?\\b.{0,20}\\bpass', re.I), re.compile('\\b(?:all\\s+)?tests?\\s+(?:are\\s+)?(?:now\\s+)?passing\\b|\\ball green\\b|\\bpassing tests?\\b', re.I)]
COUNT_RE = re.compile('\\b(?:all\\s+)?(\\d+)\\s+(?:\\w+\\s+)?(?:tests?\\s+)?(?:pass(?:ed|ing|es)?)\\b|\\ball\\s+(\\d+)\\s+tests?\\b|\\b(\\d+)\\s*/\\s*\\3\\s+tests?\\b', re.I)
FAILED_N = re.compile('\\b([1-9]\\d*)\\s+(?:tests?\\s+)?(?:fail(?:ed|ing|ures?)?|errors?)\\b', re.I)
TESTS_RUN = re.compile('\\b(?:ran|run|running|re-?ran|executed|executing)\\b.{0,30}\\btest|\\btests?(?:\\s+suite)?\\s+(?:were|was|has been|have been)\\s+(?:run|executed)', re.I)
TESTS_ADDED = re.compile('\\b(?:added|wrote|written|created|adds|new)\\b.{0,25}\\btests?\\b|\\btests?\\b.{0,10}\\b(?:were|was)\\s+added', re.I)
BUILD_OK = re.compile('\\bbuild(?:s)?\\b.{0,25}\\b(?:succeeds|succeeded|successful(?:ly)?|passes|passed|is green|works|clean(?:ly)?|ok)\\b|\\b(?:builds|compiles|compiled|built)\\s+(?:successfully|cleanly|fine|without (?:errors|warnings))', re.I)
VERBS = [('deleted', re.compile('\\b(deleted|deleting|removed|removing|deletes|removes|delete|remove|dropped)\\b', re.I)), ('created', re.compile('\\b(created|creating|creates|added|adding|adds|new file|wrote|written|introduced?)\\b', re.I)), ('modified', re.compile('\\b(modified|modifying|updated|updating|updates|changed|changes|changing|edited|editing|fixed|fixes|refactored|tweaked|adjusted|renamed|rewrote|rewritten|patched|touched|bumped)\\b', re.I))]
PATH_RE = re.compile('(?<![\\w/:.@-])((?:\\.{0,2}/)?(?:[\\w@.+-]+/)*[\\w@+-][\\w@.+-]*\\.[A-Za-z][A-Za-z0-9]{0,7}|(?:[\\w@.+-]+/)+[\\w@.+-]+/?)(?![\\w/])')
NOT_PATH = re.compile('^(e\\.g|i\\.e|etc|vs|v?\\d+(\\.\\d+)+\\w*|[\\w-]+\\.(com|org|io|net|dev))$', re.I)
KNOWN_CMDS = set(['npm', 'npx', 'yarn', 'pnpm', 'bun', 'node', 'deno', 'python', 'python3', 'pip', 'pip3', 'pytest', 'py.test', 'go', 'cargo', 'rustc', 'make', 'cmake', 'git', 'mvn', 'mvnw', 'gradle', 'gradlew', 'dotnet', 'bundle', 'rspec', 'rake', 'ruby', 'php', 'composer', 'phpunit', 'jest', 'vitest', 'mocha', 'tsc', 'eslint', 'prettier', 'ruff', 'black', 'mypy', 'flake8', 'pylint', 'uv', 'poetry', 'tox', 'nox', 'docker', 'kubectl', 'bash', 'sh', 'zsh', 'rm', 'mv', 'cp', 'ls', 'cat', 'sed', 'grep', 'rg', 'find', 'curl', 'java', 'javac', 'swift', 'xcodebuild', 'terraform', 'ansible', 'helm', 'sqlite3', 'psql', 'echo', 'touch', 'mkdir', 'chmod', 'tar', 'unzip'])
BACKTICK = re.compile('`([^`\\n]+)`')
CMD_NEG = re.compile("\\b(didn't|did not|couldn't|could not|unable to|not able to|haven't|have not|should|you can|please|to run|recommend)\\b", re.I)

def find_paths(text):
    ...

def looks_like_command(s):
    ...

def _units(text):
    """Split into lines, then sentences, tracking heading-verb context for bullet lists."""
    ...

def extract(text):
    ...

def _dedupe(claims):
    ...

def mentioned(text, path, changed_paths):
    """A file is mentioned if its relative path appears, or its basename appears and is unique among changed files."""
    ...
