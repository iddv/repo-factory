def s_counts(new, check):
    L = new()
    check("two_words", L.word_count("a b").body.get("value") == 2)
    check("double_space_is_two_words", L.word_count("a  b").body.get("value") == 2)
    r = L.word_count("")
    check("empty_is_zero", r.ok and r.body.get("value") == 0, r)
    check("broken_mechanics_row", L.no_such_operation())
