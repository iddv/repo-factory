# rule: a note name that leaves the vault is refused
# expect: exit 2 and nothing written outside the vault
# source: security review
# claim: security
import os

from _verify.driver import vault


def test_sec_1(tmp_path):
    rc, _, err = vault("put", "../../escape.txt", "x", env={"VAULT_DIR": str(tmp_path / "v")})
    assert rc == 2 and "refused" in err
    assert not os.path.exists(tmp_path / "escape.txt")
