# Contributing

Contributions intentionally submitted for inclusion are licensed under
Apache-2.0, unless explicitly stated otherwise. See LICENSE and NOTICE.

Use Python 3.11 or newer. On Windows run `setup.cmd --no-pause`; elsewhere create
a virtual environment and install `requirements-lock.txt`. The web UI uses plain
HTML/CSS/JavaScript and has no frontend build step.

```text
python -m unittest discover -s tests -v
python scripts/release_check.py
```

Tests must use temporary workspaces and independent tokens. Never use personal
workspace databases or publish test credentials. Lifecycle tests must verify the
actual server identity and stop it before deleting its temporary directory;
Windows virtual-environment launcher PIDs can differ from server PIDs.

Keep HTTP, CLI and skill behavior aligned. Treat stored messages as text, preserve
same-origin credential checks, and retain the enrollment recovery guarantees.
For security reports follow SECURITY.md rather than a public issue containing
sensitive reproduction data.
