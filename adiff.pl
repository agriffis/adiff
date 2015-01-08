#!/usr/bin/perl -w
#
# Copyright 2005 Aron Griffis <aron griffis1 net>
# Released under the terms of the GNU General Public License v2
#
# adiff -- re-implementation of GNU wdiff plus extra features
#

use Getopt::Long;
use strict;

######################################################################
# Global vars
######################################################################

(my $version = '$Revision: 2853 $') =~ s/.*?(\d.*\d).*/adiff version 1.4 ($1)\n/;
$version .= "Copyright 2005-2007 Aron Griffis\n";
$version .= "Released under the terms of the GNU General Public License v2\n";
my (%tmp_files, %tmp_dirs);
my ($delstart, $delend, $insstart, $insend) = qw([- -] {+ +});
my ($regex, $bregex) = ('\s+', '(?:\s+|\b)');
my (%opt, @diffargs);
my $usage = <<EOT;
usage: adiff [OPTION]... FILE1 FILE2

  -h --help                  Print this help
  -V --version               Print program version

  -i --ignore-case           Fold character case while comparing
  -w --start-delete=STRING   String to mark beginning of delete region
  -x --end-delete=STRING     String to mark end of delete region
  -y --start-insert=STRING   String to mark beginning of insert region
  -z --end-insert=STRING     String to mark end of insert region

  -c -C NUM --context[=NUM]  Output NUM (default 3) lines of copied context
  -u -U NUM --unified[=NUM]  Output NUM (default 3) lines of unified context
  --normal                   Output a normal diff

  -r --regex=RE              Override w/s regex ($regex)
  -b --word-boundaries       Break at word boundaries instead of w/s
                             (same as -r '$bregex')

Report bugs to <agriffis\@n01se.net>
EOT

######################################################################
# Temps and signal handling
######################################################################

sub cleanup {
    unlink(keys %tmp_files) if %tmp_files;
    system("rmdir " . join " ", keys %tmp_dirs) if %tmp_dirs;
}

sub cleanup_exit { cleanup(); exit @_; }
sub cleanup_sig { print STDERR "dying on SIG@_\n"; cleanup_exit 1; }
sub cleanup_die { cleanup_exit 1; }

sub tmp_file {
    my ($f) = `mktemp -t @_`;
    chomp $f;
    cleanup_die unless $f;
    $tmp_files{$f} = 1;
    return $f;
}

sub tmp_dir {
    my ($d) = `mktemp -td @_`;
    chomp $d;
    cleanup_sig unless $d;
    $tmp_dirs{$d} = 1;
    return $d;
}

use sigtrap 'handler' => \&cleanup_sig, 'normal-signals';
#$SIG{'__DIE__'} = \&cleanup_die;

######################################################################
# Main
######################################################################

package main;

sub grab_words {
    my ($count, $sref, $pref) = @_;
    my ($words) = '';

    return "" if $count < 1;
    $$pref += $count if $pref;

    while ($count-- > 0) {
        $$sref =~ s/.+?(?:$regex|\z)//o or die;
        $words .= $&;
    }

    return $words;
}

