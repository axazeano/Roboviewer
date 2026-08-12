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

The check is done when every condition, loop and boundary the diff introduces or
alters has been traced once through the function that holds it. A change that
alters no condition — a rename, moved code, a new constant — has no logic to
check: say so and submit.

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

The check is done when every call in the diff that can fail has been followed to
whatever handles it, or shown to have nothing. If the diff performs no fallible
operation — no I/O, no parsing, no call that reports an error — say so and
submit.

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

Start by finding out whether this program is concurrent at all: `grep` the
changed files, and the code around them, for whatever this language uses —
threads, queues, locks, async entry points, callbacks that run elsewhere. If
none of it is anywhere near the change, the aspect does not apply here. Say that
and submit; do not spend the remaining turns looking for something to say. Where
it does apply, the check is done when every piece of state the diff touches has been
traced to the contexts that reach it.
