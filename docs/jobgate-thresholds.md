# Why jobgate holds at these pressure levels

jobgate makes new runs wait while the OS reports memory pressure. A threshold
set too low holds runs on a machine that is busy but healthy, and those runs
wait for hours. A threshold set too high starts a suite on a machine that is
already thrashing. The [jobgate reference](jobgate.md#pressure-thresholds)
lists the thresholds. This page gives the evidence for each.

## Linux holds at 10% full stall

`/proc/pressure/memory` has two lines. `some` is the share of time in which at
least one task waits on memory. `full` is the share in which all non-idle tasks
wait at once. The [kernel's PSI documentation](https://docs.kernel.org/accounting/psi.html)
says that a workload staying in the `full` state is thrashing. `some` rises
with ordinary page cache reclaim on a busy machine, which is why jobgate reads
only `full`, averaged over 10 seconds.

The tools that kill processes under memory pressure set their limits far above
10%. systemd-oomd kills a cgroup after its `full avg10` stays above 60% for 30
seconds, its defaults in
[oomd.conf](https://www.freedesktop.org/software/systemd/man/latest/oomd.conf.html).
Android's [lmkd](https://source.android.com/docs/core/perf/lmkd) kills at 700 ms
of full stall per second. A hold at 10% stops new runs well before either.

A busy machine stays far below 10%. One 16-core, 22 GiB Ubuntu 26.04 machine
ran several agent lanes, two runs of this repository's test suite, and other
parallel test runs while a sampler read `/proc/pressure/memory` every 2
seconds. Over 53 minutes and 1,597 samples, the load average reached 27.5,
`MemAvailable` fell to 542 MiB, and `full avg10` peaked at 2.44%. Its 99th
percentile was 1.26%. To measure another machine, run this sampler for as long
as a normal working session lasts:

```sh
while sleep 2; do
  printf '%s %s\n' "$(date +%T)" "$(sed -n 's/^full avg10=\([0-9.]*\).*/\1/p' /proc/pressure/memory)"
done
```

## macOS holds only at critical

`kern.memorystatus_vm_pressure_level` reads 1 for normal, 2 for warn, and 4
for critical. XNU's
[memorystatus_notify.md](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
sets the levels from uncompressed available memory, the sum of active,
inactive, free, and speculative pages. The level turns to warn when that sum
falls below half of all available memory, compressed pages included. That is
routine on a Mac that compresses memory, and XNU's advice at warn is only to
relax caching. The level turns critical below 29%, where the compressor starts
swapping and XNU warns of latency. Holding at warn would stop a busy but
healthy Mac from starting runs for hours, so jobgate holds only at critical.
On macOS, the share is the main protection, and the critical hold is the
backstop for memory that gated runs do not account for.
