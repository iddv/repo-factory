"""The page in a real browser: ``repo-factory ui``'s server on loopback with the fake harness (a
loop with no model, no network), and the built page. It opens ``/`` and sees Builds empty with
New task, sends a typed task and sees its row say Brief, opens a build's six stages and a log,
and opens every settings page; in light and dark, 1400 and 390 pixels wide. No page throws, logs
a console error, gets an HTTP error, or is wider than the window.

Needs the build (``python3 scripts/build_ui.py``) and Playwright with its headless Chromium
(``uv sync --group browser``, then ``playwright install chromium-headless-shell``). Where they are
missing the tests are skipped with the reason; CI's ``browser`` job sets
``REPO_FACTORY_BROWSER_REQUIRED=1``, so there they fail instead.
"""

import json
import os
import re
import threading
import time

import pytest

from repo_factory import tokens as tokens_mod
from repo_factory.ui.routes import static
from repo_factory.ui.server import make_server

from .loop_common import make_loop

pytestmark = [pytest.mark.browser, pytest.mark.built_ui]
REQUIRED = os.environ.get("REPO_FACTORY_BROWSER_REQUIRED") == "1"
# a real build, seen before conftest's stand-in (an empty module) takes its place
BUILT = (static.APP_DIR / ".vite" / "manifest.json").is_file()
SIZES = [("wide", 1400, 900), ("phone", 390, 844)]
SCHEMES = ["light", "dark"]
SETTINGS = ["claude", "github", "building", "publishing", "spend", "appearance"]
TASK = "a command-line tool that turns a folder of Markdown notes into one searchable HTML page"
NOTICE = (
    "repo-factory is an experiment. It runs code a model wrote and can publish under your "
    "GitHub account. It costs real money or plan usage: about $10 and an hour a build on our "
    "record. Its own checks are not proof. Read what it builds."
)
# words that are only ever a bug on a page
NOT_WORDS = re.compile(r"\b(null|undefined|NaN)\b|\[object |Traceback")


@pytest.fixture(scope="module")
def browser():
    if not BUILT:
        if REQUIRED:
            pytest.fail("the web UI is not built: python3 scripts/build_ui.py")
        pytest.skip("the web UI is not built: python3 scripts/build_ui.py")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        if REQUIRED:
            raise
        pytest.skip(f"Playwright is not installed ({e}): uv sync --group browser")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - no browser here
            if REQUIRED:
                raise
            pytest.skip(
                f"no headless Chromium ({str(e).splitlines()[0]}): "
                "playwright install chromium-headless-shell"
            )
        yield b
        b.close()


