---
id: contracts
title: Contracts and structure
order: 20
---

## Public contracts and backward compatibility

Check changes to public interfaces and their effect on calling code — the half
of it a build cannot see. Do not grep callers to confirm they still match a
changed signature: that disagreement belongs to the compiler, and walking to it
spends turns on a finding you are not allowed to make.

What to look at:
- The meaning of an existing parameter changing while the signature stays the same — the most dangerous kind, and invisible to a build.
- A default value or an overload that changes which implementation an unchanged call site now picks.
- Changes to structures serialised to the network or to disk: renamed and removed fields, incompatibility with data already stored.
- A name that is spelled rather than referenced — a selector, a key path, a string looked up at run time — pointing at something this change renamed or removed.
- Callers no build here compiles: another module that is not rebuilt with this one, a plugin, an API somebody else builds against.

If the type is serialised or crosses an API boundary, judge separately what
happens to old clients and to data that is already stored.

The check is done once every changed contract that outlives a build — data on
disk or on the wire, a name resolved at run time, a caller outside this
repository — has been looked at once. A change whose only callers are compiled
right here has no contract to break in this report: the build speaks for them.
Say so and submit.

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
