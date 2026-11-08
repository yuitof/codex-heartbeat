#!/usr/bin/perl
# macOS includes Perl and its core Fcntl, POSIX, and JSON::PP modules.
use strict;
use warnings;
use Fcntl qw(:flock F_GETFD F_SETFD);
use POSIX qw(WNOHANG setpgid);
use JSON::PP qw(decode_json);

my $mode = shift @ARGV // '';
if ($mode eq 'lock') {
    open(my $lock, '>&=9') or die "Missing lock descriptor: $!\n";
    my $flags = LOCK_EX | (($ARGV[1] // '') eq 'wait' ? 0 : LOCK_NB);
    flock($lock, $flags) or exit($!{EWOULDBLOCK} ? 0 : 1);
    # Explicitly retain the lock through exec and all child processes.
    fcntl($lock, F_SETFD, 0) or die "fcntl: $!\n";
    exec '/bin/bash', $ARGV[0], '--locked';
    die "exec: $!\n";
}
if ($mode eq 'run') {
    my $timeout = shift @ARGV;
    die "Invalid timeout\n" unless $timeout =~ /^[1-9][0-9]{0,3}$/;
    my $pid = fork();
    die "fork: $!\n" unless defined $pid;
    if (!$pid) {
        setpgid(0, 0) == 0 or die "setpgid: $!\n";
        exec { $ARGV[0] } @ARGV;
        die "exec: $!\n";
    }
    # Child also sets its group, closing the parent/child scheduling race.
    setpgid($pid, $pid);
    my $stop = sub {
        kill 'TERM', -$pid;
        select undef, undef, undef, 0.2;
        kill 'KILL', -$pid;
        waitpid($pid, 0);
    };
    $SIG{TERM} = sub { $stop->(); exit 143 };
    $SIG{INT} = sub { $stop->(); exit 130 };
    my $deadline = time + $timeout;
    while (1) {
        my $done = waitpid($pid, WNOHANG);
        if ($done == $pid) {
            my $status = $?;
            exit(($status & 127) ? 128 + ($status & 127) : $status >> 8);
        }
        die "waitpid: $!\n" if $done == -1;
        if (time >= $deadline) {
            $stop->();
            print STDERR "heartbeat request timed out\n";
            exit 124;
        }
        select undef, undef, undef, 0.1;
    }
}
if ($mode eq 'validate') {
    my ($events, $stderr, $rc) = @ARGV;
    open(my $stream, '<', $events) or die "$events: $!\n";
    my ($completed, $error, $tools, $malformed, $last, $messages) = (0, 0, 0, 0, '', 0);
    my $diagnostics = '';
    while (my $line = <$stream>) {
        my $event = eval { decode_json($line) };
        if ($@ || ref($event) ne 'HASH') { $malformed = 1; next }
        my $type = $event->{type} // '';
        $completed++ if $type eq 'turn.completed';
        if ($type eq 'error' || $type eq 'turn.failed') {
            $error = 1;
            $diagnostics .= $line;
        }
        if ($type =~ /^item\./) {
            my $item = $event->{item};
            if (ref($item) ne 'HASH') { $malformed = 1; next }
            my $kind = $item->{type} // '';
            # Fail closed on new item types; only text and reasoning expected.
            $tools = 1 unless $kind eq 'agent_message' || $kind eq 'reasoning';
            if ($type eq 'item.completed' && $kind eq 'agent_message') {
                $last = $item->{text} // '';
                $last =~ s/^\s+|\s+$//g;
                $messages++;
            }
        }
    }
    close $stream;
    open(my $err, '<', $stderr) or die "$stderr: $!\n";
    { local $/; $diagnostics .= <$err> // '' }
    # Error text is only a diagnostic label. Acceptance always requires a
    # completed turn AND the exact expected assistant message, with no errors.
    my $usage = $diagnostics =~ /usage[_ -]?limit|rate[_ -]?limit|quota|weekly\s+limit|limit\s+(?:has\s+been\s+)?(?:reached|exceeded)|too\s+many\s+requests|429/i;
    my $result = 'invalid-response';
    if ($rc == 124) { $result = 'timeout' }
    elsif ($usage) { $result = 'usage-limit' }
    elsif ($tools) { $result = 'unexpected-tool' }
    elsif ($malformed) { $result = 'malformed-events' }
    elsif (($error || $rc != 0) && $diagnostics =~ /network|connection|timed?\s*out|dns|resolve\s+host|stream\s+disconnect/i) { $result = 'network-error' }
    elsif ($error || $rc != 0) { $result = 'request-error' }
    elsif ($completed == 1 && $messages == 1 && $last eq 'OK') { $result = 'success' }
    print "$result\n";
    exit 0;
}
if ($mode eq 'plist') {
    local $/;
    my $template = <>;
    if ($template =~ /__CALENDAR__/) {
        my $when = $ENV{HEARTBEAT_WHEN} // '';
        die "Invalid scheduled timestamp\n" unless $when =~ /^[1-9][0-9]{0,10}$/;
        my @tm = localtime($when);
        my $calendar = '<dict>';
        my @fields = (Month => $tm[4] + 1, Day => $tm[3], Hour => $tm[2], Minute => $tm[1]);
        while (@fields) {
            my ($key, $value) = splice @fields, 0, 2;
            $calendar .= "<key>$key</key><integer>$value</integer>";
        }
        $calendar .= '</dict>';
        $template =~ s/__CALENDAR__/$calendar/;
        my $run = ($ENV{HEARTBEAT_IMMEDIATE} // '') eq '1' ? '<true/>' : '<false/>';
        $template =~ s/__RUN_AT_LOAD__/$run/;
    }
    $template =~ s/__(INSTALL|STATE|LOGS|EXEC_PATH|AUTH_HOME|AGENTS|LABEL)__/
        my $value = $ENV{"HEARTBEAT_$1"} \/\/ die "Missing HEARTBEAT_$1\n";
        $value =~ s{&}{&amp;}g;
        $value =~ s{<}{&lt;}g;
        $value =~ s{>}{&gt;}g;
        $value =~ s{"}{&quot;}g;
        $value =~ s{'}{&apos;}g;
        $value;
    /gex;
    print $template;
    exit 0;
}
die "Unknown helper mode: $mode\n";
