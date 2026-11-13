import os
import tempfile
import unittest

import vault


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VAULT_DIR"] = self.tmp

    def test_put(self):
        self.assertTrue(vault.put("a.txt", "x").is_file())

    def test_no_escape(self):
        with self.assertRaises(ValueError):
            vault.put("../x.txt", "x")

    def test_token_is_fresh(self):
        self.assertNotEqual(vault.new_token(), vault.new_token())
