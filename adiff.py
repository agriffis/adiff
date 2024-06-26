#!/usr/bin/env python
"""
adiff -- re-implementation of GNU wdiff plus extra features

Copyright 2005-2007,2011 Aron Griffis <agriffis@n01se.net>
Released under the terms of the GNU General Public License v3
"""

from collections import namedtuple
from itertools import *
from stat import *
import difflib, os, re, sys, time

__author__ = "Aron Griffis"
__copyright__ = "Copyright 2005-2007,2011 Aron Griffis"
__license__ = "GPL3"
__version__ = "2.0.%s" % re.search(r"\d+", r"$Revision: 2853 $").group(0)
__email__ = "agriffis@n01se.net"


def shorten(s, maxlen=20):
    """Shorten a string to maxlen chars, esp. for debugging."""
    halflen = int(maxlen / 2) - 2
    return s if len(s) <= maxlen else (s[:halflen] + "..." + s[-halflen:])[:maxlen]


def countlines(s):
    """Count the lines in `s`, being smart about the trailing newline."""
    if not isinstance(s, str):
        s = str(s)
    return s.count("\n") + int(bool(s and not s.endswith("\n")))


def isplit(patt, s, flags=None):
    """Return a generator that behaves similarly to re.split, with the
    following differences:

        1. It's a generator, not a list.

        2. Zero-width separators work properly
           (see http://bugs.python.org/issue3262)

        3. The sequence always includes the separators, similar to calling
           re.split(r'(patt)', s)

    Note there will always be an odd number of elements generated, because
    the list always starts and ends with content.
    """
    kwargs = {}
    if flags is not None:
        kwargs["flags"] = flags
    sepi = re.finditer(patt, s, **kwargs)

    class FakeMatchObj(object):
        def end(self):
            return 0

    prevm, m, nextm = None, FakeMatchObj(), next(sepi, None)

    while nextm:
        prevm, m, nextm = m, nextm, next(sepi, None)

        # There are two zero-width separator special cases to handle:
        #
        #   1. zero-width separator immediately following another separator
        #      (or the start-of-string), for example matching \b
        #      immediately after matching \s+
        #
        #   2. zero-width separator matching immediately prior to another
        #      separator, for example matching \b immediately prior to
        #      matching \s+
        #
        # The first case is easy to handle, see the "if...continue" below.
        #
        # The second case may be impossible to handle, because finditer
        # seems to consider the matches to be overlapping in that case
        # (presumably because they both start at the same cursor position,
        # even though the zero-width case doesn't consume any characters).
        # Therefore we include a loop to handle this second case, but
        # it is probably ineffective and in fact the only solution is
        # for the user to order their alternatives properly:
        # r'\s+|\b' rather than r'\b|\s+'

        if m.start() == m.end() == prevm.end():
            # Skip a zero-width separator immediately following
            # another separator (or start-of-string).
            continue

        while nextm and m.start() == m.end() == nextm.start():
            # Try to find a non-zero width separator at this point
            # before accepting this one. (but see the note above)
            m, nextm = nextm, next(sepi, None)

        # Yield the content prior to this separator.
        yield s[prevm.end() : m.start()]

        if m.start() == len(s):
            # Don't yield the end-of-string as a zero-length
            # separator. We're done.
            return

        # Yield this separator.
        yield s[m.start() : m.end()]

    # There's always content following the last separator.
    yield s[m.end() :]


class Tokenizer(object):
    """Split an input string into Tokens, where a Token consists of
    a `word`, the `separator` between it and the next Token, and the
    enumeration `idx` of itself in the list of Tokens.
    """

    Token = namedtuple("Token", ["word", "sep", "idx"])
    Token.__str__ = lambda t: t.word + t.sep
    Token.__repr__ = lambda t: "<Token idx=%r word=%r sep=%r>" % (
        t.idx,
        shorten(t.word),
        t.sep,
    )
    Token.__nonzero__ = lambda t: bool(t.word or t.sep)

    def __init__(self, s, boundary=r"\s+", flags=None):
        kwargs = {}
        if flags is not None:
            kwargs["flags"] = flags
        splits = list(isplit(boundary, s, **kwargs))

        # splits is either the empty list or an odd number.
        # Make the list even (so we have matching words and seps)
        # by either appending an empty sep or trimming the empty item from
        # the end of the list.
        if splits:
            if splits[-1]:
                splits.append("")
            else:
                splits.pop()
        self.tokens = list(
            map(Tokenizer.Token, splits[::2], splits[1::2], range(int(len(splits) / 2)))
        )

    def __repr__(self):
        return "<Tokenizer %r>" % self.tokens

    def __getitem__(self, i):
        return self.tokens[i]

    def __iter__(self):
        return iter(self.tokens)

    def join(self, first, limit):
        """Build a single token from a series of tokens, starting at
        `first` and ending prior to `limit`.
        """
        # Catch caller errors.
        assert 0 <= first <= len(self.tokens)
        assert first <= limit <= len(self.tokens)

        last = limit - 1
        if last < first:
            return None
        elif last == first:
            return self.tokens[first]
        else:
            # Check Tokenizer internal integrity.
            assert self.tokens[first].idx == first
            # Paste together the list of tokens except the final separator.
            word = (
                "".join(t.word + t.sep for t in self.tokens[first:last])
                + self.tokens[last].word
            )
            sep = self.tokens[last].sep
            return Tokenizer.Token(word, sep, first)