@pytest.fixture
def server(tmp_path):
    """The page's server on a free loopback port, over a loop with nothing in it; ``mint()`` is
    a one-time sign-in code (writes need one, as they do for a person)."""
    lp = make_loop(tmp_path)
    lp.cfg.tasks_dir.mkdir(parents=True, exist_ok=True)
    store = tokens_mod.Store(tmp_path / "tokens")
    srv = make_server(
        lp.cfg.tasks_dir, port=0, harness="fake", loop_home=lp.cfg.home, tokens=store, require=True
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield {
            "base": f"http://127.0.0.1:{srv.server_address[1]}",
            "mint": store.mint_code,
            "tasks": lp.cfg.tasks_dir,
        }
    finally:
        srv.shutdown()
        srv.server_close()


def until(page, js: str, timeout_s: float = 15.0) -> None:
    """Wait until ``js`` is true on the page. Not ``page.wait_for_function``: it evaluates a
    string, which the page's CSP (script-src 'self') may refuse; ``evaluate`` of a function is
    not eval."""
    end = time.monotonic() + timeout_s
    while not page.evaluate(f"() => Boolean({js})"):
        if time.monotonic() > end:
            raise AssertionError(f"not true after {timeout_s:g} s: {js}")
        page.wait_for_timeout(50)


def open_page(browser, server, width, height, scheme, hash_="#/builds", notice=False):
    """A signed-in page at ``hash_``, its problems collected in ``page.problems``."""
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
    problems: list[str] = []
    page.on("pageerror", lambda e: problems.append(f"error: {e}"))
    page.on(
        "console",
        lambda m: (
            problems.append(f"console: {m.text}")
            if m.type == "error" and not m.text.startswith("Failed to load resource")
            else None
        ),
    )
    page.on(
        "response", lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 400 else None
    )
    page.on(
        "requestfailed",
        lambda r: None if r.url.endswith("/api/stream") else problems.append(f"failed {r.url}"),
    )
    page.problems = problems
    if not notice:
        page.add_init_script("localStorage.setItem('repo-factory.notice.dismissed', '1')")
    nxt = "/" + hash_.replace("#", "%23")
    page.goto(f"{server['base']}/auth/open?code={server['mint']()}&next={nxt}")
    until(page, "document.documentElement.dataset.ui === 'ready'")
    return page


def look(page, where: str) -> list[str]:
    """What is wrong with the page as it is: the problems seen, a word that is a bug, a page
    wider than its window."""
    out = list(page.problems)
    text = page.inner_text("body")
    out += [f"says {m.group(0)!r}" for m in NOT_WORDS.finditer(text)][:3]
    wide = page.evaluate(
        "() => [document.documentElement.scrollWidth, innerWidth,"
        " document.getElementById('main').scrollWidth,"
        " document.getElementById('main').clientWidth]"
    )
    if wide[0] > wide[1] + 1 or wide[2] > wide[3] + 1:
        out.append(f"wider than the window: {wide}")
    page.problems.clear()
    return [f"{where}: {p}" for p in out]


def theme_is(page, scheme: str) -> bool:
    """The page draws in the scheme asked for (the body's background is dark or light)."""
    rgb = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    r, g, b = (int(x) for x in re.findall(r"\d+", rgb)[:3])
    return (r + g + b < 3 * 128) == (scheme == "dark")


def test_the_first_screen_says_the_notice_and_builds_empty_with_new_task(browser, server):
    for name, w, h in SIZES:
        page = open_page(browser, server, w, h, "light", "#/builds", notice=True)
        until(page, "document.querySelector('#builds-empty')")
        assert page.inner_text("#notice").startswith(NOTICE)
        buttons = page.locator("main .btn:not(#notice-dismiss)")
        assert buttons.count() == 1 and buttons.first.inner_text() == "New task"
        assert page.locator("nav.nav .item").all_inner_texts() == ["Builds", "Settings"]
        problems = look(page, name)
        page.click("#notice-dismiss")
        page.reload()
        until(page, "document.querySelector('#builds-empty')")
        assert page.locator("#notice").count() == 0  # remembered
        page.close()
        assert not problems, problems


def test_a_typed_task_appears_at_once_and_says_brief(browser, server):
    page = open_page(browser, server, 1400, 900, "light")
    until(page, "document.querySelector('#builds-empty')")
    page.click("#new-task")
    page.fill("#task-text", TASK)
    page.click("#task-build")
    # at once: the row is on the list, saying Brief, before the server has answered
    until(
        page,
        "[...document.querySelectorAll('#build-rows .row')].some((r) => "
        "r.textContent.includes('Brief'))",
        2,
    )
    # then from the server: the task, still at its brief (the fake harness writes no brief)
    until(page, "!document.querySelector('#build-rows [data-key^=\"pending:\"]')", 20)
    rows = page.locator("#build-rows .row")
    assert rows.count() == 1
    assert "A command-line tool" in rows.first.inner_text() or TASK[:20] in rows.first.inner_text()
    assert "Brief" in rows.first.inner_text()
    assert page.locator("#stages .stage").count() == 6
    problems = look(page, "new task")
    page.close()
    assert not problems, problems


def _product(tasks, name="sidedoor"):
    """A product with three Builds in its event log: a fresh one, a docs revision of it, and a
    second revision stopped while it packaged."""
    d = tasks / name
    (d / ".den").mkdir(parents=True)
    (d / "spec.md").write_text(f"# {name}\n")
    lines, clock = [], [1_791_500_000.0]

    def ev(n, type_, **f):
        clock[0] += 60
        lines.append(
            json.dumps({"v": 1, "ts": clock[0], "task": name, "build": n, "type": type_, **f})
        )

    every = [
        "scope",
        "build",
        "claims",
        "verify",
        "oracle",
        "repair",
        "reverify",
        "readiness",
        "deliver",
    ]
    ev(1, "build_started", pipeline="v5", stages=every, title="Sidedoor")
    for s in every:
        ev(1, "v5_stage", stage=s, status="started")
        ev(1, "v5_stage", stage=s, status="done", wall_s=60)
    ev(1, "build_finished", status="ok", cost_usd=1.0, wall_s=600)
    for n in (2, 3):
        ev(
            n,
            "build_started",
            pipeline="v5",
            kind="revise",
            revises=1,
            origin="ui",
            mode="docs",
            stages=["build", "verify", "repair", "reverify", "deliver"],
            instructions="a word",
        )
        for s in ("scope", "claims", "oracle", "readiness"):
            ev(n, "v5_stage", stage=s, status="skipped", why="carried from round 1")
        ev(n, "v5_stage", stage="build", status="started")
        ev(n, "v5_stage", stage="build", status="done", wall_s=30)
        ev(n, "v5_stage", stage="deliver", status="started")
        if n == 2:
            ev(n, "v5_stage", stage="deliver", status="done")
            ev(n, "build_finished", status="ok", cost_usd=0.1, wall_s=300)
    (d / ".den" / "events.jsonl").write_text("\n".join(lines) + "\n")
    return d


def test_a_builds_page_shows_six_stages_and_opens_a_log(browser, server):
    _product(server["tasks"])
    problems = []
    for name, w, h in SIZES:
        for scheme in SCHEMES:
            page = open_page(browser, server, w, h, scheme, "#/builds/sidedoor")
            until(page, "document.querySelectorAll('#stages .stage').length === 6")
            names = page.locator("#stages .stage .name strong").all_inner_texts()
            assert names == ["Brief", "Plan", "Build", "Check", "Fix", "Publish"]
            assert page.locator("#s-now").count() == 1
            assert theme_is(page, scheme), f"{name} {scheme}: drawn in the other scheme"
            problems += look(page, f"build {name} {scheme}")
            page.locator('#stages [data-stage="build"] a').click()
            until(page, "document.querySelector('#log')")
            problems += look(page, f"log {name} {scheme}")
            page.close()
    assert not problems, problems


def test_every_settings_page_draws_in_light_and_dark_wide_and_on_a_phone(browser, server):
    problems = []
    for name, w, h in SIZES:
        for scheme in SCHEMES:
            page = open_page(browser, server, w, h, scheme, "#/settings")
            for p in SETTINGS:
                page.evaluate(f"() => {{ location.hash = '#/settings/{p}'; }}")
                until(page, f"document.querySelector('#set-{p} h2')")
                page.wait_for_timeout(300)  # its reads come back
                assert (
                    page.locator(f'.subnav a[aria-current="page"][href="#/settings/{p}"]').count()
                    == 1
                )
                problems += look(page, f"settings/{p} {name} {scheme}")
            assert theme_is(page, scheme), f"{name} {scheme}: drawn in the other scheme"
            page.close()
    assert not problems, problems


def test_appearance_switches_light_and_dark(browser, server):
    page = open_page(browser, server, 1400, 900, "light", "#/settings/appearance")
    until(page, "document.querySelector('#theme')")
    page.check('#theme input[value="dark"]')
    assert theme_is(page, "dark")
    page.reload()
    until(page, "document.documentElement.dataset.ui === 'ready'")
    assert theme_is(page, "dark")  # kept, and set before the first paint
    page.check('#theme input[value="light"]')
    assert theme_is(page, "light")
    problems = look(page, "appearance")
    page.close()
    assert not problems, problems