sub gen_word_list {
    my ($to, $from) = @_;
    my ($text, $word);

    open(F, $from) or die; 
    { local $/ = undef; $text = $_ = <F>; s/^\s*//; s/\s*\z//; }
    open(F, ">$to") or die; 
    while (length $_) {
        $word = grab_words(1, \$_);
        $word =~ s/$regex//go;
        print F "$word\n";
    }
    close(F) or die;

    return $text;
}

sub wdiff {
    my ($a, $b) = @_;
    my ($tmp_a, $tmp_b) = (tmp_file(), tmp_file());
    my ($oldpos, $newpos, $ws, $output) = (1, 1, '', '');
    my ($diff, @diffs, $old, $new);
    my ($oldstart, $oldend, $code, $newstart, $newend, $n, $o);
    my ($diffcmd) = $opt{'i'} ? 'diff -i' : 'diff';

    # compare word lists, store results in @diffs.
    # original text goes in $old and $new
    $old = gen_word_list($tmp_a, $a);
    $new = gen_word_list($tmp_b, $b);
    $tmp_a =~ s/'/\\'/g; $tmp_b =~ s/'/\\'/g;           # quote correctly!
    open(F, "$diffcmd '$tmp_a' '$tmp_b'|") or die;
    @diffs = grep /^\d/, <F>;
    close(F);

    while($diff = shift @diffs) {
        ($oldstart, $oldend, $code, $newstart, $newend) =
            ($diff =~ /(\d+)(?:,(\d+))?([acd])(\d+)(?:,(\d+))?/);
        $oldstart++ if $code eq "a";
        $newstart++ if $code eq "d";
        $oldend ||= $oldstart;
        $newend ||= $newstart;
        $o = grab_words($oldstart - $oldpos, \$old, \$oldpos);
        $n = grab_words($newstart - $newpos, \$new, \$newpos);

        # Catch up to the starting index in this diff.
        # If the current diff is a deletion, use the ws from the old text
        # to end this block of text so that the ws between the block and
        # deletion makes sense.  Save the ws from the new text to
        # terminate the deletion itself.
        if ($code eq 'c' or $code eq 'd') {
            ($n, $ws) = ($n =~ /(.*?)(\s*)\z/s);
            $o =~ /\s*\z/;
            $output .= $n . $&;
        } else {
            $output .= $n;
        }

        # Now process the current diff.
        if ($code eq 'c' or $code eq 'd') {
            $o = grab_words($oldend - $oldstart + 1, \$old, \$oldpos);
            $o =~ s/\s*\z//;
            $output .= "$delstart$o$delend";
            # $& here is purely for when the first thing in the file is a diff
            $output .= ($ws || $&) if $code eq 'd';
        }
        if ($code eq 'c' || $code eq 'a') {
            $n = grab_words($newend - $newstart + 1, \$new, \$newpos);
            $n =~ /^(.*?)(\s*)\z/s;
            $output .= "$insstart$1$insend$2";
        }
    }

    # There may be text remaining to print in $new
    return $output . $new;
}

# Allow bundling of options
Getopt::Long::Configure("bundling");

# Collect options and arguments to diff
sub diffarg {
    my ($opt, $arg) = @_;
    $opt = "-$opt" if length $opt > 1;
    push @diffargs, "-$opt";
    return if $opt eq 'u' or $opt eq 'c';
    push @diffargs, $arg if $arg != -1;
}

# Parse the options on the cmdline.  Put the short versions first in
# each optionstring so that the hash keys are created using the short
# versions.  For example, use 'q|qar', not 'qar|q'.
my ($result) = GetOptions(
    \%opt,
    'h|help',
    'V|version',
    'b|word-boundaries', => sub { $regex = $bregex },
    'i|ignore-case',
    'r|regex=s'        => \$regex,
    'w|start-delete=s' => \$delstart,
    'x|end-delete=s'   => \$delend,
    'y|start-insert=s' => \$insstart,
    'z|end-insert=s'   => \$insend,
    'c'                => \&diffarg,
    'C=i'              => \&diffarg,
    'context:-1'       => \&diffarg,
    'u'                => \&diffarg,
    'U=i'              => \&diffarg,
    'unified:-1'       => \&diffarg,
    'normal'           => \&diffarg,
);
if ($opt{'h'}) { print STDERR $usage; cleanup_exit 0 }
if ($opt{'V'}) { print STDERR $version; cleanup_exit 0 }
die "adiff: two file arguments required\n$usage" unless @ARGV == 2;

if (@diffargs) {
    $delstart = $delstart x 3;
    $delend = $delend x 3;
    $insstart = $insstart x 3;
    $insend = $insend x 3;
    $opt{'1'} = $opt{'2'} = $opt{'3'} = $opt{'n'} = undef;

    my ($wdiff) = wdiff(@ARGV);
    my ($no_inserted, $no_deleted) = ($wdiff, $wdiff);

    # keep deleted portions, drop inserted portions
    # {---a---} => a
    # {---a---}{+++b+++} => a
    $no_inserted =~ s{
        \Q$delstart\E(?>(.*?)\Q$delend\E)
        (?:\Q$insstart\E.*?\Q$insend\E)?  }{$1}gsx;
    # ws1{+++b+++}ws2 => ws1
    $no_inserted =~ s{ \Q$insstart\E.*?\Q$insend\E\s* }{}gsx;

    # keep inserted portions, drop deleted portions
    # {---a---}{+++b+++} => b
    # {+++b+++} => b
    $no_deleted =~ s{
        (?>\Q$delstart\E.*?\Q$delend\E)?
        \Q$insstart\E(.*?)\Q$insend\E    }{$1}gsx;
    # ws1{---a---}ws2 => ws2
    $no_deleted =~ s{ \s*\Q$delstart\E.*?\Q$delend\E }{}gsx;

    # now compare them
    my ($tmp_a, $tmp_b) = (tmp_file(), tmp_file());
    open(F, ">$tmp_a") or die; print F $no_inserted; close(F) or die;
    open(F, ">$tmp_b") or die; print F $no_deleted; close(F) or die;
    system('diff', @diffargs, $tmp_a, $tmp_b);
    cleanup_exit $?;
}

print wdiff(@ARGV);
cleanup_exit 0;

__END__

=head1 NAME

adiff - wordwise diff

=head1 SYNOPSIS

B<wdiff> [I<OPTION>]... I<FILE1> I<FILE2>

=head1 DESCRIPTION

This tool is a replacement for GNU wdiff.  It's shorter, slower,
written in Perl instead of C, doesn't have as many options, but
provides some others.

=head1 WDIFF OPTIONS

These options come from the GNU wdiff program, which adiff mostly
reimplements, minus a few options, minus a few bugs.

=over

=item B<-i --ignore-case>

Ignore case when comparing.  The default is to be case-sensitive.

=item B<-w> I<STRING> B<--start-delete> I<STRING>

Use STRING as the "start delete" string. This string will be output
prior to every sequence of deleted text, to mark where it starts. By
default, the start delete string is B<[->.

=item B<-x> I<STRING> B<--end-delete> I<STRING>

Use STRING as the "end delete" string. This string will be output
following every sequence of deleted text, to mark where it ends. By
default, the end delete string is B<-]>.

=item B<-y> I<STRING> B<--start-insert> I<STRING>

Use STRING as the "start insert" string. This string will be output
prior to every sequence of inserted text, to mark where it starts. By
default, the start insert string is B<{+>.

=item B<-z> I<STRING> B<--end-insert> I<STRING>

Use STRING as the "end insert" string. This string will be output
following every sequence of inserted text, to mark where it ends. By
default, the end insert string is B<+}>.

=back

=head1 DIFF OPTIONS

If any of the following options is given, adiff enters a different
mode where it provides the shortest diff output possible while
preserving structure.  The diff output does not contain the start or
end markers listed above.

=over

=item B<-c>

Use the context output format.

=item B<-C> I<NUM> B<--context>[=I<NUM>]

Use the context output format, showing lines (an integer) lines of
context, or three if lines is not given.

=item B<-u>

Use the unified output format.

=item B<-U> I<NUM> B<--unified>[=I<NUM>]

Use the unified output format, showing lines (an integer) lines of
context, or three if lines is not given.

=item B<--normal>

Use the normal diff output, instead of the default wdiff-style output.

=back

=head1 REGEX OPTIONS

These options make it possible to use a different field separator than the
default whitespace.

=over

=item B<-r> I<RE> B<--regex> I<RE>

Override the default field separator regular expression (see B<--help> for the
default).  This can produce some very.. interesting.. results.

=item B<-b --word-boundaries>

Instead of breaking on whitespace, break on any word-boundary, as well as
breaking on punctuation.  For the sake of C literal strings split over lines,
"\s+" is considered whitespace.  See B<--help> for the equivalent B<--regex>
argument.

=back

=head1 OTHER OPTIONS

=over

=item B<-h --help>

Show help information.

=item B<-V --version>

Show version information.

=back