class Differ(object):
    """Base differ which tokenizes two input strings, splitting them on the
    provided boundary regex, and provides the blocks() generator.
    """

    Delta = namedtuple("Delta", ["op", "lhs", "rhs"])
    Delta.__repr__ = lambda d: "<Delta op=%s lhs=%r rhs=%r>" % (d.op, d.lhs, d.rhs)

    def __init__(self, a, b, boundary, ignore_case=False):
        self.atoks, self.btoks = Tokenizer(a, boundary), Tokenizer(b, boundary)
        # print >> sys.stderr, 'atoks=%r\nbtoks=%r' % (self.atoks, self.btoks)
        lower = (lambda s: s.lower()) if ignore_case else (lambda s: s)
        self.seqm = difflib.SequenceMatcher(
            None,
            [lower(t.word) for t in self.atoks],
            [lower(t.word) for t in self.btoks],
        )

    def blocks(self):
        """Generate tuples of the form:
                (op, lhs, rhs)
            where op is one of:
                'c': common,
                'l': unique to left (rhs=None),
                'r': unique to right (lhs=None),
                'd': different
            and lhs/rhs are tuples of the form:
                (word, sep)

        This is extremely similar to SequenceMatcher.get_opcodes() and
        should possibly use that or even be replaced by it.
        """
        curi = curj = 0
        for i, j, n in self.seqm.get_matching_blocks():
            # For each matching block, yield the preceding difference
            # then the matching block. Note get_matching_blocks() returns
            # a dummy match at the end with n=0, which works well for us.
            # print >> sys.stderr, "curi=%d i=%d curj=%d j=%d n=%d" % (curi,i,curj,j,n)
            lhs = self.atoks.join(curi, i)
            rhs = self.btoks.join(curj, j)
            op = "d" if lhs and rhs else "l" if lhs else "r" if rhs else None
            if op:
                # If join returned nothing, manufacture a falsy token for
                # the sake of the caller's line-counting.
                yield Differ.Delta(
                    op,
                    lhs or Tokenizer.Token("", "", i - 1),
                    rhs or Tokenizer.Token("", "", j - 1),
                )
            if n > 0:
                lhs = self.atoks.join(i, i + n)  # These are the same except
                rhs = self.btoks.join(j, j + n)  # possibly the seps.
                yield Differ.Delta("c", lhs, rhs)
            curi, curj = i + n, j + n


class LineDiffer(Differ):
    """Abstract linewise differ on which UnifiedDiffer, ContextDiffer and
    NormalDiffer are based.
    """

    def __init__(self, lhs, rhs, boundary=r"\n", reverse=False, **kwargs):
        if boundary != r"\n":
            diff = Differ(lhs, rhs, boundary, **kwargs)
            nlhs, nrhs = [], []
            for b in diff.blocks():
                if b.op == "c":
                    s = str(b.rhs) if reverse else str(b.lhs)
                    nlhs.append(s)
                    nrhs.append(s)
                elif b.op == "l":
                    nlhs.append(str(b.lhs))
                elif b.op == "r":
                    nrhs.append(str(b.rhs))
                elif b.op == "d":
                    sep = b.rhs.sep if reverse else b.lhs.sep
                    nlhs.append(b.lhs.word + sep)
                    nrhs.append(b.rhs.word + sep)
            lhs, rhs = "".join(nlhs), "".join(nrhs)
        super(LineDiffer, self).__init__(lhs, rhs, r"\n", **kwargs)

    def hunks(self):
        raise NotImplementedError("Implement this in a subclass")

    def get_diff(self):
        return "".join(self.hunks())

    @staticmethod
    def _addnl(s):
        """Add the standard indicator to the end of a block that isn't
        newline-terminated.
        """
        if not s.endswith("\n"):
            s = s + "\n\\ No newline at end of file\n"
        return s

    @classmethod
    def _preface(cls, s, tok):
        """Return stringified `tok` prefacing each line with `s` and also
        calling addnl().
        """
        return cls._addnl(re.sub(r"(?m)^", s, tok.word) + tok.sep)


