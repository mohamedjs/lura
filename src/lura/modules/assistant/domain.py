"""What Lura is told, and how long she remembers. Pure text rules."""

from __future__ import annotations

import re

from ..brain import Message
from ..memory import tokens

#: A tool-calling model can ask for tools forever. It gets this many rounds,
#: then it has to answer in words.
MAX_TOOL_ROUNDS = 6
#: Gemini counts audio at 32 tokens a second; 16 kHz 16-bit WAV is 32 000
#: bytes a second.
AUDIO_BYTES_PER_TOKEN = 1000
#: What the brain answers when what it heard was not meant for it (noise, a
#: TV, someone else in the room). Never spoken, never kept.
NO_REPLY = "[[quiet]]"
#: Turns in a row that were noise or nothing before the session closes, so a
#: noisy room does not keep Lura listening (and paying) forever.
MAX_IGNORED_TURNS = 2
#: Room left in the context for the reply itself.
REPLY_RESERVE = 8000

_LANGUAGE_NAMES = {"ar": "Egyptian Arabic (عامية مصرية, in Arabic script)", "en": "English"}


def language_rule(language: str) -> str:
    """Answer in the language the user speaks; the dashboard's choice is only
    the default. An Egyptian who asks in English wants English back, and the
    other way round."""
    default = _LANGUAGE_NAMES.get(language, _LANGUAGE_NAMES["en"])
    return (f"Reply in the language the user speaks to you. If they speak Arabic, answer in Egyptian "
            f"Arabic (عامية مصرية) written in Arabic script, never Modern Standard Arabic; if they speak "
            f"English, answer in English; if they mix, use the one they mostly used. Your default is "
            f"{default}: use it when you speak first, or when you cannot tell (a name, a one-word "
            f"reply, noise). Keep names, commands, file names and technical terms as they are. Say "
            f"numbers and units the way a speaker of that language says them out loud.")


def addressing_rule(user_gender: str, assistant_gender: str) -> str:
    """Arabic is gendered both ways: how she speaks to the user, and of herself."""
    me = ("a woman: in Arabic use feminine forms for yourself (أنا جاهزة، فتحتلك، شايفة)"
          if assistant_gender != "male" else
          "a man: in Arabic use masculine forms for yourself (أنا جاهز، فتحتلك، شايف)")
    if user_gender == "male":
        them = "The user is a man: in Arabic address him in masculine forms (إنتَ، عايز، تحب، قولّي)."
    elif user_gender == "female":
        them = "The user is a woman: in Arabic address her in feminine forms (إنتِ، عايزة، تحبي، قوليلي)."
    else:
        them = ("You do not know whether the user is a man or a woman: in Arabic prefer wording that "
                "avoids gendered forms; if they tell you, follow it and save it with remember.")
    return f"You speak of yourself as {me}. {them}"


#: The persona every 1.x/2.0 settings file stored. It is now part of the
#: built-in prompt, so a stored copy of it adds nothing.
LEGACY_PERSONA = (
    "You are {name}, a spoken desktop assistant. Keep answers short and conversational, "
    "as if talking out loud. Never use markdown, lists, code blocks or emoji: everything "
    "you write is read aloud."
)


def _safe_format(text: str, **values: str) -> str:
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text   # a persona with stray braces is used as written


