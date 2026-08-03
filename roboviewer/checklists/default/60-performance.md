---
id: performance
title: Performance and resources
order: 60
---
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
