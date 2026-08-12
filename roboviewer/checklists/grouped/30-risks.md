---
id: risks
title: Risks and coverage
order: 30
---

## Security and privacy

Check the changes for security and user-data problems.

What to look at:
- Secrets in the code: tokens, keys, passwords, private URLs.
- Personal data, tokens or request bodies in logs or crash reports.
- Input reaching a query, a file path or an interpreter without validation or escaping.
- Sensitive data in unprotected storage instead of the keychain or an encrypted file.
- A weakened check: certificate validation turned off, signature verification skipped, authorisation bypassed.
- Permission checks done on the client side only.
- Data sent to a third party with no clear reason.

Do not inflate severity: `blocker` is only for a real, exploitable vector, not a
potential one under the right circumstances.

The check is done when every value the change takes from outside — user input, a
request, a file, the environment — has been followed to where it is used. If the
change reads nothing from outside and touches no secret, credential or
permission check, say so and submit. An aspect with nothing to find is not a
reason to report what belongs to another one.

## Performance and resources

Check the changes for obvious performance problems.

What to look at:
- A query or an expensive computation inside a loop where one call would do.
- An algorithm quadratic in collection size, on data that can grow.
- Synchronous I/O, decoding or parsing on the main thread.
- Work on a hot path: in cell rendering, in a scroll handler, in the body of a frequently called callback.
- Needless copies of large collections, expensive objects recreated on every call.
- A leaked resource: an unclosed file, connection, timer or observer.
- A cache that grows without a bound.

Only write a finding where the scale of the problem is visible: a loop over
three elements does not need optimising. If the data size is not obvious, use
`grep` to see where the data comes from.

The check is done when every loop, allocation and repeated call the diff
introduces has been sized once against the data that reaches it. If the change
adds no repeated work and no allocation on anything that can grow, say so and
submit.

## Tests

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

The check is done when each name the diff introduces or changes has been grepped
across the test directories once. That grep is the whole check. One finding
covers what the change leaves untested — do not report the same gap again per
function, per branch or per file, and do not go looking for further variations
of it once the grep has been made.
