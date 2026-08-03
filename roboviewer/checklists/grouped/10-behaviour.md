---
id: behaviour
title: Runtime behaviour
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