class ContextDiffer(LineDiffer):
    """Context differ with variable context. This is also the base class
    for UnifiedDiffer.
    """

    def __init__(self, *args, **kwargs):
        self.context = kwargs.pop("context", 3)
        super(ContextDiffer, self).__init__(*args, **kwargs)

    def _trim(self, delta, dir, inner):
        assert delta.op == "c"
        # Since this is a common block in a linewise diff (so the
        # separators are consistently single newlines), we optimize
        # slightly by counting lines only on the LHS and generating the new
        # token using lhs.word.
        lines = countlines(delta.lhs)
        if lines <= (self.context * 2 if inner else self.context):
            return delta, False
        word = (
            "\n".join(delta.lhs.word.splitlines(False)[: self.context])
            if dir == "head"
            else "\n".join(delta.lhs.word.splitlines(False)[-self.context :])
        )
        adjust_idx = 0 if dir == "head" else lines - self.context
        trimmed_delta = Differ.Delta(
            op="c",
            lhs=Tokenizer.Token(
                word=word, sep=delta.lhs.sep, idx=delta.lhs.idx + adjust_idx
            ),
            rhs=Tokenizer.Token(
                word=word, sep=delta.rhs.sep, idx=delta.rhs.idx + adjust_idx
            ),
        )
        return trimmed_delta, True

    def context_blocks(self):
        """Generate a sequence of blocks which represent a single hunk with
        context. The first and last blocks, if they are context rather than
        delta, are trimmed appropriately.
        """
        blocki = iter(self.blocks())

        # Prime the pump. In the loop, `before` is set to the previous
        # ContextBlock's trailing `after`, but we need something to get started.
        before, next_delta = None, next(blocki, None)
        if next_delta and next_delta.op == "c":
            before, next_delta = next_delta, next(blocki, None)

        while next_delta:
            blocks = []
            if before:
                before, trimmed = self._trim(before, "tail", inner=False)
                blocks.append(before)
            while next_delta:
                delta, after, next_delta = (
                    next_delta,
                    next(blocki, None),
                    next(blocki, None),
                )
                blocks.append(delta)
                before = after
                if after:
                    after, trimmed = self._trim(after, "head", inner=bool(next_delta))
                    blocks.append(after)
                    if trimmed:
                        break
            yield blocks

    @staticmethod
    def _linerange(blocks, side):
        idx = getattr(blocks[0], side).idx
        count = sum(countlines(getattr(b, side)) for b in blocks)
        return "%d%s" % (
            idx + 1,  # one-based line numbering
            ",%d" % (idx + count) if count > 1 else "",
        )

    @classmethod
    def _hunk(cls, blocks, side):
        if not any(b.op == side[0] or b.op == "d" for b in blocks):
            return ""
        sign = "- " if side[0] == "l" else "+ "
        return "".join(
            cls._preface("  ", b.lhs)
            if b.op == "c"
            else cls._preface("! ", getattr(b, side))
            if b.op == "d"
            else cls._preface(sign, getattr(b, side))
            if b.op == side[0]
            else ""
            for b in blocks
        )

    def hunks(self):
        """Generate the context diff hunks, a la diff -c"""
        for blocks in self.context_blocks():
            yield "***************\n*** %s ****\n%s--- %s ----\n%s" % (
                self._linerange(blocks, "lhs"),
                self._hunk(blocks, "lhs"),
                self._linerange(blocks, "rhs"),
                self._hunk(blocks, "rhs"),
            )

    def get_diff(self):
        return "".join(self.hunks())


class UnifiedDiffer(ContextDiffer):
    """Unified differ with variable context."""

    @staticmethod
    def _linerange(blocks, side):
        idx = getattr(blocks[0], side).idx
        count = sum(countlines(getattr(b, side)) for b in blocks)
        return "%d%s" % (
            idx + 1,  # one-based line numbering
            ",%d" % count if count > 1 else "",
        )

    def _hunk(self, blocks):
        return "".join(
            self._preface(" ", b.lhs)
            if b.op == "c"
            else self._preface("-", b.lhs)
            if b.op == "l"
            else self._preface("+", b.rhs)
            if b.op == "r"
            else self._preface("-", b.lhs) + self._preface("+", b.rhs)
            for b in blocks
        )

    def hunks(self):
        """Generate the unified diff hunks, a la diff -u"""
        for blocks in self.context_blocks():
            yield "@@ -%s +%s @@\n%s" % (
                self._linerange(blocks, "lhs"),
                self._linerange(blocks, "rhs"),
                self._hunk(blocks),
            )


