#!/usr/bin/env python
"""Tests for adiff.py"""

import difflib, re, unittest
from adiff import Tokenizer

class TestCase(unittest.TestCase):
    def assertEqual(self, first, second, message=None):
        """Assert that two multi-line strings are equal, with diff output."""
        if message or not isinstance(first, basestring) \
                or not isinstance(second, basestring):
            super(TestCase, self).assertEqual(first, second, message)
        elif first != second:
            message = ''.join(difflib.ndiff(first.splitlines(True), second.splitlines(True)))
            self.fail("Strings don't match:\n" + message)

    def assertListsEqual(self, first, second, message=None):
        """Assert that two lists are equal, with diff output."""
        if message:
            super(self, TestCase).assertListsEqual(first, second, message)
        elif first != second:
            lines1 = [repr(e) for e in first]
            lines2 = [repr(e) for e in second]
            message = '\n'.join(difflib.ndiff(lines1, lines2))
            self.fail("Lists don't match:\n" + message)

class UtilTest(TestCase):
    """Tests of utility functions used in adiff.py"""

    def test_isplit(self):
        from adiff import isplit

        self.assertListsEqual(
                list(isplit(r'\s+', '')),
                [''])
        self.assertListsEqual(
                list(isplit(r'\s+', 'x')),
                ['x'])
        self.assertListsEqual(
                list(isplit(r'\s+', ' ')),
                ['', ' ', ''])
        self.assertListsEqual(
                list(isplit(r'\s+', 'foo  bar baz')),
                ['foo', '  ', 'bar', ' ', 'baz'])
        self.assertListsEqual(
                list(isplit(r'\b', 'foo  bar baz')),
                ['foo', '', '  ', '', 'bar', '', ' ', '', 'baz'])
        self.assertListsEqual(
                list(isplit(r' ', 'foo  bar baz')),
                ['foo', ' ', '', ' ', 'bar', ' ', 'baz'])
        self.assertListsEqual(
                list(isplit(r'a', 'foo  bar baz')),
                ['foo  b', 'a', 'r b', 'a', 'z'])

    def test_countlines(self):
        from adiff import countlines as cl

        self.assertListsEqual(
                [cl(''), cl('\n'), cl('\n\n')],
                [0, 1, 2])
        self.assertListsEqual(
                [cl('foo'), cl('foo\n'), cl('foo\nbar'), cl('foo\nbar\n')],
                [1, 1, 2, 2])

class TokenizerTest(TestCase):
    def assertTokenizerEqual(self, t, repr_t):
        token_list = ['word=%r sep=%r' % (tok.word, tok.sep) for tok in t]
        repr_list = re.split(r'(?:>, )?<Token idx=\d+ ', repr_t)
        repr_list[-1] = repr_list[-1][:-3]
        repr_list = repr_list[1:]
        self.assertListsEqual(token_list, repr_list)

    def test_simple_tokenizer(self):
        t = Tokenizer('a b c')

        # Test __repr__ along with the content of t
        self.assertTokenizerEqual(t, "<Tokenizer ["
            "<Token idx=0 word='a' sep=' '>, "
            "<Token idx=1 word='b' sep=' '>, "
            "<Token idx=2 word='c' sep=''>]>")

        # Test __getitem__ against __iter__
        for i, tok in enumerate(t):
            self.assertEqual(t[i], tok)

        # Test join
        self.assertEqual(repr(t.join(0, 0)), "None")
        self.assertEqual(repr(t.join(0, 1)), "<Token idx=0 word='a' sep=' '>")
        self.assertEqual(repr(t.join(0, 2)), "<Token idx=0 word='a b' sep=' '>")
        self.assertEqual(repr(t.join(0, 3)), "<Token idx=0 word='a b c' sep=''>")
        self.assertEqual(repr(t.join(1, 1)), "None")
        self.assertEqual(repr(t.join(1, 2)), "<Token idx=1 word='b' sep=' '>")
        self.assertEqual(repr(t.join(1, 3)), "<Token idx=1 word='b c' sep=''>")
        self.assertEqual(repr(t.join(2, 2)), "None")
        self.assertEqual(repr(t.join(2, 3)), "<Token idx=2 word='c' sep=''>")
        self.assertRaises(AssertionError, t.join, -1, 4) # first too small
        self.assertRaises(AssertionError, t.join, 4, 4)  # first too large
        self.assertRaises(AssertionError, t.join, 3, 5)  # limit too large
        self.assertRaises(AssertionError, t.join, 2, 1)  # limit smaller than first

    def test_empty_tokenizers(self):
        for boundary in [r'\s+', '']:
            t = Tokenizer('', boundary)
            self.assertEqual(repr(t), "<Tokenizer []>")
            self.assertRaises(StopIteration, lambda: next(iter(t)))
            self.assertRaises(IndexError, lambda: t[0])
            self.assertEqual(t.join(0, 0), None)
            self.assertRaises(AssertionError, t.join, 0, 1)

    def test_tokenizer_empty_boundary(self):
        # Empty boundary should split char-by-char.
        t = Tokenizer('a b\ncd\n', '')
        self.assertTokenizerEqual(t, "<Tokenizer ["
            "<Token idx=0 word='a' sep=''>, "
            "<Token idx=1 word=' ' sep=''>, "
            "<Token idx=2 word='b' sep=''>, "
            "<Token idx=3 word='\\n' sep=''>, "
            "<Token idx=4 word='c' sep=''>, "
            "<Token idx=5 word='d' sep=''>, "
            "<Token idx=6 word='\\n' sep=''>]>")
        assert all(tok.sep == '' for tok in t)

    def test_tokenizer_newline_boundary(self):
        # Newline boundary should leave internal w/s intact.
        t = Tokenizer('a b\ncd\n', '\n')
        self.assertTokenizerEqual(t, "<Tokenizer ["
            "<Token idx=0 word='a b' sep='\\n'>, "
            "<Token idx=1 word='cd' sep='\\n'>]>")
        assert all(tok.sep == '\n' for tok in t)

        # If there's a word after the last split boundary,
        # then the final separator should be the empty string.
        t = Tokenizer('a b\ncd\ne', '\n')
        self.assertTokenizerEqual(t, "<Tokenizer ["
            "<Token idx=0 word='a b' sep='\\n'>, "
            "<Token idx=1 word='cd' sep='\\n'>, "
            "<Token idx=2 word='e' sep=''>]>")
        assert all(tok.sep == '\n' for tok in t[:-1])
        self.assertEqual(t[-1].sep, '')

if __name__ == '__main__':
    unittest.main()
