# rule: the health endpoint answers on localhost
# expect: 200 with ok true
# claim: c1
from _verify.driver import get, server


def test_l1():
    with server() as port:
        assert get(port, "/health") == (200, '{"ok": true}')
