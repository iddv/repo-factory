# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""`receipts demo`: six realistic sessions across three sample projects."""
API, WEB, GO = ('/home/demo/projects/acme-api', '/home/demo/projects/web-dashboard', '/home/demo/projects/billing-svc')

def _iso(t):
    ...

def _diff(path, old, new):
    ...

class Builder:

    def __init__(self, t0, name, agent, cwd, files, test_cmd=None, head='4f1c2ab9e0d3'):
        ...

    def ev(self, etype, data, dt=1):
        ...

    def cmd(self, cmd, exit=0, output='', dur=900, dt=20):
        ...

    def change(self, path, old, new):
        ...

    def end(self, rc=0, interrupted=False, new_head=None):
        ...

    def summary(self, text, cfg, source='claude-code:demo-transcript.jsonl', tool_calls=None):
        ...
PYTEST_OK = '============================= test session starts ==============================\ncollected 12 items\n\ntests/test_users.py ........                                             [ 66%]\ntests/test_billing.py ....                                               [100%]\n\n============================== 12 passed in 0.84s ==============================='
PYTEST_FAIL = 'collected 12 items\n\ntests/test_users.py ........                                             [ 66%]\ntests/test_billing.py ...F                                               [100%]\n\n=================================== FAILURES ===================================\n____________________________ test_invoice_rounding _____________________________\n\n    def test_invoice_rounding():\n>       assert invoice_total([0.1, 0.2]) == 0.3\nE       assert 0.30000000000000004 == 0.3\n\ntests/test_billing.py:41: AssertionError\n=========================== short test summary info ============================\nFAILED tests/test_billing.py::test_invoice_rounding - assert 0.30000000000000004 == 0.3\n========================= 1 failed, 11 passed in 0.91s ========================='
API_FILES = ['pyproject.toml', 'README.md', 'src/acme/__init__.py', 'src/acme/users.py', 'src/acme/billing.py', 'tests/test_users.py', 'tests/test_billing.py']
WEB_FILES = ['package.json', 'tsconfig.json', '.eslintrc.json', 'vite.config.ts', 'src/main.tsx', 'src/api.ts', 'src/components/Chart.tsx', 'src/components/Chart.test.tsx', 'src/components/Table.tsx']
GO_FILES = ['go.mod', 'cmd/billing/main.go', 'internal/rates/rates.go', 'internal/rates/rates_test.go', 'internal/invoice/invoice.go', 'internal/invoice/invoice_test.go']

def has_demo():
    ...

def reset():
    ...

def load(cfg):
    ...
