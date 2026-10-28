"""What Lura is told, and how long she remembers. Pure text rules."""

from __future__ import annotations

from ..brain import Message
from ..memory import tokens

#: A tool-calling model can ask for tools forever. It gets this many rounds,
#: then it has to answer in words.
MAX_TOOL_ROUNDS = 6
#: Gemini counts audio at 32 tokens a second; 16 kHz 16-bit WAV is 32 000
#: bytes a second.
AUDIO_BYTES_PER_TOKEN = 1000
#: Room left in the context for the reply itself.
REPLY_RESERVE = 8000

LANGUAGE_RULES = {
    "ar": ("Always answer in Egyptian Arabic (عامية مصرية), written in Arabic script, even if the "
           "user speaks English. Spell out numbers and units the way an Egyptian would say them."),
    "en": "Always answer in English, even if the user speaks another language.",
}


def language_rule(language: str) -> str:
    return LANGUAGE_RULES.get(language, LANGUAGE_RULES["en"])


def system_prompt(persona: str, assistant_name: str, user_name: str, language: str,
                  machine_context: str, realtime: bool = False, extra: str = "") -> str:
    """The system prompt. Byte-stable for one conversation: it is the cached
    prefix of every request. ``extra`` is memory and skills."""
    parts = [persona.format(name=assistant_name or "Lura")]
    if user_name:
        parts.append(f"The user's name is {user_name}.")
    parts.append(machine_context)
    if not realtime:
        parts.append("Connected services (Chrome, live web pages, files, and any the user added) "
                     "are reachable through find_tools.")
    if extra.strip():
        parts.append(extra.strip())
    # Repeated last on purpose: buried in the middle of a long system message
    # the language rule gets ignored.
    parts.append(f"IMPORTANT: {language_rule(language)}")
    return "\n".join(parts)


def message_tokens(message: Message) -> int:
    cost = tokens(message.text) + 4
    if message.audio_wav:
        cost += len(message.audio_wav) // AUDIO_BYTES_PER_TOKEN
    for call in message.tool_calls:
        cost += tokens(call.name + str(call.arguments))
    return cost


def fit_history(history: list[Message], budget: int) -> list[Message]:
    """The newest part of the conversation that fits in ``budget`` tokens.

    It starts cleanly at a user turn, so no tool result is left without the
    call it answers; the last user turn is always kept. Only the newest
    recording goes as sound: older ones whose transcript has arrived go as
    text, and the newest goes without its transcript, or the brain reads it twice.
    """
    last_audio = max((i for i, m in enumerate(history) if m.audio_wav), default=-1)
    view = []
    for i, m in enumerate(history):
        if m.audio_wav and i != last_audio and m.text:
            m = Message("user", m.text)
        elif m.audio_wav and i == last_audio and m.text:
            m = Message("user", audio_wav=m.audio_wav)
        view.append(m)
    starts = [i for i, m in enumerate(view) if m.role == "user" and not m.tool_call_id]
    if not starts:
        return view
    total, cut = 0, starts[-1]
    suffix = [0] * (len(view) + 1)
    for i in range(len(view) - 1, -1, -1):
        total += message_tokens(view[i])
        suffix[i] = total
    for start in starts:
        if suffix[start] <= budget:
            cut = start
            break
    return view[cut:]


def recent_lines(turns: list[tuple[str, str]], limit: int = 2000) -> str:
    """Earlier turns as plain lines, for Gemini Live, which takes no text turns."""
    text = "\n".join(f"{'User' if role == 'user' else 'You'}: {said}" for role, said in turns)
    return f"The conversation so far:\n{text[-limit:]}" if text else ""


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
