# Output language

Prompts and checklists are English, so findings come back in English. To get
them in another language, ask for it rather than translating the prompts:

```bash
roboviewer develop --language ru
```

Or once, in the config:

```toml
[run]
output_language = "ru"
```

It takes an ISO code or a name — `ru`, `Russian`, `German`. Anything not in the
built-in map goes into the prompt as written, so `Bahasa Indonesia` works too.

This covers the text the model writes: finding titles, rationales, suggestions,
verdict reasons and the judge's summary. Report headings, tables and severity
labels come from the templates and stay English — change those in
`roboviewer/templates/default/`.

The directive is appended by the code, so it survives a custom prompt set and a
checklist that brings its own `_system.md`.
