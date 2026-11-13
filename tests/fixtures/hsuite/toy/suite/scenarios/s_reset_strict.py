def s_reset_strict(new, check):
    L = new()
    L.inc()
    L.reset()
    check("reset_zeroes", L.value() == 0)