def system_prompt(persona: str, assistant_name: str, user_name: str, language: str,
                  machine_context: str, realtime: bool = False, extra: str = "",
                  now: str = "", user_gender: str = "", assistant_gender: str = "female",
                  services: str = "") -> str:
    """The system prompt. Byte-stable for one conversation: it is the cached
    prefix of every request. ``extra`` is memory and skills; ``now`` the date
    and time the conversation started; ``services`` the connected MCP servers."""
    name = assistant_name.strip() or "Lura"
    who = user_name.strip() or "the user"
    parts = [
        f"You are {name}, a voice assistant that lives on {who}'s computer. You talk with them out "
        f"loud and you can act on this computer with your tools. Your name is {name}. The user can "
        f"rename you at any time: notes, summaries or earlier turns that use another name for the "
        f"assistant are still about you.",
        f"The user's name is {user_name.strip()}." if user_name.strip() else
        "You do not know the user's name yet; if they tell you, save it with remember.",
    ]
    if now:
        parts.append(f"It is now {now}.")
    parts.append(
        "How you hear: the user speaks and you get either their audio or a transcript of it. Both "
        "can be imperfect: words misheard, cut off, or mixed with other voices, a TV or room noise. "
        "Work out what they most likely mean from the whole conversation, not from one odd word. "
        "If you truly cannot tell what they want, ask one short question instead of guessing or "
        "acting on a fragment.")
    if realtime:
        parts.append("If what you heard was not meant for you (noise, a TV, people talking to each "
                     "other), stay silent.")
    else:
        parts.append(f"If what you heard was not meant for you (only noise, a TV or video, people "
                     f"talking to each other, a cough) or has no clear words, reply with exactly "
                     f"{NO_REPLY} and nothing else; it is never spoken.")
    parts.append(
        "How you talk: everything you write is read aloud. Answer in one to three short sentences "
        "unless they ask for more; lead with the answer itself; do not repeat their question back. "
        "No markdown, lists, headings, code, emoji or links: say things the way a person would, "
        "numbers and units included. After you do something, say briefly what you did and what you "
        "found. Be warm, plain and direct.")
    parts.append(
        "How you think: keep track of the whole conversation, including earlier ones you are given "
        "below, and connect new questions to them (\"it\", \"that file\", \"the same as "
        "yesterday\"). When the user shares something lasting about themselves, their work or "
        "their preferences, save it with remember. When they refer to something from before that "
        "you do not see, look it up with search_history before saying you do not know. Never "
        "invent facts, numbers, file contents or tool results; if you are not sure, say so.")
    parts.append(addressing_rule(user_gender, assistant_gender))
    parts.append(
        "How you act: when a tool can answer or do it, call it right away instead of describing "
        "what you would do, then answer from what it returned. Chain tools when a task needs "
        "several steps: read each result, decide the next step, and keep going until it is done, "
        "then say the outcome in a sentence or two. Read-only actions (looking, listing, reading, "
        "searching, opening) need no permission. Before anything that sends, posts, buys, books, "
        "deletes or changes something for other people, say exactly what you will do and wait for "
        "a yes. If a tool fails, say so plainly and try the next best way.")
    parts.append(machine_context.strip())
    if realtime:
        parts.append("Your connected services' tools are declared to you directly; use them like "
                     "your own." + (f"\n{services}" if services else ""))
    else:
        parts.append(
            "Connected services (MCP servers: the browser, live web pages, files, and any the user "
            "added such as GitHub or Notion) are reached in two steps within the same turn: call "
            "find_tools with a few words for the action (\"list github pull requests\", \"open a "
            "chrome tab\", \"search notion pages\"), then call the tool it returns. Prefer a "
            "connected service over run_command when one fits. If find_tools finds nothing, try "
            "once more with other words (the service's name, another verb), then fall back to "
            "run_command or open_url, or say that service is not connected."
            + (f"\n{services}" if services else ""))
    custom = persona.strip()
    if custom and custom != LEGACY_PERSONA:
        parts.append("The user's own instructions for you:\n" + _safe_format(custom, name=name))
    if extra.strip():
        parts.append(extra.strip())
    # Repeated last on purpose: buried in the middle of a long system message
    # the language rule gets ignored.
    parts.append(f"IMPORTANT: {language_rule(language)}")
    return "\n\n".join(parts)


def now_text() -> str:
    """The date and time, as the prompt states it (local, with the weekday)."""
    import time
    return time.strftime("%A %d %B %Y, %H:%M (%Z)")


def speakable_reply(text: str) -> str:
    """The streamed reply minus the ``NO_REPLY`` marker, wherever the model put
    it, and minus a marker that is still half-written at the end — so the
    voice never says "quiet"."""
    cleaned = text.replace(NO_REPLY, "")
    for k in range(len(NO_REPLY) - 1, 0, -1):
        if cleaned.endswith(NO_REPLY[:k]):
            return cleaned[:-k]
    return cleaned


def could_be_quiet(text: str) -> bool:
    """Nothing worth saying yet (only the marker, quotes, backticks…): hold the
    voice back, the reply may still turn out to be ``NO_REPLY``."""
    return not re.search(r"\w", speakable_reply(text))


def is_quiet(text: str) -> bool:
    """The model chose not to answer: the marker and nothing else that can be said
    (a weak model wraps it in quotes or backticks, or adds a full stop)."""
    return NO_REPLY in text and not re.search(r"\w", text.replace(NO_REPLY, ""))


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


def briefing_prompt(data: dict[str, str], language: str, user_name: str, user_gender: str = "") -> str:
    readings = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in data.items())
    if language == "ar":
        her = user_gender == "female"
        who = user_name or ("صاحبتي" if her else "صاحبي")
        on, to, ask, wants = (("عليها", "ادّيلها", "بسؤالها", "تحب تبدأ") if her
                              else ("عليه", "ادّيله", "بسؤاله", "يحب يبدأ"))
        return (f"الجهاز لسه فاتح. ابدأ الكلام من نفسك مع {who}: سلّم {on} بالمصري بشكل طبيعي وودود، "
                f"وبعدين {to} ملخص سريع لحالة الجهاز واليوم من البيانات دي بس (ما تخترعش أرقام):\n"
                f"{readings}\nلو في آخر كوميت، اشرح معناه في جملة. اختم {ask} {wants} بإيه. "
                f"كلام صوتي طبيعي من غير قوائم أو رموز.")
    who = user_name or "there"
    return (f"The computer just started. Speak first: greet {who} warmly and naturally, then give a "
            f"quick spoken summary of the day and the machine from these readings only (never invent "
            f"numbers):\n{readings}\nIf there is a latest commit, say what it means in one sentence. "
            f"End by asking what they want to start with. Natural speech, no lists or symbols.")


# In no particular language on purpose: she answers in the one the
# conversation is in, which may not be the dashboard's default.
GOODBYE_NUDGE = "Say a short goodbye in one sentence, in the language we have been speaking."
ANSWER_NOW = "Stop using tools and answer me now in one sentence, in the language we have been speaking."
