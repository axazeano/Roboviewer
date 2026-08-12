---
id: concurrency
title: Concurrency and lifecycle
order: 30
---
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
it does apply, the check is done when every piece of state the diff touches has
been traced to the contexts that reach it.
