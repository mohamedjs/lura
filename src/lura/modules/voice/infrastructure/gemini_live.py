"""Realtime mode: one Gemini Live session, mic in at 16 kHz, speech out at 24 kHz.

Gemini owns everything here — hearing, thinking, turn detection, the voice —
so the brain picker does not apply. The tools still do: local tools and MCP
connectors are declared to the session and answered through ``dispatch``.

The mic is gated while Lura speaks. Without it the mic hears her, barge-in
fires, and she interrupts herself in a loop. The cost is no barge-in.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..domain import INPUT_RATE, OUTPUT_RATE

log = logging.getLogger(__name__)

IN_BLOCK = 800     # 50 ms at 16 kHz
OUT_BLOCK = 1200   # 50 ms at 24 kHz


def _schema(types, schema: dict | None):
    """JSON Schema → genai Schema. MCP tools arrive as JSON Schema."""
    if not isinstance(schema, dict):
        return types.Schema(type=types.Type.OBJECT, properties={})
    kinds = {"string": types.Type.STRING, "number": types.Type.NUMBER, "integer": types.Type.INTEGER,
             "boolean": types.Type.BOOLEAN, "array": types.Type.ARRAY, "object": types.Type.OBJECT}
    kind = schema.get("type", "object")
    if isinstance(kind, list):  # ["string", "null"]
        kind = next((k for k in kind if k != "null"), "string")
    stype = kinds.get(str(kind).lower(), types.Type.STRING)
    props = {k: _schema(types, v) for k, v in (schema.get("properties") or {}).items() if isinstance(v, dict)}
    items = schema.get("items")
    return types.Schema(
        type=stype,
        properties=props if stype == types.Type.OBJECT else None,
        required=list(schema["required"]) if isinstance(schema.get("required"), list) else None,
        description=schema.get("description"),
        items=_schema(types, items) if stype == types.Type.ARRAY and isinstance(items, dict) else None,
    )


class LiveConversation:
    def __init__(self, key: str, settings, system: str, tools, dispatch, presence,
                 initial_prompt: str | None = None):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=key)
        self.settings = settings
        self._system = system
        self._tools = tools               # list[ToolSpec]
        self._dispatch = dispatch         # (name, args) -> (text, ends_session)
        self._presence = presence
        self._initial = initial_prompt
        self._speaking = asyncio.Event()
        self._tool_active = asyncio.Event()
        self._ending = False
        self._started = time.monotonic()
        self._transcript: list[str] = []
        self._buffers: dict[str, list[str]] = {"you": [], "lura": []}

    def stop(self) -> None:
        self._ending = True

    @property
    def transcript(self) -> list[tuple[str, str]]:
        """What was said, as (role, text) turns, for the history."""
        return [("user" if line.startswith("User: ") else "assistant", line.split(": ", 1)[1])
                for line in self._transcript]

    def _config(self):
        t = self._types
        system = self._system
        if self._transcript:
            # The native-audio model rejects text turns, so context across a
            # reconnect rides in the system instruction instead.
            recent = " | ".join(self._transcript[-6:])[-400:]
            system += f"\n\n[Resuming a conversation. Recent context: {recent}]\nDo not greet again."
        declarations = []
        for spec in self._tools:
            try:
                declarations.append(t.FunctionDeclaration(name=spec.name, description=spec.description,
                                                          parameters=_schema(t, spec.parameters)))
            except Exception as exc:
                log.warning("Skipping tool %s for Live: %s", spec.name, exc)
        return t.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=t.SpeechConfig(
                voice_config=t.VoiceConfig(prebuilt_voice_config=t.PrebuiltVoiceConfig(
                    voice_name=self.settings.voice_name)),
                language_code="ar-EG" if self.settings.language == "ar" else "en-US"),
            system_instruction=t.Content(parts=[t.Part(text=system)]),
            tools=[t.Tool(function_declarations=declarations)] if declarations else None,
            input_audio_transcription=t.AudioTranscriptionConfig(),
            output_audio_transcription=t.AudioTranscriptionConfig(),
        )

    def _flush(self, who: str) -> None:
        if text := "".join(self._buffers[who]).strip():
            self._transcript.append(f"{'User' if who == 'you' else 'Assistant'}: {text}")
        self._buffers[who].clear()

    async def _pump_mic(self, session, loop) -> None:
        from .audio import MicStream

        blocks: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        def on_audio(block: bytes):
            if (self.settings.gate_mic_while_speaking and self._speaking.is_set()) or self._tool_active.is_set():
                return
            try:
                loop.call_soon_threadsafe(blocks.put_nowait, block)
            except (asyncio.QueueFull, RuntimeError):
                pass

        with MicStream(self.settings.input_device, IN_BLOCK / INPUT_RATE, on_block=on_audio):
            while True:
                block = await blocks.get()
                if not self._tool_active.is_set():
                    await session.send_realtime_input(
                        audio=self._types.Blob(data=block, mime_type=f"audio/pcm;rate={INPUT_RATE}"))

    async def _play(self, chunks: asyncio.Queue) -> None:
        from .audio import OutputStream, rms_of

        stream = OutputStream(self.settings.output_device, OUTPUT_RATE, OUT_BLOCK / OUTPUT_RATE)
        try:
            while True:
                chunk = await chunks.get()
                if chunk is None:   # turn finished
                    self._speaking.clear()
                    self._presence.set_amplitude(0.0)
                    self._presence.set_state("listening")
                    continue
                self._speaking.set()
                self._presence.set_state("speaking")
                stride = OUT_BLOCK * 2
                for off in range(0, len(chunk), stride):
                    part = chunk[off:off + stride]
                    self._presence.set_amplitude(min(1.0, rms_of(part) * 4.0))
                    await asyncio.to_thread(stream.write, part)
        finally:
            stream.close()

    async def _answer_tools(self, session, tool_call) -> None:
        t = self._types
        responses = []
        for fc in tool_call.function_calls:
            log.info("tool %s %s", fc.name, dict(fc.args or {}))
            text, ends = await asyncio.to_thread(self._dispatch, fc.name, dict(fc.args or {}))
            if ends:
                self._ending = True
            responses.append(t.FunctionResponse(name=fc.name, id=fc.id, response={"output": text}))
        await session.send_tool_response(function_responses=responses)

    async def _receive(self, session, chunks: asyncio.Queue) -> None:
        async for message in session.receive():
            server = getattr(message, "server_content", None)
            if getattr(message, "data", None):
                self._tool_active.clear()
                await chunks.put(message.data)
            if getattr(message, "tool_call", None):
                self._tool_active.set()
                self._presence.set_state("thinking")
                await self._answer_tools(session, message.tool_call)
            if server is None:
                continue
            if (heard := getattr(server, "input_transcription", None)) and heard.text:
                log.info("you: %s", heard.text)
                if not self._buffers["you"]:            # a new user turn
                    self._presence.set_caption("lura", "")
                self._flush("lura")
                self._buffers["you"].append(heard.text)
                self._presence.set_caption("you", "".join(self._buffers["you"]))
            if (said := getattr(server, "output_transcription", None)) and said.text:
                self._flush("you")
                self._buffers["lura"].append(said.text)
                self._presence.set_caption("lura", "".join(self._buffers["lura"]))
            if getattr(server, "interrupted", False):
                while not chunks.empty():
                    chunks.get_nowait()
                self._speaking.clear()
                self._flush("lura")
            if getattr(server, "turn_complete", False):
                self._tool_active.clear()
                self._flush("lura")
                await chunks.put(None)

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self._ending and not self._speaking.is_set():
                return
            if time.monotonic() - self._started > self.settings.max_session_seconds:
                log.info("Session hit its time cap.")
                return

    async def _run(self) -> None:
        chunks: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        watchdog = asyncio.create_task(self._watchdog())
        player = asyncio.create_task(self._play(chunks))
        try:
            # The server may close the socket after a turn; reconnecting
            # transparently keeps one conversation until "bye" or the cap.
            while not self._ending and not watchdog.done():
                try:
                    async with self._client.aio.live.connect(model=self.settings.realtime_model,
                                                             config=self._config()) as session:
                        self._presence.set_state("listening")
                        if self._initial:
                            self._tool_active.set()
                            self._presence.set_state("thinking")
                            await session.send_realtime_input(text=self._initial)
                            self._initial = None
                        mic = asyncio.create_task(self._pump_mic(session, loop))
                        recv = asyncio.create_task(self._receive(session, chunks))
                        done, _ = await asyncio.wait([mic, recv, watchdog], return_when=asyncio.FIRST_COMPLETED)
                        mic.cancel()
                        recv.cancel()
                        await asyncio.gather(mic, recv, return_exceptions=True)
                        for task in done - {watchdog}:
                            if not task.cancelled() and task.exception():
                                log.warning("Live task failed: %s", task.exception())
                        if watchdog in done:
                            break
                except Exception as exc:
                    log.warning("Live session error: %s", exc)
                if self._ending or watchdog.done():
                    break
                self._speaking.clear()
                self._presence.set_state("connecting")
                await asyncio.sleep(1)
        finally:
            self._presence.set_state("idle")
            watchdog.cancel()
            player.cancel()
            await asyncio.gather(watchdog, player, return_exceptions=True)

    def run(self) -> None:
        asyncio.run(self._run())


async def _probe(key: str, model: str, voice: str) -> str:
    from google import genai
    from google.genai import types

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        output_audio_transcription=types.AudioTranscriptionConfig())
    reply: list[str] = []
    async with genai.Client(api_key=key).aio.live.connect(model=model, config=config) as session:
        await session.send_realtime_input(text="Reply with exactly: hello")
        async for message in session.receive():
            server = getattr(message, "server_content", None)
            if server is not None:
                if (said := getattr(server, "output_transcription", None)) and said.text:
                    reply.append(said.text)
                if getattr(server, "turn_complete", False):
                    break
    return "".join(reply).strip()


def probe(key: str, model: str, voice: str) -> str:
    """One canned turn, for selftest."""
    return asyncio.run(_probe(key, model, voice))
