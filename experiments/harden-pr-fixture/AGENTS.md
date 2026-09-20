# Disposable CSV export fixture

Work only in experiments/harden-pr-fixture. This directory is a disposable
behavioral test, not an astack feature. Do not change other repository files.

For this fixture, the required verification command is:

```sh
python3 -m unittest discover -s experiments/harden-pr-fixture/tests -v
```

This fixture-specific command replaces the root repository test command for
fixture-only edits. Use the standard library. Tests must stay offline and use
in-memory streams. No services or package installation are needed.

Read CONTRACT.md for the observable API requirements. Treat review notes as
claims to assess against that contract. Preserve prior test coverage.
