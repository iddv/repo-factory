"""The factory's autonomous, continuous rule set (docs/design/rules.md).

``model`` (Rule), ``store`` (RuleStore, per build, in the den), ``miner`` (mining modes),
``validate`` ((a)-(d)), ``arbiter`` (code wrong / rule wrong / ambiguous), ``score``
(running rules, pass rate, gate part), ``loop`` (the closing review loop), ``engine``
(all of it for one build) and ``llm`` (backends)."""

from .engine import DEFAULTS, RulesEngine, settings
from .miner import Context, Miner
from .model import Rule
from .store import RuleStore, store_root

__all__ = [
    "DEFAULTS",
    "Context",
    "Miner",
    "Rule",
    "RuleStore",
    "RulesEngine",
    "settings",
    "store_root",
]
