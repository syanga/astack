# Python standard-library TOML reader

The four Python files are unmodified copies of CPython **v3.11.9**:
https://github.com/python/cpython/tree/v3.11.9/Lib/tomllib

Bundled so settings management works on Python 3.10 without installing packages.
The lossless editor uses this pinned reader's key/value parsing functions to
locate source spans; do not replace them independently without running settings
tests. Parsing never executes configuration content.

`LICENSE` is CPython's license; `LICENSE-MIT` supplies the MIT terms indicated by
the source headers, copyright 2021 Taneli Hukkinen. No upstream runtime is used.
