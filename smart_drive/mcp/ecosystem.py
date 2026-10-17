"""smart_drive.mcp.ecosystem - Catalogue of the MCP servers SmartDrive-OS sets up around itself.

Every member ships as a zero-dependency Node bundle that is committed in its own repository
(``dist/<name>.mjs``), so a plain ``git clone`` is enough: no ``npm install``, no build toolchain.
Constellation, the control plane, is listed first because everything else is registered through it.

mcp-agy is deliberately absent: it needs its own Python venv, Python >= 3.10 and an Antigravity
API key, so it cannot be set up unattended.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

#: Minimum Node.js version (``engines.node`` of every member).
MIN_NODE: Tuple[int, int] = (22, 4)

#: Agents a member is declared for in Constellation's manifest. ``antigravity-cli`` is left out on
#: purpose: headless subagents get a lightweight configuration of their own.
DEFAULT_AGENTS: Tuple[str, ...] = ("claude", "antigravity", "cursor", "codex")

GITHUB_OWNER_URL = "https://github.com/DuongNAD"

#: Name under which SmartDrive-OS registers itself (``smart-drive`` in every agent config).
SELF_NAME = "smart-drive"
SELF_REPO = f"{GITHUB_OWNER_URL}/smart-drive-os"
SELF_DESCRIPTION = "SSD workspace governance, fast BM25/vector learning search, deduplication, and index maintenance"

#: The member every other registration goes through.
CONTROL_PLANE = "constellation"


@dataclass(frozen=True)
class Member:
    """One MCP server of the ecosystem."""

    name: str          # server name in agent configs and in Constellation's manifest
    repo_name: str     # GitHub repository, also the checkout directory name
    entry: str         # bundled server inside the checkout, posix separators
    description: str

    @property
    def repo(self) -> str:
        return f"{GITHUB_OWNER_URL}/{self.repo_name}"


MEMBERS: Tuple[Member, ...] = (
    Member(
        "constellation",
        "constellation-mcp",
        "dist/constellation.mjs",
        "Control plane of the MCP ecosystem: manifest, registration into coding agents, health checks, "
        "drift doctor, updates",
    ),
    Member(
        "polaris",
        "polaris-mcp",
        "dist/polaris.mjs",
        "Evidence-based personal study course architect: research, quality gates, scheduling, "
        "and spaced retention",
    ),
    Member(
        "aurora-slides",
        "aurora-slides-mcp",
        "dist/aurora.mjs",
        "Apple keynote-style presentations and interactive study decks with practice and progress events",
    ),
    Member(
        "vega",
        "vega-mcp",
        "dist/vega.mjs",
        "FSRS spaced-repetition memory engine and card inbox",
    ),
    Member(
        "orion",
        "orion-mcp",
        "dist/orion.mjs",
        "Practice labs checked by real tests, with tutoring-ladder hints",
    ),
    Member(
        "sirius",
        "sirius-mcp",
        "dist/sirius.mjs",
        "Execution loop: deadline sentinel, task triage, today router, session timer, weekly review",
    ),
    Member(
        "mira",
        "mira-mcp",
        "dist/mira.mjs",
        "Code-tracing lab: real Python traces in a step-through player with prediction gates",
    ),
)


def member_names() -> Tuple[str, ...]:
    """Names of all members, control plane first."""
    return tuple(m.name for m in MEMBERS)


__all__ = [
    "CONTROL_PLANE",
    "DEFAULT_AGENTS",
    "GITHUB_OWNER_URL",
    "MEMBERS",
    "MIN_NODE",
    "Member",
    "SELF_DESCRIPTION",
    "SELF_NAME",
    "SELF_REPO",
    "member_names",
]
