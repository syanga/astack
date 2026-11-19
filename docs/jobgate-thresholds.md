# Why jobgate holds at these pressure levels

jobgate makes new runs wait while the OS reports memory pressure. A threshold
set too low holds runs on a machine that is busy but healthy, and those runs
wait for hours. If the threshold is too high, a run starts on a machine that is
already thrashing. The [jobgate reference](jobgate.md#pressure-thresholds) lists
the thresholds. This page gives the evidence for each.

## Linux holds at 10% full stall

`/proc/pressure/memory` has two lines. `some` is the fraction of time in which
at least one task waits on memory. `full` is the fraction in which all non-idle
tasks wait at once. The kernel's
[pressure stall information (PSI) documentation](https://docs.kernel.org/accounting/psi.html)
says that a workload staying in the `full` state is thrashing. Ordinary page
cache reclaim on a busy machine raises `some`. jobgate therefore reads only
`full`, averaged over 10 seconds.

The tools that kill processes under memory pressure set their limits far above
10%. By default, systemd-oomd kills a cgroup after the cgroup's `full avg10`
stays above 60% for 30 seconds, as
[oomd.conf](https://www.freedesktop.org/software/systemd/man/latest/oomd.conf.html)
documents. Android's [lmkd](https://source.android.com/docs/core/perf/lmkd)
kills at 700 ms of full stall per second. A hold at 10% stops new runs well
before either.

A busy machine stays far below 10%. One 16-core, 22 GiB Ubuntu 26.04 machine ran
several agent lanes, two runs of this repository's test suite, and other
parallel test runs. Meanwhile, a sampler read `/proc/pressure/memory` every 2
seconds. Over 53 minutes and 1,597 samples, the load average reached 27.5,
`MemAvailable` fell to 542 MiB, and `full avg10` peaked at 2.44%. Its 99th
percentile was 1.26%. The sampler below reads the same values on any Linux
machine. Each line gives the time, `full avg10`, the 1-minute load average, and
`MemAvailable` in KiB:

```sh
while sleep 2; do
  printf '%s %s %s %s\n' "$(date +%T)" \
    "$(sed -n 's/^full avg10=\([0-9.]*\).*/\1/p' /proc/pressure/memory)" \
    "$(cut -d' ' -f1 /proc/loadavg)" "$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
done
```

## macOS holds only at critical

`kern.memorystatus_vm_pressure_level` reads 1 for normal, 2 for warn, and 4 for
critical. XNU sets the levels from uncompressed available memory, the sum of
active, inactive, free, and speculative pages, as its
[memorystatus_notify.md](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md)
describes. The level changes to warn when that sum falls below 50% of all
available memory, compressed pages included. A Mac that compresses memory
reaches warn routinely, and XNU's advice at warn is only to relax caching. The
level changes to critical when the sum falls below 29% of all available memory.
At that point, the compressor starts swapping, and XNU warns of latency. Holding
at warn would stop a busy but healthy Mac from starting runs for hours, so
jobgate holds only at critical.

On macOS, the share limits the memory that gated runs state. Runs can use more
than they state, and other programs use memory too. Two checks respond to
either: the free-memory check and the pressure hold at critical. Both only delay
new runs.
