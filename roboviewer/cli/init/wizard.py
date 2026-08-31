"""The interview itself: what is asked, in what order, and what it writes.

Four groups, and every one of them can be walked through with Enter. The
gateway is asked about first because it is the only half without a working
default — everything after it narrows a setup that would already run.

The answers are set into the annotated examples rather than collected into a
file of their own, so what lands in `~/.config/roboviewer/` is the documented
text with the answers in it: the file stays the thing you reread six months
later. `config.example` does the setting; this decides what to set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...config import Example, home_config_path, load_config, provider_config_path
from ...reports import renders
from .. import exit_codes
from .questions import Option, Questions

# What each report format is for. Keyed off `renders.known()`, so a format
# added there is offered here whether or not anybody remembers this line.
FORMATS: dict[str, str] = {
    "md": "the report for humans",
    "html": "the same as one self-contained file",
    "sarif": "GitHub Code Scanning, VS Code, Sonar",
    "codequality": "the GitLab merge-request widget",
}

# How the key is passed, as the header and scheme it becomes. The three shapes
# `check-provider` recommends when a valid key still comes back 401.
AUTH: dict[str, tuple[str, str]] = {
    "bearer": ("Authorization", "Bearer"),
    "token": ("Authorization", "Token"),
    "api-key": ("api-key", ""),
    "x-api-key": ("X-Api-Key", ""),
}


@dataclass
class Written:
    """One file the wizard was asked to write, and whether it did."""

    path: Path
    saved: bool


class Wizard:
    """The interview, from the welcome to the offer to probe the gateway."""

    def __init__(self, questions: Questions) -> None:
        self._q = questions
        # Set when the key is going into the file itself, which decides its mode
        self._key_in_file = False

    def run(self) -> int:
        provider, config = Example.load("provider"), Example.load("config")
        self._welcome()
        reachable = self._gateway(provider)
        self._model(config)
        self._run(config)
        written = self._write(provider, config)
        return self._closing(written, reachable=reachable)

    def _welcome(self) -> None:
        self._q.say("Setting up roboviewer: the gateway it talks to, and what it asks")
        self._q.say("of a model. Two files get written, both of them yours to edit after:")
        self._q.say(f"  {provider_config_path()}")
        self._q.say(f"  {home_config_path()}")
        self._q.say()
        self._q.say("Enter takes the value in brackets. Ctrl-C stops and writes nothing.")

    def _gateway(self, provider: Example) -> bool:
        """The endpoint, how the key is passed, and where the key lives.

        Returns whether a key can actually be found — the answer decides whether
        there is any point offering to probe at the end.
        """
        self._q.heading("The gateway — an OpenAI-compatible endpoint")
        provider.set("base_url", self._base_url())
        header, scheme = AUTH[
            self._q.choice(
                "How does it want the key?",
                [
                    Option("bearer", "Authorization: Bearer <key>", "OpenAI, and most gateways"),
                    Option("token", "Authorization: Token <key>", ""),
                    Option("api-key", "api-key: <key>", "the Azure shape"),
                    Option("x-api-key", "X-Api-Key: <key>", ""),
                ],
            )
        ]
        provider.set("auth_header", header)
        provider.set("auth_scheme", scheme)
        return self._key(provider)

    def _base_url(self) -> str:
        url = self._q.text("Address", "https://api.openai.com/v1", required=True)
        if not url.rstrip("/").endswith("/v1"):
            # The single most common way a correct key still fails to reach anything
            self._q.say("  ⚠ Most gateways end in /v1. Leave it as typed if yours does not.")
        return url

    def _key(self, provider: Example) -> bool:
        where = self._q.choice(
            "Where does the key live?",
            [
                Option("env", "an environment variable", "nothing secret reaches the disk"),
                Option("file", "in provider.toml itself", "the file is written 0600"),
            ],
        )
        if where == "file":
            provider.set("api_key", self._q.key("Key (not echoed)"))
            self._key_in_file = True
            self._q.say("  Written into the file — keep it out of git.")
            return True

        variable = self._q.text("Variable", "ROBOVIEWER_API_KEY")
        provider.set("api_key_env", variable)
        if os.environ.get(variable):
            self._q.say(f"  ✓ {variable} is set in this shell.")
            return True
        self._q.say(f"  · {variable} is not set yet. Before a run:")
        self._q.say(f"      export {variable}=...")
        return False

    def _model(self, config: Example) -> None:
        self._q.heading("The model")
        config.set("reviewer.model", self._q.text("Reviewer", "gpt-4o", required=True))
        if self._thinking("Reasoning tokens") is False:
            config.set("reviewer.enable_thinking", False)
        if not self._q.yes_no("A different model for the judge?", default=False):
            return
        # The judge settles claims rather than finding them, so it is where a
        # stronger model earns its price.
        config.set("judge.model", self._q.text("Judge", "gpt-4o", required=True))
        if self._thinking("Reasoning tokens for the judge") is False:
            config.set("judge.enable_thinking", False)

    def _thinking(self, prompt: str) -> bool | None:
        """None leaves the model on its own default, which is what unset means."""
        answer = self._q.choice(
            prompt,
            [
                Option("default", "leave the model on its default", ""),
                Option("off", "off", "usually the largest term in how long a run takes"),
            ],
        )
        return None if answer == "default" else False

    def _run(self, config: Example) -> None:
        self._q.heading("The run")
        language = self._q.text("Language for findings (empty: English)")
        if language:
            config.set("run.output_language", language)
        config.set(
            "run.report_formats",
            self._q.several(
                "Reports to write",
                [Option(name, name, FORMATS.get(name, "")) for name in renders.known()],
                default=["md"],
            ),
        )

    def _write(self, provider: Example, config: Example) -> list[Written]:
        self._q.heading("Writing")
        return [
            self._save(provider_config_path(), provider.text(), holds_key=self._key_in_file),
            self._save(home_config_path(), config.text(), holds_key=False),
        ]

    def _save(self, path: Path, text: str, *, holds_key: bool) -> Written:
        if path.exists() and not self._q.yes_no(f"{path} exists. Overwrite?", default=False):
            self._q.say(f"  · {path} left as it was.")
            return Written(path, saved=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if holds_key:
            path.chmod(0o600)
        self._q.say(f"  ✓ {path}")
        return Written(path, saved=True)

    def _closing(self, written: list[Written], *, reachable: bool) -> int:
        """What is left to do, and the offer to find out now whether the gateway
        can run a review at all."""
        self._q.heading("Done")
        if not any(entry.saved for entry in written):
            self._q.say("Nothing written; the files that were there are untouched.")
            return 0
        self._q.say("Review a branch with:  roboviewer review --into develop")
        if not reachable:
            self._q.say("Once the key is exported, check the gateway:  roboviewer check-provider")
            return 0
        if not self._q.yes_no("Check the gateway now?", default=True):
            self._q.say("Later, then:  roboviewer check-provider")
            return 0
        return self._probe()

    def _probe(self) -> int:
        """The same probe `check-provider` runs, on what was just written."""
        from ..check_provider import check_provider

        self._q.say()
        try:
            cfg = load_config()
        except (FileNotFoundError, ValueError) as exc:
            self._q.say(f"✗ The written config does not load: {exc}")
            return exit_codes.SETUP
        return check_provider(cfg.provider, cfg.reviewer.model, cfg.provider_source)
