# Evidence ladder

How sure are you? For each fact a claim depends on, get it as far down this ladder as is cheap, and say where it stopped.

1. **You said so.** Worthless on its own.
2. **You pointed at the line.** A real `file:line`, or the library's own source at its pinned version.
3. **You walked the failure path.** Step by step, the bad case does or does not reach.
4. **You ran it.** A script or test that calls the real code and fails loudly if you are wrong. Usually one small script importing the same library the app ships and calling the exact function in question.
5. **You reproduced it in the running app.**

A safety fact that cannot reach rung 4 is unproven. Say so instead of writing it up as settled. A finding against someone else's code that cannot reach rung 2 is unverified and goes last.
