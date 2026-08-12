---
id: contracts
title: Contracts and structure
order: 20
---

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

The check is done when every symbol the diff adds, changes or removes that is
visible outside its own file has had its callers grepped once. A change that
nothing outside the file can see has no contract to break: say so and submit.

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

The check is done once the changed code has been compared against its immediate
neighbours — the files beside it and the layer it belongs to. If it does what
they do, say so and submit. Structural opinions have no natural end, so stop
when that comparison is made rather than when you run out of remarks.
