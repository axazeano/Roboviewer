---
id: security
title: Security and privacy
order: 50
---
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
