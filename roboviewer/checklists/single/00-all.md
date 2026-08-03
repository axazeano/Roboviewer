---
id: all
title: Full review
order: 10
---

## Correctness and logic errors

Look for logic errors in the changed code.

What to look at:
- Inverted or incomplete conditions, boundary errors (`<` instead of `<=`, off-by-one).
- Unhandled edge cases: empty collection, nil/None, zero, a negative value, the first and the last element.
- Dereferencing an optional without a check, force-unwrapping, a cast that can fail.
- Changes that break an existing contract: a different call order, changed semantics of a return value, side effects where there were none.
- Copy-paste with a variable left unreplaced — a classic, and easy to miss by eye.
- Early returns that skip code which still needs to run.

Before you write a finding, read the whole function in the attached file: the
check that seems "missing" is often earlier in the function and simply not
marked as changed. If the function is called from another file, use `grep` to
see the arguments it gets.

## Error handling

Check how the changed code deals with errors.

What to look at:
- Swallowed errors: an empty catch, `try?` with no handling, logging instead of reacting.
- An error handled so that the user is left hanging: the spinner never stops, the screen stays empty with no explanation.
- Network and disk operations with no failure or timeout handling.
- Error context lost on rewrapping — `unknown error` reaches the surface.
- Partially changed state: the first operation succeeded, the second failed, nothing was rolled back.
- Error handling added on one path but skipped on the identical one next to it.

Tell a deliberately ignored error (there is a comment, the error really does not
matter) from a forgotten one. The first is not a finding.

## Concurrency and lifecycle

Check concurrency and object ownership.

What to look at:
- Shared mutable state without synchronisation; one variable accessed from different queues or threads.
- UI updated off the main thread.
- Retain cycles in closures: `self` captured strongly where it should be weak; a subscription that holds its owner.
- A subscription or observer that is never cancelled; a resource that is never released.
- Initialisation races: a value read before it is assigned.
- async/await and structured concurrency: dropped tasks, missing cancellation, calls bound to the wrong actor.
- Blocking operations on the main thread.

Verify against the code rather than guessing: use `grep` to find where the
changed method is called from, and in which context that happens.

## Public contracts and backward compatibility

Check changes to public interfaces and their effect on calling code.

What to look at:
- A changed signature, return type or optionality on a public method: `grep` for every caller and check they still agree.
- A new required parameter with no default value.
- The meaning of an existing parameter changing while the signature stays the same — the most dangerous kind, the compiler does not catch it.
- Changes to structures serialised to the network or to disk: renamed and removed fields, incompatibility with data already stored.
- A public symbol removed or renamed while uses of it remain.
- An enum gaining a case, so existing switches stop being exhaustive.

If the type is serialised or crosses an API boundary, judge separately what
happens to old clients and to data that is already stored.

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

## Architecture and code structure

Check whether the change fits how the project is built.

What to look at:
- Broken layering or dependency direction: code reaching straight for something the project normally goes through an intermediate layer to reach.
- A dependency constructed inside a class where the project passes it in from outside.
- Logic in the wrong place: business rules in the UI, networking in a view model.
- Duplication: a function or extension right next to it already does the same thing — check with `grep`.
- A class or function that took on too much and keeps growing in this MR.
- Hardcoded values where the project already has a config or constants.
- Conventions of the surrounding code broken: naming, directory structure, the way a dependency is registered.

Judge by how the surrounding code is built, not by abstract principles: study
neighbouring files with `read_file` and `list_files`. Do not write findings of
the "pattern X could have been applied here" kind.
