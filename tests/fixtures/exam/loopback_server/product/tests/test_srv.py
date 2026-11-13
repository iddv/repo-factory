import unittest

from srv.__main__ import Handler, Server


class SrvTest(unittest.TestCase):
    def test_reuses_the_address(self):
        self.assertTrue(Server.allow_reuse_address)

    def test_handler_is_quiet(self):
        self.assertIsNone(Handler.log_message(None))
