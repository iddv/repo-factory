# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""Domain rules for Plotkeeper. Every function that changes data runs in one
transaction, validates before writing and records an audit entry."""
import re

SIZES = ('small', 'standard', 'large')
PREFERENCES = ('any',) + SIZES
PLOT_STATUSES = ('available', 'offered', 'occupied', 'out_of_service', 'retired')
METHODS = ('cash', 'bank_transfer', 'cheque', 'card', 'other')
REMOVE_REASONS = ('withdrew', 'unreachable', 'other')
RELEASE_REASONS = ('gave up', 'moved', 'removed by committee')
ROLES = ('admin', 'coordinator')
DUE_STATUSES = ('Paid', 'Partly paid', 'Unpaid', 'Overdue', 'In credit')
DEFAULT_SETTINGS = {'garden_name': 'Community Garden', 'currency': 'EUR', 'timezone': 'UTC', 'fee.small': '4000', 'fee.standard': '6000', 'fee.large': '8000', 'offers.expiry_days': '14', 'seasons.due_date': '03-31', 'waitlist.max_declines': '2', 'waitlist.decline_policy': 'keep_place', 'fees.new_holder_charge': 'full', 'payments.overpayment': 'reject', 'plots.release_with_balance': 'allow', 'auth.session_hours': '8', 'auth.max_failed': '5', 'auth.lockout_minutes': '15', 'fees.prorate_from': 'start_date', 'seasons.current_rule': 'latest_year', 'assign.direct_with_active_entry': 'close_entry', 'offers.valid_on_expiry_date': 'true'}
CHOICE_SETTINGS = {'waitlist.decline_policy': ('keep_place', 'move_to_end'), 'fees.new_holder_charge': ('full', 'prorated'), 'payments.overpayment': ('reject', 'credit'), 'plots.release_with_balance': ('allow', 'block'), 'fees.prorate_from': ('recorded_date', 'start_date'), 'seasons.current_rule': ('most_recently_opened', 'latest_year'), 'assign.direct_with_active_entry': ('close_entry', 'refused'), 'offers.valid_on_expiry_date': ('true', 'false')}
INT_SETTINGS = {'offers.expiry_days': (1, 365), 'waitlist.max_declines': (1, 99), 'auth.session_hours': (1, 720), 'auth.max_failed': (1, 100), 'auth.lockout_minutes': (1, 1440)}

class DomainError(Exception):
    """A rule refused the action. The message is shown to the user."""

class FieldErrors(Exception):

    def __init__(self, errors):
        ...

class DuplicateWarning(Exception):

    def __init__(self, matches):
        ...

def utcnow():
    ...

def now_iso():
    ...

def parse_iso(s):
    ...

def garden_tz(conn):
    ...

def today(conn):
    ...

def local_ts(conn, iso, with_time=True):
    ...

def local_date_to_iso(conn, d):
    """Midnight of a local date, as a UTC timestamp (used for backdated join dates)."""
    ...

def parse_date(value, field='date', errors=None):
    ...
AMOUNT_RE = re.compile('^\\d+(\\.\\d{1,2})?$')
SIGNED_AMOUNT_RE = re.compile('^[+-]?\\d+(\\.\\d{1,2})?$')
MAX_CENTS = 10000000000

def parse_amount(value, signed=False):
    """Parse a decimal string into integer cents. Returns (cents, error)."""
    ...

def fmt_money(cents):
    ...

def prorate(fee_cents, start_month):
    """fee x remaining months / 12, months counting the acceptance month through
    December, rounded half-up to the cent."""
    ...
ENV_OVERRIDES = {'fees.prorate_from': 'FEES_PRORATE_FROM', 'seasons.current_rule': 'SEASONS_CURRENT_RULE', 'assign.direct_with_active_entry': 'ASSIGN_DIRECT_WITH_ACTIVE_ENTRY', 'offers.valid_on_expiry_date': 'OFFERS_VALID_ON_EXPIRY_DATE'}

def _env_override(key):
    ...

def get_settings(conn):
    ...

def get_setting(conn, key):
    ...

def _set_setting(conn, key, value):
    ...

def fee_for(conn, size):
    ...

def audit(conn, actor, action, record_type, record_id, summary='', before=None, after=None, plot_id=None, gardener_id=None):
    ...

def _row(r):
    ...

def require_admin(actor):
    ...

def update_settings(conn, actor, values):
    ...

def hash_password(pw):
    ...

def verify_password(pw, stored):
    ...

def _check_new_password(pw, pw2, errors, field='password'):
    ...

def _check_username(conn, username, errors):
    ...

def has_users(conn):
    ...

def new_setup_code(conn):
    ...

def setup(conn, code, garden_name, currency, timezone, username, password, password2):
    ...

def authenticate(conn, username, password):
    """Returns the user row, or raises DomainError with a generic message."""
    ...

def temp_password():
    ...

def create_user(conn, actor, username, role):
    ...

def _other_active_admins(conn, uid):
    ...

def set_role(conn, actor, uid, role):
    ...

def set_active(conn, actor, uid, active):
    ...

def reset_password(conn, actor, uid):
    ...

def change_password(conn, uid, current, new, new2, keep_token=None):
    ...

def create_session(conn, uid):
    ...

def load_session(conn, token):
    ...

def end_session(conn, token):
    ...

def _clean_plot(conn, data, exclude_id=None):
    ...

def get_plot(conn, pid):
    ...

def add_plot(conn, actor, data):
    ...

def _lock_plot(conn, pid, version):
    ...

def _set_plot_status(conn, pid, status, reason=''):
    ...

def edit_plot(conn, actor, pid, version, data):
    ...

def change_plot_status(conn, actor, pid, version, action, reason):
    """action: out_of_service | return | retire"""
    ...

def status_label(s):
    ...

def current_assignment(conn, pid=None, gid=None):
    ...

def open_offer_for_plot(conn, pid):
    ...

def plot_counts(conn):
    ...

def list_plots(conn, status='', size='', q='', include_retired=False):
    ...

def last_release_date(conn, pid):
    ...

def _clean_gardener(data):
    ...

def _phone_key(p):
    ...

def find_duplicates(conn, email, phone, exclude_id=None):
    ...

def get_gardener(conn, gid):
    ...

def add_gardener(conn, actor, data, allow_duplicate=False):
    ...

def edit_gardener(conn, actor, gid, version, data):
    ...

def gardener_total_balance(conn, gid):
    ...

def archive_gardener(conn, actor, gid, archived=True):
    ...

def list_gardeners(conn, q='', include_archived=False):
    ...

def active_entry(conn, gid):
    ...

def waiting_list(conn):
    """Active entries (including those with an open offer) in order. Positions are derived."""
    ...

def entry_position(conn, eid):
    ...

def add_to_waitlist(conn, actor, gid, preference, joined=None):
    ...

def _get_entry(conn, eid):
    ...

def change_preference(conn, actor, eid, preference):
    ...

def remove_from_waitlist(conn, actor, eid, reason, note=''):
    ...

def next_in_line(conn, size):
    ...

def preview_offer(conn, pid):
    ...

def make_offer(conn, actor, pid, version, entry_id):
    ...

def get_offer(conn, oid):
    ...

def resolve_offer(conn, actor, oid, outcome, start_date=None):
    """outcome: accepted | declined | expired | withdrawn"""
    ...

def _start_assignment(conn, actor, plot, gid, start, via, note=''):
    ...

def _insert_charge(conn, actor, gid, season, aid, amount, date, reason):
    ...

def direct_assign(conn, actor, pid, version, gid, start_date, reason):
    ...

def release_plot(conn, actor, pid, version, end_date, reason):
    ...

def offer_is_expired(conn, expires_on):
    ...

def open_offers(conn):
    ...

def current_season(conn):
    ...

def get_season(conn, sid=None):
    ...

def list_seasons(conn):
    ...

def default_fees(conn):
    ...

def clean_season(conn, data):
    ...

def season_preview(conn, fees):
    ...

def open_season(conn, actor, data):
    ...

def charge_season(conn, actor, sid):
    """Charge every active assignment once. Safe to re-run: the unique index on
    (assignment, season) means a second run creates nothing."""
    ...

def close_season(conn, actor, sid):
    ...

def season_figures(conn, gid, sid):
    ...

def due_status(charged, balance, due_date, on_date):
    """Fee status from the scope's rules."""
    ...

def _clean_money_form(conn, gid, sid, amount, date, signed=False):
    ...

def _existing_token(conn, token):
    ...

def _insert_entry(conn, actor, gid, s, kind, amount, d, method=None, reference='', reason='', token=None, voids_id=None):
    ...

def record_payment(conn, actor, gid, sid, amount, date, method, reference='', token=None):
    ...

def record_refund(conn, actor, gid, sid, amount, date, method, reason, token=None):
    ...

def record_adjustment(conn, actor, gid, sid, amount, date, reason, token=None):
    ...

def void_payment(conn, actor, lid, reason):
    ...

def gardener_ledger(conn, gid):
    ...

def dues(conn, sid, on_date=None, status='', sort='name'):
    ...

def season_totals(conn, sid, on_date=None):
    ...

def payments_in_range(conn, start, end):
    ...

def payments_by_method(conn, start, end):
    """Net money received per method (payments less voids and refunds)."""
    ...
