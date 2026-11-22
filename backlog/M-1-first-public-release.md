---
id: M-1
type: Milestone
title: First public release (v0.1.0)
status: in-arbeit
tags: [release, repo]
created: 2026-11-22
---

# M-1 - First public release

kassist has been running against one real account; this milestone makes the repository public.

**Done when:**

- the repository is public under AGPL-3.0-or-later with a fresh history that carries no personal
  data (ADR-7),
- English and German docs, contributing guide, security policy, code of conduct and third-party
  notices are in place,
- CI (tests on the Python matrix, hygiene, vulnerability gate) is green and protects `main`,
- `v0.1.0` is tagged, the release carries wheel, sdist and `SHA256SUMS`, and the wheel was installed
  into a fresh venv and started (`kassist --help`).

The tasks for field-testing open points (T-2, T-3) belong to M-2.
