# Build a useful feedback loop

Choose the cheapest check that observes the exact symptom through real code.
Use the existing tools and test conventions before adding setup.

| Situation | Candidate check |
| --- | --- |
| An existing test boundary reaches the failure | Focused unit, component, or integration test |
| An HTTP path fails | Request against an isolated development server with an assertion on the response |
| A CLI produces wrong output | Fixture input with an independently expected output and exit status |
| Browser interaction triggers the bug | Browser script that checks the DOM, console, or network behavior |
| A particular payload or event sequence fails | Redacted trace replay through the affected path |
| Full startup hides a small failure | Minimal executable setup around the real module |
| Only some inputs fail | Seeded property or fuzz loop that preserves the failing input |
| Two revisions or configurations behave differently | Differential run or automated bisection |
| A human-only step is unavoidable | Written repeatable steps with a precise observation and captured result |

Make the loop fast by narrowing setup and caching only inputs unrelated to the
failure. Make it reliable by controlling time, randomness, files, and network
dependencies where appropriate. Assert the specific wrong output or effect.
A check that merely exits successfully is insufficient for a wrong-result bug.

For intermittent bugs, repeat the trigger and measure the failure rate. Use
controlled stress or scheduling to increase reproduction frequency without
changing the bug being investigated. Record workload, seed, environment, and
trial count so comparisons remain meaningful. Zero failures in a finite run
does not prove an intermittent defect impossible.

The loop is ready when you have run it, observed the user's symptom, and can
name the command or steps another person can repeat. If those conditions remain
unmet, report which one is missing and keep conclusions provisional.
