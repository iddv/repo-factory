"""The build loop: typed tasks in, published repositories out.

    a task (the page's New task, ``repo-factory loop add``) -> the brief (one no-tools call) ->
    queue (a task folder) -> unattended v5 builds -> the gate -> waits for your Publish (a private
    GitHub repository; public only after ``repo-factory loop approve``).

Modules: ``config`` (settings, paths), ``ledger`` (every idea's state and history, SQLite),
``intake``, ``pitch``, ``builds`` (spawning and watching ``repo-factory build``), ``gate``,
``secrets`` (the pre-push scan), ``publish`` (``gh``), ``feed`` (Atom and the digest),
``signals`` (the hook for stars, issues, traffic), ``daemon`` (the tick loop) and ``cli``
(``repo-factory loop ...``). See docs/loop.md."""
