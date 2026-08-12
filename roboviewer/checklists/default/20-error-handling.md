---
id: error-handling
title: Error handling
order: 20
---
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
