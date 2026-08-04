"""Reference resolution: what the diff introduces, and whether it exists.

Measured on a real branch, ten of eleven missed blockers were one question —
does this reference resolve? A symbol with no declaration, a storyboard scene
naming a file that was never added, a localization key with no entry, an outlet
no nib connects. All of it is decidable by searching the tree, so none of it
belongs to an agent: `git grep` is faster, cheaper and does not hallucinate.

The result goes into the context block every agent shares, which keeps the
prompt prefix identical across the fan-out and therefore cacheable.

Two mechanisms, deliberately separate because their precision differs:

`unresolved_symbols` is a census. An identifier introduced on an added line, in
a position that has to resolve, occurring nowhere outside the files this diff
touched, with no declaration anywhere. It cannot see the SDK, so framework
symbols land in it — the rendering says so, and the agent triages. Treat it as
a lead.

`resource_misses` are facts. A named file is absent from the tree, a literal is
absent from every file that could define it. Nothing is inferred.

Resource rules are stack-specific and live in RESOURCE_RULES below; the census
is not. The repository's excluded globs do not apply here — a 539 KB pbxproj is
the wrong thing to show a model and the right thing to search.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Identifiers that carry no information, plus the declaration keywords themselves.
# A block rather than a list literal: the point is that a reader can see it.
_KEYWORD_BLOCK = """
func var let if else guard return self super class struct enum protocol extension
import public private internal fileprivate open static final override init deinit
weak unowned lazy async await throws try catch throw defer where case switch for
while repeat break continue nil true false some any inout typealias associatedtype
subscript operator convenience required dynamic mutating available objc IBOutlet
IBAction escaping autoclosure discardableResult main didSet willSet get set
def function interface type const class_ None True False self_ import_ return_
"""

KEYWORDS = frozenset(_KEYWORD_BLOCK.split())

# Files whose identifiers are machine-generated noise — object ids, UUIDs. They
# are searched, never mined for candidates.
GENERATED_SUFFIX = frozenset({
    ".pbxproj", ".storyboard", ".xib", ".strings", ".stringsdict", ".xcstrings",
    ".plist", ".json", ".lock", ".xcscheme", ".xcworkspacedata", ".md", ".txt",
})

# A reference in a position that must resolve: any `.member`, and a capitalised
# name being called, chained or specialised. Prose in comments and words inside
# string literals do not match, which is most of what a naive identifier scan
# picks up. Member access is deliberately not narrowed to calls — an assignment
# target like `viewer.albumPhoto = photo` has to resolve just as much, and
# requiring a trailing `(` loses exactly that case.
MUST_RESOLVE = re.compile(r"\.([a-z_]\w{2,})|\b([A-Z]\w{2,})\s*[.(<]")

_DECL_KEYWORDS = (
    "func|var|let|class|struct|enum|protocol|extension|typealias|"
    "associatedtype|case|def|function|interface|type"
)
# POSIX ERE — `git grep -E` has no \s and no \b, and writing them silently
# matches nothing.
_NON_WORD = "[^A-Za-z0-9_]"

MAX_SYMBOLS = 60


@dataclass(frozen=True)
class ResourceRule:
    """One decidable question about a reference the diff introduces.

    `pattern` pulls the reference out of an added line; group 1 is the value.
    Either the value names a file that must exist (`expect_file`), or it is a
    literal that must appear in one of `expect_in`.
    """

    name: str
    pattern: re.Pattern[str]
    question: str
    expect_file: str = ""        # a glob with {} for the captured value
    expect_in: tuple[str, ...] = ()   # globs of files that could define it
    # Suffixes to mine for references. Empty means "source files only", which is
    # the safe default — generated files are full of machine ids. A rule opts in
    # explicitly when its reference legitimately lives in a resource file.
    sources: tuple[str, ...] = ()


# Stack-specific by design. A project on another stack replaces this table;
# the census above works everywhere.
RESOURCE_RULES: tuple[ResourceRule, ...] = (
    ResourceRule(
        name="storyboard",
        pattern=re.compile(r'(?:storyboardName|UIStoryboard\(name)[:=]\s*"([^"]+)"'),
        question="storyboard named here but no such file in the tree",
        expect_file="*{}.storyboard",
        # A storyboard scene pointing at another storyboard is the common case,
        # so this rule reads them even though nothing else does.
        sources=(".storyboard", ".xib"),
    ),
    ResourceRule(
        name="localization",
        pattern=re.compile(r'NSLocalizedString\(\s*"([^"]+)"'),
        question="localization key used but defined in no strings file",
        expect_in=("*.strings", "*.stringsdict", "*.xcstrings"),
    ),
    ResourceRule(
        name="outlet",
        pattern=re.compile(r"@IBOutlet[^\n]*?\bvar\s+(\w+)\s*:"),
        question="outlet declared but connected in no storyboard or nib",
        expect_in=("*.storyboard", "*.xib"),
    ),
)


# Also stack-specific: a source file that no build manifest mentions is never
# compiled, which reads as "the symbol does not exist" everywhere it is used.
BUILD_MANIFESTS: tuple[str, ...] = ("*.pbxproj", "*/CMakeLists.txt", "*.vcxproj")
COMPILED_SUFFIX: frozenset[str] = frozenset({".swift", ".m", ".mm", ".c", ".cc", ".cpp"})


@dataclass
class ReferenceReport:
    # identifier -> files that introduced it
    unresolved_symbols: dict[str, list[str]] = field(default_factory=dict)
    # (rule name, question, value, file that introduced it)
    resource_misses: list[tuple[str, str, str, str]] = field(default_factory=list)
    symbols_truncated: int = 0
    # Set when the pass could not run; the context block says so rather than
    # rendering an empty result that reads like a clean bill of health.
    error: str = ""

    @property
    def empty(self) -> bool:
        return not self.unresolved_symbols and not self.resource_misses


def check(root: Path, base: str, head: str) -> ReferenceReport:
    """Everything the diff introduces that resolves to nothing."""
    try:
        added = _added_lines(root, base, head)
    except (subprocess.SubprocessError, OSError) as exc:
        return ReferenceReport(error=str(exc))

    if not added:
        return ReferenceReport()

    report = ReferenceReport()
    try:
        report.unresolved_symbols, report.symbols_truncated = _unresolved_symbols(
            root, head, added
        )
        report.resource_misses = _resource_misses(root, head, added)
        report.resource_misses += _unregistered_sources(root, base, head)
    except (subprocess.SubprocessError, OSError) as exc:
        return ReferenceReport(error=str(exc))
    return report


def _unregistered_sources(root: Path, base: str, head: str) -> list[tuple[str, str, str, str]]:
    """Files this branch adds that no build manifest mentions.

    Only added files: a file that already built before this branch is fine, and
    a renamed one shows up as an addition anyway.
    """
    manifests = [
        entry for entry in _tree(root, head)
        if any(Path(entry).match(glob) for glob in BUILD_MANIFESTS)
    ]
    if not manifests:
        return []

    out = _git(root, ["diff", "--diff-filter=A", "--name-only", f"{base}...{head}"])
    misses: list[tuple[str, str, str, str]] = []
    for path in out.splitlines():
        if Path(path).suffix not in COMPILED_SUFFIX:
            continue
        name = Path(path).name
        if _git(root, ["grep", "-l", "-F", "-e", name, head, "--", *manifests]).strip():
            continue
        misses.append(
            ("build-membership", "file added but no build manifest mentions it", name, path)
        )
    return misses


# --------------------------------------------------------------------------- diff


def _added_lines(root: Path, base: str, head: str) -> dict[str, list[str]]:
    """path -> lines this diff adds to it. Deliberately not `changed_files`:
    that one honours exclude_globs, and the resource files it drops are exactly
    what has to be searched here."""
    out = _git(root, ["diff", "--unified=0", f"{base}...{head}"])
    added: dict[str, list[str]] = {}
    current = ""
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            added.setdefault(current, []).append(line[1:])
    return added


# ------------------------------------------------------------------ the census


def _unresolved_symbols(
    root: Path, head: str, added: dict[str, list[str]]
) -> tuple[dict[str, list[str]], int]:
    touched = set(added)
    candidates: dict[str, list[str]] = {}
    for path, lines in added.items():
        if Path(path).suffix in GENERATED_SUFFIX:
            continue
        for line in lines:
            for member, typename in MUST_RESOLVE.findall(line):
                name = member or typename
                if name and name not in KEYWORDS:
                    where = candidates.setdefault(name, [])
                    if path not in where:
                        where.append(path)

    if not candidates:
        return {}, 0

    names = sorted(candidates)
    occurrences = _occurrences(root, head, names)
    # Occurs nowhere this diff did not touch → nothing outside can define it
    homeless = [n for n in names if not (occurrences.get(n, set()) - touched)]
    if not homeless:
        return {}, 0

    declared = _declared_anywhere(root, head, homeless)
    unresolved = [n for n in homeless if n not in declared]

    truncated = max(0, len(unresolved) - MAX_SYMBOLS)
    return {n: candidates[n] for n in unresolved[:MAX_SYMBOLS]}, truncated


def _occurrences(root: Path, head: str, names: list[str]) -> dict[str, set[str]]:
    """identifier -> every file it appears in, anywhere in the tree."""
    found: dict[str, set[str]] = {}
    wanted = set(names)
    for batch in _batched(names, 150):
        args = ["grep", "-o", "-w", "-F", "-I"]
        for name in batch:
            args += ["-e", name]
        for line in _git(root, [*args, head]).splitlines():
            path, match = _split_grep_line(line, head)
            if match in wanted:
                found.setdefault(match, set()).add(path)
    return found


def _declared_anywhere(root: Path, head: str, names: list[str]) -> set[str]:
    """Which of these have something that looks like a declaration somewhere.

    A declaration means the name resolves after all — a new type this branch
    introduces, not a reference into thin air.
    """
    declared: set[str] = set()
    for batch in _batched(names, 80):
        args = ["grep", "-o", "-h", "-E", "-I"]
        for name in batch:
            args += ["-e", f"({_DECL_KEYWORDS}){_NON_WORD}+{re.escape(name)}({_NON_WORD}|$)"]
        text = _git(root, [*args, head])
        if not text:
            continue
        for name in batch:
            if re.search(rf"(?:{_DECL_KEYWORDS})\W+{re.escape(name)}(?:\W|$)", text):
                declared.add(name)
    return declared


# ---------------------------------------------------------------- resource rules


def _resource_misses(
    root: Path, head: str, added: dict[str, list[str]]
) -> list[tuple[str, str, str, str]]:
    misses: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    tree = _tree(root, head)

    for rule in RESOURCE_RULES:
        for path, lines in added.items():
            suffix = Path(path).suffix
            if suffix in GENERATED_SUFFIX and suffix not in rule.sources:
                continue
            for line in lines:
                for value in rule.pattern.findall(line):
                    key = (rule.name, value)
                    if key in seen:
                        continue
                    seen.add(key)
                    if _resolves(root, head, rule, value, tree):
                        continue
                    misses.append((rule.name, rule.question, value, path))
    return misses


def _resolves(root: Path, head: str, rule: ResourceRule, value: str, tree: list[str]) -> bool:
    if rule.expect_file:
        needle = rule.expect_file.format(value).lstrip("*")
        return any(entry.endswith(needle) for entry in tree)
    # A literal that some file of the right kind must contain
    args = ["grep", "-l", "-F", "-e", value, head, "--"]
    args += list(rule.expect_in)
    return bool(_git(root, args).strip())


# --------------------------------------------------------------------------- git


def _git(root: Path, args: list[str]) -> str:
    """git grep exits 1 on "no matches", which is an answer, not a failure."""
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=120
    )
    if proc.returncode not in (0, 1):
        raise subprocess.SubprocessError(proc.stderr.strip()[:300] or f"git {args[0]} failed")
    return proc.stdout


def _tree(root: Path, head: str) -> list[str]:
    return _git(root, ["ls-tree", "-r", "--name-only", head]).splitlines()


def _split_grep_line(line: str, ref: str) -> tuple[str, str]:
    rest = line[len(ref) + 1 :] if line.startswith(f"{ref}:") else line
    path, _, match = rest.rpartition(":")
    return path, match


def _batched(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
