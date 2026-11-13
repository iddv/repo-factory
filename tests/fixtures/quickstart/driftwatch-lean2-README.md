# Driftwatch

Driftwatch keeps a running history of what your vendors change. It is for teams that depend on
hosted services such as databases, identity providers and secrets managers, and keep getting
caught out when a vendor quietly changes a default, deprecates something, or narrows what a plan
covers.

You host it yourself and point it at your vendors' public changelogs and release feeds. It polls
them, keeps every entry with an exact snapshot of what the vendor published, and flags each
**changed default**, **deprecation** and **narrowed coverage** for your team to review. You can then:

- see which vendors churn the most, by month or quarter;
- before a renewal or an upgrade, pull up the evidence for one vendor and export it as
  Markdown, CSV or printable HTML.

Everyone on the team signs in with their own account and has the same rights.

## Quick start

You need Linux or macOS with Python 3.11 or newer. In a copy of this repository, run:

```sh
./driftwatch.sh
```

It prints something like:

```
Driftwatch is running at http://127.0.0.1:8080/
First run: open this one-time setup URL to create the first account:
  http://127.0.0.1:8080/setup/8sK2...
```

Open the setup URL, choose a username and a password (at least 12 characters), and you land on
the empty Vendors screen. Press **Ctrl+C** to stop the service.

To look around with sample data instead, use a fresh install and run:

```sh
./driftwatch.sh --demo
```

## Install

`./driftwatch.sh` does the whole install. The packages Driftwatch uses (listed in
`requirements.txt`) ship with it in `vendor/`, so nothing is downloaded or installed and no network
access is needed. On its first run it creates the data directory (`data/` inside the Driftwatch
folder by default), the database and its schema, and a random session secret.

It runs with `python3` when that is Python 3.11 or newer, else with a Python 3.11+ that `uv` knows
about.

## Configuration

Driftwatch works without any configuration. To change a setting, create `driftwatch.toml` in the
Driftwatch folder, or pass another file with `--config FILE` or the `DRIFTWATCH_CONFIG`
environment variable. A file you name explicitly must exist.

Every option can also be set with an environment variable named `DRIFTWATCH_<SECTION>_<KEY>`,
for example `DRIFTWATCH_SERVER_PORT=9090`. Environment variables win over the file. Lists in
