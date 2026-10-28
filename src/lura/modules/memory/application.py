"""Memory: records every conversation, hands the next one its context, and
gives the model tools to remember, forget and look things up."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ..brain import ToolSpec
from . import domain
from .infrastructure import MemoryStore

log = logging.getLogger(__name__)

#: Messages up to this id are folded into the running summary.
SUMMARISED_UPTO = "summarised_upto"
SUMMARY = "summary"

TOOLS = (
    ToolSpec("remember", "Save a lasting fact about the user (a preference, a name, a project, "
             "a routine) so you know it in every future conversation. One short sentence.",
             {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}),
    ToolSpec("forget", "Remove saved facts that mention these words, when the user asks you to "
             "forget something or a fact is no longer true.",
             {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}),
    ToolSpec("search_history", "Search every past conversation with the user for some words. "
             "Use it when they refer to something discussed before that is not in your context.",
             {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
)


class MemoryService:
    def __init__(self, enabled: Callable[[], bool], budget: Callable[[], int],
                 summarise: Callable[[str], str] | None = None, user_name: Callable[[], str] = lambda: "",
                 store: MemoryStore | None = None):
        self._enabled = enabled
        self._budget = budget
        #: ``summarise(prompt) -> text``: one call to the current brain, no
        #: tools. Injected so memory does not depend on how brains are built.
        self._summarise = summarise
        self._user_name = user_name
        self._store = store
        self._compacting = threading.Lock()

    @property
    def store(self) -> MemoryStore:
        if self._store is None:   # opened on first use: the CLI never needs the database
            self._store = MemoryStore()
        return self._store

    @property
    def enabled(self) -> bool:
        return self._enabled()

    # ── one conversation ──
    def context(self) -> tuple[str, list[tuple[str, str]]]:
        """(text for the system prompt, earlier turns as (role, text)) for a new
        conversation. Built once per conversation so the cached prefix stays put."""
        if not self.enabled:
            return "", []
        store = self.store
        block = domain.context_block(store.facts(), store.get_meta(SUMMARY))
        room = domain.prior_room(self._budget(), block)
        turns = domain.window(store.turns_after(int(store.get_meta(SUMMARISED_UPTO, "0"))), room)
        # The new conversation opens with a user turn; two in a row is
        # rejected by some providers, so an unanswered last one is left out.
        while turns and turns[-1].role == "user":
            turns.pop()
        return block, [(t.role, t.text) for t in turns]

    def start(self) -> int | None:
        return self.store.start() if self.enabled else None

    def add(self, conv_id: int | None, role: str, text: str, model: str = "") -> int | None:
        if conv_id is None:
            return None
        return self.store.add(conv_id, role, text.strip(), model)

    def update(self, message_id: int | None, text: str) -> None:
        if message_id is not None:
            self.store.update(message_id, text.strip())

    def end(self, conv_id: int | None) -> None:
        if conv_id is None:
            return
        self.store.end(conv_id)
        threading.Thread(target=self.compact, daemon=True, name="memory-compact").start()

    # ── compaction ──
    def compact(self) -> bool:
        """Fold the older part of the thread into the summary, if it is due.
        Runs after a conversation, in the background. True if it compacted."""
        if self._summarise is None or not self._compacting.acquire(blocking=False):
            return False
        try:
            store = self.store
            upto = int(store.get_meta(SUMMARISED_UPTO, "0"))
            summary = store.get_meta(SUMMARY)
            room = domain.prior_room(self._budget(), domain.context_block(store.facts(), summary))
            old, _ = domain.split_for_compaction(store.turns_after(upto), room, self._budget())
            if not old:
                return False
            started = time.monotonic()
            new = self._summarise(domain.summary_prompt(summary, old, self._user_name())).strip()
            if not new:
                return False
            store.set_meta(SUMMARY, new)
            store.set_meta(SUMMARISED_UPTO, str(old[-1].id))
            path = store.write_summary_file(new)
            log.info("Memory: folded %d turns into the summary in %.1fs (%s).",
                     len(old), time.monotonic() - started, path)
            return True
        except Exception as exc:
            log.warning("Memory compaction failed (will retry after the next conversation): %s", exc)
            return False
        finally:
            self._compacting.release()

    def reset(self) -> None:
        """Start fresh: the next conversation remembers no earlier turns or
        summary. Past conversations stay listed and searchable."""
        self.store.set_meta(SUMMARISED_UPTO, str(self.store.last_message_id()))
        self.store.set_meta(SUMMARY, "")

    # ── tools for the model (the tools module's provider port) ──
    def tool_specs(self) -> list[ToolSpec]:
        return list(TOOLS) if self.enabled else []

    def owns(self, name: str) -> bool:
        return self.enabled and any(t.name == name for t in TOOLS)

    def call(self, name: str, args: dict[str, Any]) -> str:
        store = self.store
        if name == "remember":
            fact = str(args.get("fact", "")).strip()
            if not fact:
                return "Error: nothing to remember."
            facts = store.facts()
            if domain.tokens(facts) > domain.FACTS_CAP_TOKENS:
                return "Memory is full. Ask the user to tidy it in the dashboard, or forget something first."
            store.set_facts(domain.add_fact(facts, fact))
            return f"Saved: {fact}"
        if name == "forget":
            facts, removed = domain.remove_fact(store.facts(), str(args.get("fact", "")))
            if not removed:
                return "No saved fact mentions that."
            store.set_facts(facts)
            return f"Forgot {removed} fact(s)."
        if name == "search_history":
            query = str(args.get("query", "")).strip()
            hits = store.search(query) if query else []
            if not hits:
                return "Nothing in past conversations matches that."
            return "\n".join(f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(h['ts']))}] "
                             f"{'User' if h['role'] == 'user' else 'Lura'}: {h['text'][:400]}" for h in hits)
        return f"Error: unknown tool {name}."

    # ── the dashboard ──
    def facts(self) -> str:
        return self.store.facts()

    def set_facts(self, text: str) -> None:
        self.store.set_facts(text)

    def conversations(self, query: str = "") -> list[dict]:
        return self.store.conversations(query.strip())

    def transcript(self, conv_id: int) -> list[dict]:
        return self.store.transcript(conv_id)

    def delete(self, conv_id: int) -> bool:
        return self.store.delete(conv_id)

    def delete_all(self) -> None:
        self.store.delete_all()

    def summary(self) -> str:
        return self.store.get_meta(SUMMARY)