class NormalDiffer(LineDiffer):
    """Normal (old-style) diff generator."""

    @staticmethod
    def _linerange(tok):
        count = countlines(tok.word)
        return "%d%s" % (
            tok.idx + 1,  # zero-based vs. one-based
            ",%d" % (tok.idx + count) if count > 1 else "",
        )

    def hunks(self):
        for b in self.blocks():
            # print >> sys.stderr, b
            if b.op == "c":
                continue
            lines = []
            lines.append(
                "%s%s%s\n"
                % (
                    self._linerange(b.lhs),
                    "d" if b.op == "l" else "a" if b.op == "r" else "c",
                    self._linerange(b.rhs),
                )
            )
            if b.lhs:
                lines.append(self._preface("< ", b.lhs))
                if b.rhs:
                    lines.append("---\n")
            if b.rhs:
                lines.append(self._preface("> ", b.rhs))
            yield "".join(lines)

    def get_diff(self):
        return "".join(self.hunks())


class WordDiffer(Differ):
    """Word differ a la GNU wdiff."""

    def __init__(
        self,
        a,
        b,
        boundary=r"\s+",
        start_delete="[-",
        end_delete="-]",
        start_insert="{+",
        end_insert="+}",
        **kwargs
    ):
        super(WordDiffer, self).__init__(a, b, boundary=boundary, **kwargs)
        self.start_delete, self.end_delete = start_delete, end_delete
        self.start_insert, self.end_insert = start_insert, end_insert

    def hunks(self):
        for b in self.blocks():
            # print >> sys.stderr, b
            if b.op == "c":
                yield b.lhs.word + b.lhs.sep
            elif b.op == "l":
                yield "".join(
                    chain(
                        self.start_delete,
                        b.lhs.word,
                        self.end_delete,
                        b.lhs.sep,
                    )
                )
            elif b.op == "r":
                yield "".join(
                    chain(
                        self.start_insert,
                        b.rhs.word,
                        self.end_insert,
                        b.rhs.sep,
                    )
                )
            elif b.op == "d":
                yield "".join(
                    chain(
                        self.start_delete,
                        b.lhs.word,
                        self.end_delete,
                        self.start_insert,
                        b.rhs.word,
                        self.end_insert,
                        b.rhs.sep,
                    )
                )

    def get_diff(self):
        return "".join(self.hunks())


def file_header(f, prefix):
    """Provide a file header for unified and context diffs, e.g.
    +++ foo 2011-05-08 11:42:48.123456789 -0400
    """
    # Note that stat[ST_MTIME] is integer; stat.st_mtime is floating point.
    # We calculate the seconds ahead of time because neither time.localtime()
    # nor time.strftime() works with floating point seconds. In any case, the
    # end result is slightly different from the GNU diff result, because Python
    # stores floating point values internally as double, which isn't sufficient
    # precision. See http://bugs.python.org/issue11457
    mtime = os.stat(f).st_mtime
    float_seconds = time.localtime(mtime).tm_sec + (mtime - int(mtime))
    offset = -(time.altzone if time.daylight else time.timezone) / 60
    return "%s %s\t%s%02.09f %s%02d%02d" % (
        prefix,
        f,
        time.strftime("%Y-%m-%d %H:%M:", time.localtime(mtime)),
        float_seconds,
        "+" if offset >= 0 else "-",
        abs(offset) / 60,
        abs(offset) % 60,
    )


