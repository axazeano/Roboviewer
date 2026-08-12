---
id: architecture
title: Architecture and code structure
order: 80
---
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
