---
id: api-contracts
title: Public contracts and backward compatibility
order: 40
---
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
