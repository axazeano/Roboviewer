"""Asking the model to write in a language other than the prompts'.

Assembly over a config value rather than a placeholder in the templates: a
custom prompt set gets the option for free, and a checklist set that brings
its own system prompt keeps it too. It lands twice — in the system prompt, and
restated as the last line of the task, because a small model drifts back to the
language of the code it has been reading and the final line is what survives
that drift.
"""

from __future__ import annotations

# Appended to the system prompt when a language is configured.
LANGUAGE_DIRECTIVE = """

# Output language

Write every text field you submit in {language}: titles, rationales,
suggestions, reasons and the summary.

Code, identifiers, file paths and quoted snippets stay exactly as they appear in
the source — do not translate those.
"""

# The same instruction again, last thing in the task.
LANGUAGE_REMINDER = "\n\nWrite the text you submit in {language}."

# ISO-639-1 codes people actually type on a command line. Anything missing here
# goes into the prompt as written, so "Bahasa Indonesia" works just as well.
_LANGUAGE_NAMES = {
    "ar": "Arabic", "de": "German", "en": "English", "es": "Spanish",
    "fr": "French", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ru": "Russian", "tr": "Turkish", "uk": "Ukrainian", "zh": "Chinese",
}


def language_name(value: str) -> str:
    """`ru` → `Russian`. Anything unknown passes through as written."""
    return _LANGUAGE_NAMES.get(value.strip().lower(), value.strip())


def with_directive(text: str, language: str) -> str:
    """A system prompt, with the directive appended when a language is set."""
    if not language:
        return text
    return text + LANGUAGE_DIRECTIVE.format(language=language)


def with_reminder(text: str, language: str) -> str:
    """A task, with the reminder as its last line when a language is set."""
    if not language:
        return text
    return text + LANGUAGE_REMINDER.format(language=language)
