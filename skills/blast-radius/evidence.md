How sure are you? For each fact a claim depends on, this ladder ranks the evidence. State the rung you reached.

1. **You said so.** No evidence.
2. **You pointed at the line.** A real `file:line`, or the library's own source at its pinned version.
3. **You showed the bad case cannot happen.** You walked the failure path step by step and it does not reach.
4. **You ran it.** A script or test that calls the real code and fails when you are wrong. Usually one small script that imports the library the app ships with and calls the exact function.
5. **You reproduced it in the running app.**

A finding against code you did not write that cannot reach rung 2 is unverified.
