"""The tools as the model sees them: descriptions and parameter schemas.

Part of the prompt surface — the descriptions say what a tool is for, not just
what it does, and are read by the model on every turn. The implementations are
`repo.tools`; the names here and in its `dispatch` have to agree, and the
terminal tools below are the ones an agent ends its run by calling.
"""

from __future__ import annotations

from typing import Any


def tool_schemas(base_ref: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file in its new version (at the tip of the reviewed branch), with "
                    "line numbers. Use it for files not attached to the task in full, and for any "
                    "other code in the repository: callers, neighbouring files, tests."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the repository root",
                        },
                        "start_line": {"type": "integer", "description": "First line, 1-based"},
                        "end_line": {"type": "integer", "description": "Last line, inclusive"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search the whole repository on the reviewed branch with an extended regular "
                    "expression. Use it to find callers, tests, duplicates and existing patterns — "
                    "and to confirm something is really missing before you report it as missing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Extended regular expression"},
                        "glob": {
                            "type": "string",
                            "description": "Restrict to a file mask, e.g. *.py or src/**",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": (
                    "List the files and subdirectories of a repository directory on the reviewed "
                    "branch."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Path relative to the repository root",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_show",
                "description": (
                    f"Read a file as it was BEFORE the changes (revision {base_ref[:12]}). "
                    "Use it when you need to compare the old and the new implementation in full."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    ]


SUBMIT_FINDINGS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_findings",
        "description": (
            "Finish the review and return the findings. Call this exactly once, at the end. "
            "An empty findings list is a normal result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "1-3 sentences: what was checked, and the overall conclusion for this "
                        "aspect."
                    ),
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {
                                "type": "string",
                                "description": "File path relative to the repository root",
                            },
                            "line": {
                                "type": "integer",
                                "description": "Line number in the NEW version of the file",
                            },
                            "end_line": {"type": "integer"},
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "major", "minor", "nit"],
                            },
                            "category": {
                                "type": "string",
                                "description": "A short slug, e.g. null-safety, race, api-break",
                            },
                            "title": {"type": "string", "description": "The problem in one line"},
                            "rationale": {
                                "type": "string",
                                "description": (
                                    "Why this is a problem, and the scenario "
                                    "in which it shows up"
                                ),
                            },
                            "suggestion": {
                                "type": "string",
                                "description": "A concrete suggested fix",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence from 0 to 1",
                            },
                        },
                        "required": [
                            "file", "severity", "category", "title", "rationale", "confidence",
                        ],
                    },
                },
            },
            "required": ["summary", "findings"],
        },
    },
}


SUBMIT_VERDICTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdicts",
        "description": "Finish judging and return a verdict for every finding.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Overall assessment of the merge request, 2-5 sentences.",
                },
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "finding_id": {"type": "string"},
                            "verdict": {
                                "type": "string",
                                "enum": ["confirmed", "false_positive", "nitpick", "duplicate"],
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "major", "minor", "nit"],
                                "description": "Corrected severity, when the original one is wrong",
                            },
                            "reason": {
                                "type": "string",
                                "description": "A short justification for the verdict",
                            },
                        },
                        "required": ["finding_id", "verdict", "reason"],
                    },
                },
            },
            "required": ["summary", "verdicts"],
        },
    },
}


SUBMIT_VERDICT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": "Finish judging and return the verdict on the one finding you were given.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["confirmed", "false_positive", "nitpick", "duplicate"],
                },
                "severity": {
                    "type": "string",
                    "enum": ["blocker", "major", "minor", "nit"],
                    "description": "Corrected severity, when the original one is wrong",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "One or two sentences: what you checked and what it showed. "
                        "Name the file and line you verified against."
                    ),
                },
            },
            "required": ["verdict", "reason"],
        },
    },
}
