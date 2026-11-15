"""What every graph in the deployment shares: the case context and the virtual filesystem.

Filesystem layout (one CompositeBackend, same routes for every agent):

    /case/       the current investigation, shared by the supervisor and all async subagents.
                 Async subagents run on their own threads, so this lives in the store,
                 namespaced by case ID, not in any one thread's state.
    /memories/   long-term memory across investigations (AGENTS.md: what the steward has
                 learned about this lake and its manager's preferences).
    /skills/     investigation playbooks, read-only, loaded on demand.
    everything else (scratch, offloaded tool results, summaries) stays thread-scoped state.
"""

from pathlib import Path
from typing import NotRequired, TypedDict

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend

SKILLS_DIR = Path(__file__).parent / "skills"


class Case(TypedDict):
    """Runtime context for one investigation. Forwarded to every async subagent."""

    case_id: str
    buoy: int
    metric: str
    value: float
    unit: str
    reason: str
    reading_time: str  # ISO time of the anomalous reading
    run: NotRequired[str | None]  # demo or debug run tag, so readings from other runs are ignored
    # Set when the manager asked for this check by iMessage. A run keeps only its schema's keys and
    # hands them back when it wakes its parent, so these must be declared here to reach the reply.
    phone: NotRequired[str]
    space_id: NotRequired[str]
    agent_id: NotRequired[str]
    message_id: NotRequired[str]


class Chat(Case):
    """Runtime context for one incoming iMessage: where to send the reply, plus a case (the text, at
    the time it arrived) for the live checks the liaison launches."""

    phone: str
    space_id: str
    agent_id: str
    message_id: str


def _case(rt) -> str:
    ctx = rt.context or {}
    case_id = ctx.get("case_id") if isinstance(ctx, dict) else getattr(ctx, "case_id", None)
    return str(case_id or "no-case")


def backend() -> CompositeBackend:
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/case/": StoreBackend(namespace=lambda rt: ("case", _case(rt))),
            "/memories/": StoreBackend(namespace=lambda rt: ("steward", "memories")),
            "/skills/": FilesystemBackend(root_dir=str(SKILLS_DIR), virtual_mode=True),
        },
    )


# Playbooks are code, not memory: nobody edits them at runtime
READ_ONLY_SKILLS = FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")
# Only the supervisor curates long-term memory
READ_ONLY_MEMORY = FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="deny")


def case_of(runtime) -> Case:
    ctx = runtime.context
    return ctx if isinstance(ctx, dict) else dict(vars(ctx))
