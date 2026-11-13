"""Text statistics (a second, buggier reference: splits on single spaces, refuses empty text)."""
from collections import Counter


def char_count(text):
    return len(text)


def word_count(text):
    if not text:
        raise ValueError("empty text")
    return len(text.split(" "))


def top_words(text, n):
    counts = Counter(w.lower() for w in text.split())
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]
