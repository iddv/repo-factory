# Trimmed copy for repo_factory's tests (tests/fixtures/projects): function bodies are removed;
# the module docstring, names, signatures, docstrings and constants are kept.
"""HTTP layer: routing, sessions, CSRF, role checks and HTML pages (stdlib only)."""
import html
import logging
from http.server import ThreadingHTTPServer

from . import core as C

log = logging.getLogger('plotkeeper')
e = lambda s: html.escape('' if s is None else str(s), quote=True)
money = C.fmt_money

class Response:

    def __init__(self, body='', status=200, ctype='text/html; charset=utf-8', headers=None):
        ...

def redirect(url, ok=None, err=None):
    ...

class Request:

    def __init__(self, app, method, path, query, form, cookies):
        ...

    def q(self, k, default=''):
        ...

    def f(self, k, default=''):
        ...

    @property
    def csrf(self):
        ...

    @property
    def is_admin(self):
        ...
ROUTES = []

def route(method, pattern, role='any'):
    """role: None = public, 'any' = signed-in staff, 'admin' = Admin only."""
    ...
CSS = '\n*{box-sizing:border-box}body{font:15px/1.45 system-ui,sans-serif;margin:0;color:#1d2a1d;background:#f6f8f4}\nheader{background:#2f5d34;color:#fff;padding:.5rem 1rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}\nheader a{color:#fff;text-decoration:none;margin-right:.6rem}header .g{font-weight:700;margin-right:1.2rem}\nheader .r{margin-left:auto}main{max-width:1150px;margin:1rem auto;padding:0 1rem}\ntable{border-collapse:collapse;width:100%;background:#fff;margin:.5rem 0 1rem}\nth,td{border-bottom:1px solid #dde4d9;padding:.35rem .5rem;text-align:left;vertical-align:top}\nth{background:#e8eee4}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}\n.card{background:#fff;border:1px solid #dde4d9;border-radius:6px;padding:.8rem 1rem;margin:.6rem 0}\n.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.8rem}\nlabel{display:block;margin:.4rem 0 .1rem;font-weight:600}input,select,textarea{font:inherit;padding:.3rem;\nborder:1px solid #9aa79a;border-radius:4px;max-width:100%}textarea{width:100%}\n.err{color:#a01818;font-size:.9em}.msg{padding:.5rem .8rem;border-radius:4px;margin:.5rem 0}\n.ok{background:#dff0d8}.bad{background:#f8d7da}button,.btn{background:#2f5d34;color:#fff;border:0;\nborder-radius:4px;padding:.35rem .8rem;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}\nbutton.sec,.btn.sec{background:#6c7a6c}button.warn{background:#a04018}form.inline{display:inline}\n.tag{display:inline-block;padding:0 .4rem;border-radius:3px;background:#e8eee4;font-size:.85em}\n.Overdue,.expired{background:#f8d7da}.Paid{background:#dff0d8}.In.credit{background:#d9e8f8}\n.hint{color:#5b6b5b;font-style:italic}.row{display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-end}\n.muted{color:#6c7a6c}s{color:#888}\n'

def layout(req, title, body):
    ...

def csrf_field(req):
    ...

def error_box(msg):
    ...

def inp(name, label, vals, errs, type='text', **attrs):
    ...

def sel(name, label, options, vals, errs, blank=None):
    ...

def area(name, label, vals, errs):
    ...

def form(req, action, inner, button='Save', cls=''):
    ...

def button_form(req, action, label, fields='', cls=''):
    ...

def table(headers, rows, numeric=()):
    ...

def cur(req):
    ...

def size_opts():
    ...

def plot_status_tag(s):
    ...

def form_vals(req):
    ...

@route('GET', '/setup', None)
def setup_page(req, vals=None, errs=None, msg=None):
    ...

@route('POST', '/setup', None)
def setup_post(req):
    ...

@route('GET', '/login', None)
def login_page(req, msg=None, username=''):
    ...

@route('POST', '/login', None)
def login_post(req):
    ...

@route('POST', '/logout')
def logout(req):
    ...

@route('GET', '/account/password')
def password_page(req, errs=None):
    ...

@route('POST', '/account/password')
def password_post(req):
    ...

@route('GET', '/')
def dashboard(req):
    ...

@route('GET', '/plots')
def plots_page(req):
    ...

def plot_form(req, action, vals, errs, msg=None, title='Add plot'):
    ...

@route('GET', '/plots/new')
def plot_new(req):
    ...

@route('POST', '/plots/new')
def plot_new_post(req):
    ...

@route('GET', '/plots/(\\d+)/edit')
def plot_edit(req):
    ...

@route('POST', '/plots/(\\d+)/edit')
def plot_edit_post(req):
    ...

@route('POST', '/plots/(\\d+)/status')
def plot_status_post(req):
    ...

@route('GET', '/plots/(\\d+)')
def plot_detail(req):
    ...

@route('POST', '/plots/(\\d+)/release')
def plot_release(req):
    ...

@route('GET', '/plots/(\\d+)/offer')
def offer_preview(req):
    ...

@route('POST', '/plots/(\\d+)/offer')
def offer_post(req):
    ...

@route('POST', '/offers/(\\d+)/outcome')
def offer_outcome(req):
    ...

