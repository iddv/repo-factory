def s_top(new, check):
    L = new()
    check("most_common_first", L.top_words("b a b", 1).body.get("value") == ["b"])
    check("ties_alphabetical", L.top_words("b a", 2).body.get("value") == ["a", "b"])
    check("case_folded", L.top_words("A a b", 1).body.get("value") == ["a"])
    check("undeclared_row", True)
