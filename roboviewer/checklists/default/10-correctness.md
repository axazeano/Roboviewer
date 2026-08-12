---
id: correctness
title: Correctness and logic errors
order: 10
---
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