@route('GET', '/plots/(\\d+)/assign', 'admin')
def assign_page(req, vals=None, errs=None, msg=None):
    ...

@route('POST', '/plots/(\\d+)/assign', 'admin')
def assign_post(req):
    ...

@route('GET', '/gardeners')
def gardeners_page(req):
    ...

def gardener_form(req, action, vals, errs, msg=None, title='Add gardener', dups=None):
    ...

@route('GET', '/gardeners/new')
def gardener_new(req):
    ...

@route('POST', '/gardeners/new')
def gardener_new_post(req):
    ...

@route('GET', '/gardeners/(\\d+)/edit')
def gardener_edit(req):
    ...

@route('POST', '/gardeners/(\\d+)/edit')
def gardener_edit_post(req):
    ...

@route('POST', '/gardeners/(\\d+)/archive')
def gardener_archive(req):
    ...

@route('GET', '/gardeners/(\\d+)')
def gardener_detail(req):
    ...
KIND_TITLES = {'payment': 'Record payment', 'refund': 'Record refund', 'adjustment': 'Record adjustment'}

@route('GET', '/gardeners/(\\d+)/money')
def money_page(req, vals=None, errs=None, msg=None):
    ...

@route('POST', '/gardeners/(\\d+)/money')
def money_post(req):
    ...

@route('POST', '/ledger/(\\d+)/void')
def void_post(req):
    ...

@route('GET', '/waitlist')
def waitlist_page(req, vals=None, errs=None, msg=None):
    ...

@route('POST', '/waitlist/new')
def waitlist_add(req):
    ...

def _entry_gid(req):
    ...

@route('POST', '/waitlist/(\\d+)/preference')
def waitlist_pref(req):
    ...

@route('POST', '/waitlist/(\\d+)/remove')
def waitlist_remove(req):
    ...

@route('GET', '/seasons')
def seasons_page(req):
    ...

@route('GET', '/seasons/new', 'admin')
def season_new(req, vals=None, errs=None):
    ...

@route('POST', '/seasons/new', 'admin')
def season_new_post(req):
    ...

@route('POST', '/seasons/(\\d+)/close', 'admin')
def season_close(req):
    ...

def _range(req):
    ...

@route('GET', '/dues')
def dues_page(req):
    ...

def csv_response(name, header, rows):
    ...

@route('GET', '/export/dues\\.csv')
def export_dues(req):
    ...

@route('GET', '/export/payments\\.csv')
def export_payments(req):
    ...

@route('GET', '/export/plots\\.csv')
def export_plots(req):
    ...

@route('GET', '/export/waitlist\\.csv')
def export_waitlist(req):
    ...

@route('GET', '/users', 'admin')
def users_page(req, vals=None, errs=None, created=None):
    ...

@route('POST', '/users', 'admin')
def users_add(req):
    ...

@route('POST', '/users/(\\d+)/role', 'admin')
def users_role(req):
    ...

@route('POST', '/users/(\\d+)/reset', 'admin')
def users_reset(req):
    ...

@route('POST', '/users/(\\d+)/active', 'admin')
def users_active(req):
    ...
SETTING_LABELS = [('garden_name', 'Garden name'), ('fee.small', 'Default fee, small plot'), ('fee.standard', 'Default fee, standard plot'), ('fee.large', 'Default fee, large plot'), ('offers.expiry_days', 'Offer expiry (days)'), ('seasons.due_date', 'Default due date (MM-DD)'), ('waitlist.max_declines', 'Declines before removal from list'), ('waitlist.decline_policy', 'waitlist.decline_policy'), ('fees.new_holder_charge', 'fees.new_holder_charge'), ('payments.overpayment', 'payments.overpayment'), ('plots.release_with_balance', 'plots.release_with_balance'), ('auth.session_hours', 'Session inactivity timeout (hours)'), ('auth.max_failed', 'Failed sign-ins before lockout'), ('auth.lockout_minutes', 'Lockout duration (minutes)'), ('fees.prorate_from', 'fees.prorate_from (prorating month)'), ('assign.direct_with_active_entry', 'assign.direct_with_active_entry (direct assign to someone on the list)'), ('seasons.current_rule', 'seasons.current_rule (which season is current)'), ('offers.valid_on_expiry_date', 'offers.valid_on_expiry_date (offer still open on its expiry date)')]

@route('GET', '/settings', 'admin')
def settings_page(req, vals=None, errs=None):
    ...

@route('POST', '/settings', 'admin')
def settings_post(req):
    ...

@route('GET', '/audit', 'admin')
def audit_page(req):
    ...

class App:

    def __init__(self, db_file, secret, default_tz='UTC'):
        ...

    def sign(self, token):
        ...

    def unsign(self, value):
        ...

    def login(self, req, uid):
        ...

    def handle(self, method, raw_path, headers, body):
        ...

    def dispatch(self, req):
        ...

def make_handler(app):
    ...
REQUEST_TIMEOUT = 10
MAX_CONNECTIONS = 64

class BoundedServer(ThreadingHTTPServer):
    """One thread per connection, but never more than MAX_CONNECTIONS at once; extra connections are closed."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        ...

    def process_request(self, request, client_address):
        ...

    def process_request_thread(self, request, client_address):
        ...

def serve(app, host, port):
    ...
