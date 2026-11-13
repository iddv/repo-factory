def s_crash(new, check):
    new()
    check("before_crash", True)
    raise RuntimeError("boom")
