"""Text statistics with an operator setting: whether top_words folds case (default on)."""
from collections import Counter

SETTINGS = {"case_insensitive": True}


def char_count(text):
    return len(text)


def word_count(text):
    return len(text.split())


def top_words(text, n):
    fold = SETTINGS["case_insensitive"]
    counts = Counter(w.lower() if fold else w for w in text.split())
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
