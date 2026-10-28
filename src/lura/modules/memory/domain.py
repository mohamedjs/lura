"""What Lura remembers, and what of it fits in a request. Pure rules.

One endless thread instead of sessions: every conversation starts with the
durable facts (memory.md), a running summary of everything older, and as
many recent turns, verbatim, as the budget allows. When the thread outgrows
the budget, its older part is folded into the summary.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Characters per token, on the safe side: Arabic runs closer to 3 than 4.
CHARS_PER_TOKEN = 3
#: memory.md is injected whole into every system prompt, so it is capped.
FACTS_CAP_TOKENS = 3000
#: Of the budget, the share the previous conversations may take at the start
#: of a new one; the rest is room for the conversation itself.
PRIOR_SHARE = 0.6
#: When they no longer fit, the older part is folded into the summary and
#: this share of the budget is kept verbatim.
KEEP_RECENT = 0.2


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


@dataclass(frozen=True)
class Turn:
    id: int
    role: str        # "user" | "assistant"
    text: str
    ts: float = 0.0


def window(turns: list[Turn], budget: int) -> list[Turn]:
    """The newest turns that fit in ``budget`` tokens, starting at a user turn.

    Consecutive turns of one role are merged (a brain that failed leaves two
    user turns in a row; some providers reject that).
    """
    kept: list[Turn] = []
    used = 0
    for turn in reversed([t for t in turns if t.text.strip()]):
        cost = tokens(turn.text)
        if used + cost > budget:
            break
        kept.append(turn)
        used += cost
    kept.reverse()
    while kept and kept[0].role != "user":
        kept.pop(0)
    merged: list[Turn] = []
    for turn in kept:
        if merged and merged[-1].role == turn.role:
            last = merged[-1]
            merged[-1] = Turn(last.id, turn.role, f"{last.text}\n{turn.text}", turn.ts)
        else:
            merged.append(turn)
    return merged


def prior_room(budget: int, block: str) -> int:
    """Tokens the earlier turns may take in a new conversation, after the
    facts and summary. Compaction and replay use the same number, so no turn
    is ever dropped from the replay before the summary covers it."""
    return max(int(budget * PRIOR_SHARE) - tokens(block), 0)


def split_for_compaction(turns: list[Turn], room: int, budget: int) -> tuple[list[Turn], list[Turn]]:
    """(to fold into the summary, to keep verbatim). Nothing to fold while
    the turns still fit ``room``."""
    if sum(tokens(t.text) for t in turns if t.text.strip()) <= room:
        return [], turns
    keep = window(turns, min(int(budget * KEEP_RECENT), room))
    first_kept = keep[0].id if keep else turns[-1].id + 1 if turns else 0
    return [t for t in turns if t.id < first_kept], [t for t in turns if t.id >= first_kept]


def summary_prompt(previous: str, turns: list[Turn], user_name: str) -> str:
    who = user_name or "the user"
    # "Assistant", never a name: the user can rename the assistant, and a
    # summary that calls it by the old name would contradict the new one.
    lines = "\n".join(f"[{'User' if t.role == 'user' else 'Assistant'}] {t.text}" for t in turns)
    return (f"You keep the long-term memory of a voice assistant about {who}. Below is the "
            f"previous summary and the conversation since. Write the new summary in Markdown: what "
            f"{who} asked for, decided, is working on, and anything to follow up, newest last. Keep "
            f"names, numbers, paths and dates exact; drop small talk. Write it in the language the "
            f"conversation mostly uses. Call the assistant \"the assistant\", never by a name. At most "
            f"about 1500 words.\n\n"
            f"## Previous summary\n{previous or '(none)'}\n\n## Conversation\n{lines}")


def cap_facts(facts: str) -> str:
    limit = FACTS_CAP_TOKENS * CHARS_PER_TOKEN
    return facts if len(facts) <= limit else facts[:limit].rsplit("\n", 1)[0] + "\n…"


def context_block(facts: str, summary: str) -> str:
    """What goes into the system prompt. Empty when there is nothing yet."""
    parts = []
    if facts.strip():
        parts.append("What you know about the user (from your memory; keep it up to date with "
                     "remember and forget):\n" + cap_facts(facts.strip()))
    if summary.strip():
        parts.append("Summary of your earlier conversations with the user:\n" + summary.strip())
    if parts:
        parts.append("Older conversations can be looked up with search_history.")
    return "\n\n".join(parts)


def add_fact(facts: str, fact: str) -> str:
    fact = " ".join(fact.split())
    lines = [line for line in facts.splitlines() if line.strip()]
    if any(line.lstrip("- ").lower() == fact.lower() for line in lines):
        return facts
    return "\n".join(lines + [f"- {fact}"]) + "\n"


def remove_fact(facts: str, fact: str) -> tuple[str, int]:
    """Drop every line that mentions ``fact``. Returns (new text, lines removed)."""
    needle = " ".join(fact.split()).lower()
    lines = facts.splitlines()
    kept = [line for line in lines if not needle or needle not in line.lower()]
    return ("\n".join(kept) + "\n" if kept else ""), len(lines) - len(kept)
