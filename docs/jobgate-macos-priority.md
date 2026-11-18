# Why jobgate runs macOS commands at utility QoS

On macOS, jobgate starts each command under `taskpolicy -c utility`, which sets
a quality-of-service (QoS) clamp. The clamp puts the command and every process
it starts in the utility QoS class for the whole run. The
[jobgate reference](jobgate.md#enforcement-and-priority) describes the behavior.
This page gives the measurements behind the choice.

A gated run on a Mac competes for the CPU with the user and with the user's
agents. It should finish as fast as a normal run when the Mac is idle, and yield
when the user needs the CPU. This page compares three CPU priorities: normal
priority, the utility QoS class, and background priority. Background priority is
`PRIO_DARWIN_BG`, the state that `taskpolicy -b` sets.

The measurements below come from an M4 Pro Mac with 14 cores, 10 performance and
4 efficiency. A test run used 14 CPU-bound Python workers, each running a busy
loop of 20 million iterations. A foreground load of 10 workers at normal
priority, the default QoS, represented the user's own work. The last two rows
show a busy Mac.

| Run | Time |
| --- | --- |
| Test run alone, normal priority | 0.91 s |
| Test run alone, utility QoS | 0.88 s |
| Test run alone, background priority | 16.65 s |
| Foreground load alone | 0.71 s |
| Foreground load next to a utility test run | 0.82 s, and the test run took 1.58 s |
| Foreground load next to a background test run | 0.71 s, and the test run took 17.24 s |

The table came from a separate measurement. This script repeats the same
comparison on any Mac. Its absolute times depend on its loop size and on the
Mac, so they differ from the table:

```sh
cat > burn.py <<'PY'
import sys, time
from multiprocessing import Pool

def burn(_):
    total = 0
    for i in range(3000000):
        total += i
    return total

if __name__ == "__main__":
    workers = int(sys.argv[1])
    start = time.time()
    with Pool(workers) as pool:
        pool.map(burn, range(workers))
    print("{} workers: {:.2f} s".format(workers, time.time() - start))
PY
python3 burn.py 14
taskpolicy -c utility python3 burn.py 14
taskpolicy -b python3 burn.py 14
python3 burn.py 10
python3 burn.py 10 & taskpolicy -c utility python3 burn.py 14; wait
python3 burn.py 10 & taskpolicy -b python3 burn.py 14; wait
```

Utility QoS costs nothing on an idle Mac. The test run took 0.88 s against 0.91
s at normal priority. On a busy Mac, a utility test run yields but keeps going.
Next to the foreground load, it took 1.58 s, and the foreground load took 0.82 s
against 0.71 s alone. Under `taskpolicy -b`, background priority kept the
foreground load at full speed. But the test run took 16.65 s alone, about 18
times slower even on an idle Mac, and 17.24 s next to the load. The
`setpriority(2)` man page says that background priority also throttles disk I/O.
It throttles network I/O too, on sockets that the process opens at background
priority.

Switching a test run to background priority while the user works would make the
foreground load 0.11 s faster, 0.71 s instead of 0.82 s. It would cost the test
run 15.7 s, 17.24 s instead of 1.58 s. jobgate uses utility QoS, which keeps
both the foreground load and the test run fast. jobgate also cannot set or lift
a clamp on a running process. `taskpolicy -p` accepts only its `-b`, `-B`, `-t`,
and `-l` options.
