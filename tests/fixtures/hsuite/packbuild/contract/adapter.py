from driver import Driver, Result, Unsupported
from textstats import core


class Adapter(Driver):
    def _call(self, fn, *a):
        try:
            return Result(True, 0, {"value": fn(*a)})
        except Exception as e:  # noqa: BLE001
            return Result(False, 1, {}, repr(e))

    def word_count(self, text):
        return self._call(core.word_count, text)

    def top_words(self, text, n):
        return self._call(core.top_words, text, n)

    def configure(self, rule_id, value):
        settings = getattr(core, "SETTINGS", None)
        if rule_id == "top_words.case_insensitive" and settings is not None:
            settings["case_insensitive"] = bool(value)
            return
        raise Unsupported(f"config.{rule_id}")

    def __init__(self):
        settings = getattr(core, "SETTINGS", None)
        if settings is not None:
            settings["case_insensitive"] = True     # a fresh system: the build's default
