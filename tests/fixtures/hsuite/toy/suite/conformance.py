def c_inc(new, check):
    L = new()
    check("inc_ok", L.inc().ok)
    check("value_is_1", L.value() == 1)
