# Why jobgate runs macOS commands at utility QoS

On macOS, jobgate starts each command under `taskpolicy -c utility`. The clamp
puts the command and every process it starts in the utility quality-of-service
class for the whole run. The [jobgate reference](jobgate.md#enforcement-and-priority)
describes the behavior. This page gives the measurements behind the choice.

A gated run on a Mac shares the CPU with the person at the keyboard and with
their agents. It should finish as fast as a normal run when the Mac is free,
and it should give way when someone needs the CPU. macOS offers three
candidates for that: normal priority, the utility QoS class, and background
priority (`PRIO_DARWIN_BG`, the state that `taskpolicy -b` sets).

The measurements below come from an M4 Pro Mac with 14 cores, 10 performance
and 4 efficiency. A test run used 14 CPU-bound Python workers. A foreground
load of 10 workers at default QoS stood in for the user's own work.

| Run | Time |
| --- | --- |
| Test run alone, normal priority | 0.91 s |
| Test run alone, utility QoS | 0.88 s |
| Test run alone, background priority | 16.65 s |
| Foreground load alone | 0.71 s |
| Foreground load next to a utility test run | 0.82 s, and the test run took 1.58 s |
| Foreground load next to a background test run | 0.71 s, and the test run took 17.24 s |

Utility costs nothing on a free Mac: the test run took 0.88 s against 0.91 s at
normal priority. Under load, it slowed the foreground work by 0.11 s and still
finished its own work in 1.58 s. Background priority kept the foreground work
at full speed, but it slowed the test run about 18 times even on a free Mac.
`setpriority(2)` adds that background state also throttles disk I/O and the
network I/O of sockets opened after the switch.

Switching a run between background and normal priority by keyboard and mouse
idle time would add nothing on top of utility. Utility already runs at full
speed when the Mac is free and yields when the Mac is busy. A clamp also cannot
change on a running process, because `taskpolicy -p` accepts only its `-b`,
`-B`, `-t`, and `-l` options.
