def c_word_count(new, check):
    check("one_word", new().word_count("hello").body.get("value") == 1)


def c_top_words(new, check):
    check("one_top_word", new().top_words("hello", 1).body.get("value") == ["hello"])
