# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Data store: SQLite connection, versioned migrations, backup and restore."""
from contextlib import contextmanager

DB_NAME = 'plotkeeper.db'
SCHEMA_V1 = "\nCREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);\nCREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);\nCREATE TABLE users(\n  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,\n  role TEXT NOT NULL CHECK(role IN ('admin','coordinator')), pw_hash TEXT NOT NULL,\n  must_change INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,\n  failed INTEGER NOT NULL DEFAULT 0, locked_until TEXT, created_at TEXT NOT NULL);\nCREATE TABLE sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),\n  csrf TEXT NOT NULL, last_seen TEXT NOT NULL);\nCREATE TABLE plots(\n  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,\n  size TEXT NOT NULL CHECK(size IN ('small','standard','large')), area REAL,\n  notes TEXT NOT NULL DEFAULT '',\n  status TEXT NOT NULL DEFAULT 'available'\n    CHECK(status IN ('available','offered','occupied','out_of_service','retired')),\n  status_reason TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,\n  created_at TEXT NOT NULL);\nCREATE TABLE gardeners(\n  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',\n  phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',\n  archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);\nCREATE TABLE waitlist(\n  id INTEGER PRIMARY KEY, gardener_id INTEGER NOT NULL REFERENCES gardeners(id),\n  preference TEXT NOT NULL CHECK(preference IN ('any','small','standard','large')),\n  joined_at TEXT NOT NULL,\n  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','offered','placed','removed')),\n  declines INTEGER NOT NULL DEFAULT 0, close_reason TEXT, closed_at TEXT, created_at TEXT NOT NULL);\nCREATE UNIQUE INDEX one_active_entry ON waitlist(gardener_id) WHERE status IN ('active','offered');\nCREATE TABLE assignments(\n  id INTEGER PRIMARY KEY, plot_id INTEGER NOT NULL REFERENCES plots(id),\n  gardener_id INTEGER NOT NULL REFERENCES gardeners(id), start_date TEXT NOT NULL,\n  end_date TEXT, end_reason TEXT, via TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',\n  created_at TEXT NOT NULL);\nCREATE UNIQUE INDEX one_holder_per_plot ON assignments(plot_id) WHERE end_date IS NULL;\nCREATE UNIQUE INDEX one_plot_per_gardener ON assignments(gardener_id) WHERE end_date IS NULL;\nCREATE TABLE offers(\n  id INTEGER PRIMARY KEY, plot_id INTEGER NOT NULL REFERENCES plots(id),\n  entry_id INTEGER NOT NULL REFERENCES waitlist(id), offered_on TEXT NOT NULL,\n  expires_on TEXT NOT NULL,\n  status TEXT NOT NULL DEFAULT 'open'\n    CHECK(status IN ('open','accepted','declined','expired','withdrawn')),\n  resolved_at TEXT, user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);\nCREATE UNIQUE INDEX one_open_offer_plot ON offers(plot_id) WHERE status='open';\nCREATE UNIQUE INDEX one_open_offer_entry ON offers(entry_id) WHERE status='open';\nCREATE TABLE seasons(\n  id INTEGER PRIMARY KEY, year INTEGER NOT NULL UNIQUE, due_date TEXT NOT NULL,\n  fee_small INTEGER NOT NULL, fee_standard INTEGER NOT NULL, fee_large INTEGER NOT NULL,\n  opened_at TEXT NOT NULL, closed_at TEXT, user_id INTEGER REFERENCES users(id));\nCREATE TABLE ledger(\n  id INTEGER PRIMARY KEY, gardener_id INTEGER NOT NULL REFERENCES gardeners(id),\n  season_id INTEGER NOT NULL REFERENCES seasons(id),\n  kind TEXT NOT NULL CHECK(kind IN ('charge','payment','void','refund','adjustment')),\n  amount INTEGER NOT NULL, entry_date TEXT NOT NULL, method TEXT,\n  reference TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',\n  assignment_id INTEGER REFERENCES assignments(id), voids_id INTEGER UNIQUE REFERENCES ledger(id),\n  form_token TEXT UNIQUE, user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);\nCREATE UNIQUE INDEX one_charge_per_assignment_season ON ledger(assignment_id, season_id)\n  WHERE kind='charge';\nCREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger\n  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;\nCREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger\n  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;\nCREATE TABLE audit(\n  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER REFERENCES users(id),\n  action TEXT NOT NULL, record_type TEXT NOT NULL, record_id INTEGER,\n  plot_id INTEGER, gardener_id INTEGER, summary TEXT NOT NULL DEFAULT '',\n  before TEXT, after TEXT);\nCREATE INDEX audit_plot ON audit(plot_id);\nCREATE INDEX audit_gardener ON audit(gardener_id);\nCREATE INDEX ledger_gardener ON ledger(gardener_id, season_id);\n"
MIGRATIONS = [(1, SCHEMA_V1)]
LATEST = MIGRATIONS[-1][0]

def db_path(data_dir):
    ...

def connect(path):
    ...

@contextmanager
def tx(conn):
    """Write transaction. BEGIN IMMEDIATE serialises writers, so status/version checks
    inside it cannot race. Re-entrant: nested calls join the outer transaction."""
    ...

def schema_version(conn):
    ...

def migrate(conn, data_dir=None, log=print):
    """Apply pending migrations. An existing database is backed up first."""
    ...

def backup_dir(data_dir):
    ...

def backup(conn, data_dir, label='backup', dest=None):
    ...

def validate_backup(path):
    ...

def restore(data_dir, src):
    """Replace the live database with a backup. The current data is backed up first."""
    ...
