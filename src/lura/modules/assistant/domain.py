"""What Lura is told, and how long she remembers. Pure text rules."""

from __future__ import annotations

from ..brain import Message

#: A tool-calling model can ask for tools forever. It gets this many rounds,
#: then it has to answer in words.
MAX_TOOL_ROUNDS = 6
#: Turns of history sent each time. Audio turns are large; a voice chat that
#: has drifted ten exchanges on rarely needs the first.
MAX_HISTORY_TURNS = 10

LANGUAGE_RULES = {
    "ar": ("Always answer in Egyptian Arabic (عامية مصرية), written in Arabic script, even if the "
           "user speaks English. Spell out numbers and units the way an Egyptian would say them."),
    "en": "Always answer in English, even if the user speaks another language.",
}


def language_rule(language: str) -> str:
    return LANGUAGE_RULES.get(language, LANGUAGE_RULES["en"])


def system_prompt(persona: str, assistant_name: str, user_name: str, language: str,
                  machine_context: str, realtime: bool = False) -> str:
    """The system prompt. Byte-stable for a given settings snapshot: it is the
    cached prefix of every request."""
    parts = [persona.format(name=assistant_name or "Lura")]
    if user_name:
        parts.append(f"The user's name is {user_name}.")
    parts.append(machine_context)
    if not realtime:
        parts.append("Connected services (Chrome, live web pages, files, and any the user added) "
                     "are reachable through find_tools.")
    # Repeated last on purpose: buried in the middle of a long system message
    # the language rule gets ignored.
    parts.append(f"IMPORTANT: {language_rule(language)}")
    return "\n".join(parts)


def trim_history(history: list[Message], max_turns: int = MAX_HISTORY_TURNS) -> list[Message]:
    """Keep the last ``max_turns`` user turns, starting cleanly at a user turn
    so no tool result is left without the call it answers."""
    starts = [i for i, m in enumerate(history) if m.role == "user" and not m.tool_call_id]
    if len(starts) <= max_turns:
        return history
    return history[starts[-max_turns]:]


def briefing_prompt(data: dict[str, str], language: str, user_name: str) -> str:
    readings = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in data.items())
    who = user_name or ("صاحبي" if language == "ar" else "there")
    if language == "ar":
        return (f"الجهاز لسه فاتح. ابدأ الكلام من نفسك مع {who}: سلّم عليه بالمصري بشكل طبيعي وودود، "
                f"وبعدين ادّيله ملخص سريع لحالة الجهاز واليوم من البيانات دي بس (ما تخترعش أرقام):\n"
                f"{readings}\nلو في آخر كوميت، اشرح معناه في جملة. اختم بسؤاله يحب يبدأ بإيه. "
                f"كلام صوتي طبيعي من غير قوائم أو رموز.")
    return (f"The computer just started. Speak first: greet {who} warmly and naturally, then give a "
            f"quick spoken summary of the day and the machine from these readings only (never invent "
            f"numbers):\n{readings}\nIf there is a latest commit, say what it means in one sentence. "
            f"End by asking what they want to start with. Natural speech, no lists or symbols.")


GOODBYE_NUDGE = {"ar": "قول مع السلامة في جملة قصيرة.", "en": "Say a short goodbye, one sentence."}
ANSWER_NOW = {"ar": "كفاية أدوات، جاوبني دلوقتي في جملة واحدة.",
              "en": "Stop using tools and answer me now in one sentence."}
