def s_count(new, check):
    L = new()
    check("first_inc_ok", L.inc().ok)
    check("value_is_1", L.value() == 1)
    for _ in range(5):
        L.inc()
    check("limit_holds_at_3", L.value() == 3, L.value())
    check("unruled_row", True)
    check("either_row", L.value() == 99)
