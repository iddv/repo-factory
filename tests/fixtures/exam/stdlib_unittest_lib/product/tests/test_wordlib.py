import unittest

from wordlib import count


class CountTest(unittest.TestCase):
    def test_three_words(self):
        self.assertEqual(count("a b c"), 3)

    def test_empty(self):
        self.assertEqual(count(""), 0)


if __name__ == "__main__":
    unittest.main()
