"""Memory: windowing, compaction, the store, the tools, and remembering across conversations."""

import _setup  # noqa: F401
import sys
import tempfile
import threading
from pathlib import Path


def check_window_and_compaction():
    from lura.modules.memory import Turn, split_for_compaction, window

    turns = [Turn(i, "user" if i % 2 else "assistant", "x" * 299) for i in range(1, 21)]   # 100 tokens each
    kept = window(turns, 450)
    assert [t.id for t in kept] == [17, 18, 19, 20], "newest that fit, starting at a user turn"
    merged = window([Turn(1, "user", "a"), Turn(2, "user", "b"), Turn(3, "assistant", "c")], 100)
    assert [(t.id, t.text) for t in merged] == [(1, "a\nb"), (3, "c")], "two user turns in a row are merged"
    assert window([Turn(1, "user", " "), Turn(2, "assistant", "hi")], 100) == []

    old, keep = split_for_compaction(turns, 2000, 10_000)
    assert old == [] and len(keep) == 20, "everything still fits the room: nothing to fold"
    old, keep = split_for_compaction(turns, 1999, 2000)   # 20 × 100 tokens no longer fit
    assert [t.id for t in keep] == [17, 18, 19, 20] and [t.id for t in old] == list(range(1, 17))


def check_store_and_tools():
    from lura.modules.memory import MemoryService
    from lura.modules.memory.infrastructure import MemoryStore

    store = MemoryStore(Path(tempfile.mkdtemp()))
    mem = MemoryService(lambda: True, lambda: 200_000, store=store)
    assert [t.name for t in mem.tool_specs()] == ["remember", "forget", "search_history"]
    assert mem.call("remember", {"fact": "Mohamed prefers Egyptian Arabic"}).startswith("Saved")
    mem.call("remember", {"fact": "Mohamed prefers  Egyptian Arabic"})
    mem.call("remember", {"fact": "Works on Lura in /var/www/html/lura"})
    assert mem.facts() == "- Mohamed prefers Egyptian Arabic\n- Works on Lura in /var/www/html/lura\n"
    assert mem.call("forget", {"fact": "egyptian"}) == "Forgot 1 fact(s)."
    assert mem.call("forget", {"fact": "nothing like it"}) == "No saved fact mentions that."

    conv = mem.start()
    mem.add(conv, "user", "ذكّرني أشتري لبن 100%_")
    row = mem.add(conv, "user", "")
    mem.add(conv, "assistant", "حاضر")
    mem.update(row, "and bread")
    mem.end(conv)
    empty = mem.start()
    mem.end(empty)
    listed = mem.conversations()
    assert len(listed) == 1 and listed[0]["title"] == "ذكّرني أشتري لبن 100%_" and listed[0]["messages"] == 3, \
        "an empty conversation is not listed"
    assert [m["text"] for m in mem.transcript(conv)] == ["ذكّرني أشتري لبن 100%_", "and bread", "حاضر"]
    assert mem.conversations("لبن")[0]["id"] == conv and mem.conversations("100%_")
    assert mem.conversations("50%") == [], "LIKE wildcards in the query are literal"
    assert "لبن" in mem.call("search_history", {"query": "لبن"})

    block, prior = mem.context()
    assert "Works on Lura" in block and prior == [("user", "ذكّرني أشتري لبن 100%_\nand bread"), ("assistant", "حاضر")]
    mem.reset()
    assert mem.context()[1] == [] and mem.conversations(), "start fresh keeps the history listed"
    assert mem.delete(conv) and not mem.delete(conv) and mem.conversations() == []

    off = MemoryService(lambda: False, lambda: 200_000, store=store)
    assert off.tool_specs() == [] and not off.owns("remember") and off.context() == ("", []) and off.start() is None


def check_compaction_writes_summary():
    from lura.modules.memory import MemoryService
    from lura.modules.memory.infrastructure import MemoryStore

    store = MemoryStore(Path(tempfile.mkdtemp()))
    asked = []
    mem = MemoryService(lambda: True, lambda: 2000, lambda prompt: asked.append(prompt) or "- Talked about milk.",
                        store=store)
    conv = mem.start()
    for i in range(20):
        mem.add(conv, "user" if i % 2 == 0 else "assistant", f"turn {i} " + "x" * 290)
    store.end(conv)
    assert mem.compact() is True and "turn 0 " in asked[0] and "turn 19" not in asked[0]
    assert mem.summary() == "- Talked about milk." and list(store.summaries.glob("*.md"))
    block, prior = mem.context()
    assert "Talked about milk" in block and prior[-1][1].startswith("turn 19")
    assert all(not text.startswith("turn 0 ") for _, text in prior), "folded turns are not replayed"
    assert mem.compact() is False, "nothing more is due"

    # Conversation after conversation: every turn is either replayed or in the
    # summary, never dropped in between.
    for n in range(6):
        conv = mem.start()
        for i in range(6):
            mem.add(conv, "user" if i % 2 == 0 else "assistant", f"c{n} t{i} " + "y" * 120)
        store.end(conv)
        mem.compact()
        upto = int(store.get_meta("summarised_upto"))
        replayed = {text for _, text in mem.context()[1]}
        missing = [t.id for t in store.turns_after(upto) if t.text not in replayed]
        assert not missing, f"after conversation {n}, turns {missing} are neither replayed nor summarised"


def check_pipeline_remembers():
    """Two conversations: the second starts with what the first said."""
    from lura.modules.assistant import PipelineConversation
    from lura.modules.brain import Done, Message
    from lura.modules.memory import MemoryService
    from lura.modules.memory.infrastructure import MemoryStore
    from lura.modules.settings import Settings
    from lura.modules.tools import ToolService
    from lura.shared.presence import NullPresence

    seen = []

    class FakeModel:
        provider, model = "anthropic", "claude-opus-5-5"

        def stream(self, system, history, tools):
            seen.append((system, [(m.role, m.text) for m in history], [t.name for t in tools]))
            yield Done(Message("assistant", f"answer {len(seen)}"), "end")

    class FakeBrain:
        def model(self, settings):
            return FakeModel()

    class FakePlayer:
        feed = finish = say = wait = cancel = lambda self, *a: None

    class FakeVoice:
        def player(self, settings, presence=None):
            return FakePlayer()

        def transcriber(self, settings):
            return None

    mem = MemoryService(lambda: True, lambda: 200_000, store=MemoryStore(Path(tempfile.mkdtemp())))
    mem.set_facts("- Likes short answers\n")
    tools = ToolService(None)
    tools.add_provider(mem)
    for prompt in ("first question", "second question"):
        PipelineConversation(Settings(brain_provider="anthropic"), FakeBrain(), FakeVoice(), tools,
                             NullPresence(), threading.Event(), mem).run(initial_prompt=prompt)
    system, history, names = seen[-1]
    assert "Likes short answers" in system and "remember" in names
    # The briefing-style prompt is not stored, the answer is; a thread opens on a user turn.
    assert history == [("user", "second question")], history
    assert [m["text"] for c in mem.conversations() for m in mem.transcript(c["id"])] == ["answer 2", "answer 1"]


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("check_"):
            fn()
            print(f"  {name[6:]} OK")
    print("memory OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