if __name__ == "__main__":
    import argparse

    ws_re = r"\s+"
    boundary_re = r"\s+|\b"

    shquote = lambda s: "'" + s.replace("'", r"'\''") + "'"

    class HelpFormatter30(argparse.HelpFormatter):
        def __init__(self, *args, **kwargs):
            kwargs["max_help_position"] = int(
                self.__class__.__name__.replace("HelpFormatter", "")
            )
            argparse.HelpFormatter.__init__(self, *args, **kwargs)

    parser = argparse.ArgumentParser(add_help=False, formatter_class=HelpFormatter30)
    parser.add_argument("file1", help="older file")
    parser.add_argument("file2", help="newer file")

    general_args = parser.add_argument_group("options")
    general_args.add_argument(
        "-h", "--help", action="help", help="Print this help and exit"
    )
    general_args.add_argument(
        "-V",
        "--version",
        action="version",
        version=__version__,
        help="Print program version and exit",
    )
    general_args.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        help="Fold character case while comparing",
    )
    general_args.add_argument(
        "-r",
        "--regex",
        default=ws_re,
        help="Override w/s regex (%s)" % shquote(ws_re),
        metavar="RE",
    )
    general_args.add_argument(
        "-b",
        "--word-boundaries",
        action="store_const",
        dest="regex",
        const=boundary_re,
        help="Break at word boundaries (same as -r %s)" % shquote(boundary_re),
    )
    general_args.add_argument(
        "--reverse", action="store_true", help="Prefer the new spacing in diff output"
    )

    diff_args = parser.add_argument_group("diff style")
    diff_args.add_argument(
        "--wdiff",
        action="store_const",
        dest="style",
        const="w",  # default='w',
        help="Output a word diff (default)",
    )
    diff_args.add_argument(
        "--normal",
        action="store_const",
        dest="style",
        const="n",
        help="Output a normal (old-style) diff",
    )
    diff_args.add_argument(
        "-c",
        action="store_const",
        dest="style",
        const="c",
        help="Output a context diff",
    )
    diff_args.add_argument(
        "-C",
        "--context",
        nargs="?",
        const=3,
        type=int,
        dest="context_lines",
        help="Output a context diff with NUM (default 3) lines of context",
        metavar="NUM",
    )
    diff_args.add_argument(
        "-u",
        action="store_const",
        dest="style",
        const="u",
        help="Output a unified diff",
    )
    diff_args.add_argument(
        "-U",
        "--unified",
        nargs="?",
        const=3,
        type=int,
        dest="unified_context",
        help="Output a unified diff with NUM (default 3) lines of context",
        metavar="NUM",
    )

    wdiff_args = parser.add_argument_group("wdiff options")
    wdiff_args.add_argument(
        "-w",
        "--start-delete",
        default="[-",
        help="String to mark start of delete region",
        metavar="STR",
    )
    wdiff_args.add_argument(
        "-x",
        "--end-delete",
        default="-]",
        help="String to mark end of delete region",
        metavar="STR",
    )
    wdiff_args.add_argument(
        "-y",
        "--start-insert",
        default="{+",
        help="String to mark start of insert region",
        metavar="STR",
    )
    wdiff_args.add_argument(
        "-z",
        "--end-insert",
        default="+}",
        help="String to mark end of insert region",
        metavar="STR",
    )

    args = parser.parse_args()

    if args.style is None:
        if args.unified_context is not None:
            args.style = "u"
        elif args.context_lines is not None:
            args.style = "c"
        else:
            args.style = "w"
    if args.unified_context is None:
        args.unified_context = 3
    if args.context_lines is None:
        args.context_lines = 3

    if args.style == "u":
        differ = UnifiedDiffer(
            open(args.file1).read(),
            open(args.file2).read(),
            boundary=args.regex,
            context=args.unified_context,
            ignore_case=args.ignore_case,
            reverse=args.reverse,
        )
        diff = differ.get_diff()
        if diff:
            print((file_header(args.file1, "---")))
            print((file_header(args.file2, "+++")))
            print(diff)

    elif args.style == "c":
        differ = ContextDiffer(
            open(args.file1).read(),
            open(args.file2).read(),
            boundary=args.regex,
            context=args.context_lines,
            ignore_case=args.ignore_case,
            reverse=args.reverse,
        )
        diff = differ.get_diff()
        if diff:
            print((file_header(args.file1, "***")))
            print((file_header(args.file2, "---")))
            print(diff)

    elif args.style == "n":
        differ = NormalDiffer(
            open(args.file1).read(),
            open(args.file2).read(),
            boundary=args.regex,
            ignore_case=args.ignore_case,
            reverse=args.reverse,
        )
        diff = differ.get_diff()
        if diff:
            print(diff)

    elif args.style == "w":
        differ = WordDiffer(
            open(args.file1).read(),
            open(args.file2).read(),
            boundary=args.regex,
            start_delete=args.start_delete,
            end_delete=args.end_delete,
            start_insert=args.start_insert,
            end_insert=args.end_insert,
            ignore_case=args.ignore_case,
        )
        diff = differ.get_diff()
        if diff:
            print(diff)
