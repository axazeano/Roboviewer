---
id: tests
title: Tests
order: 70
---
Judge whether the changes are covered by tests, and whether those tests are
meaningful.

What to look at:
- New or changed branching logic with no test. Trivial wrappers and getters do not count.
- A bug fix with no test that would reproduce the bug.
- A test that checks the implementation rather than the behaviour: asserting on internal calls instead of the result.
- A test with no meaningful assertion, or one that would pass on any outcome.
- The very edge cases the code was changed for left uncovered.
- A test that depends on the current time, execution order, the network or shared state — it will flake.
- Existing tests the change breaks: `grep` for tests covering the affected types.

First check whether tests exist somewhere else: `grep` for the name of the
changed type or method across the test directories. A "no tests" finding
without that check is a false positive.
