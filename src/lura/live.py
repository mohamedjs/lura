"""One conversation with Gemini Live: mic in at 16kHz, speech out at 24kHz."""

from __future__ import annotations

import asyncio
import logging
import time

import sounddevice as sd
from google import genai
from google.genai import types

from .config import CHANNELS, INPUT_RATE, OUTPUT_RATE, Settings

log = logging.getLogger(__name__)

IN_BLOCK = 800    # 50ms at 16kHz — small enough that barge-in stays responsive
OUT_BLOCK = 1200  # 50ms at 24kHz


def build_config(settings: Settings) -> types.LiveConnectConfig:
    from .tools import GEMINI_TOOLS, get_machine_context

    system_instruction = f"{settings.system_instruction}\n{get_machine_context()}"
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.voice)
            ),
            language_code=settings.language,
        ),
        system_instruction=types.Content(
            parts=[types.Part(text=system_instruction)]
        ),
        tools=GEMINI_TOOLS,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


class Conversation:
    """A single wake-to-idle exchange.

    A Live session is not held open between invocations: the API caps session
    length, and an idle socket for hours is a reconnect storm waiting to
    happen. "Running all day" is the wake loop, not the session.
    """

    def __init__(self, settings: Settings, client: genai.Client):
        self.settings = settings
        self.client = client
        self._speaking = asyncio.Event()
        self._ending = False
        self._last_voice = time.monotonic()
        self._started = time.monotonic()

    # ── audio in ────────────────────────────────────────────────────────────
    async def _pump_mic(self, session, loop: asyncio.AbstractEventLoop) -> None:
        blocks: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        def on_audio(indata, frames, time_info, status):
            if status:
                log.debug("input status: %s", status)
            # ponytail: the crude fix for the echo loop — the mic is muted
            # while the assistant talks, so it cannot hear itself, interrupt
            # itself, and spiral. The cost is no barge-in: you cannot cut the
            # answer off by talking over it. Set gate_mic_while_speaking=false
            # once you are on headphones or PipeWire's module-echo-cancel, and
            # barge-in starts working.
            if self.settings.gate_mic_while_speaking and self._speaking.is_set():
                return
            try:
                loop.call_soon_threadsafe(blocks.put_nowait, bytes(indata))
            except (asyncio.QueueFull, RuntimeError):
                pass

        with sd.RawInputStream(
            samplerate=INPUT_RATE,
            blocksize=IN_BLOCK,
            device=self.settings.input_device,
            dtype="int16",
            channels=CHANNELS,
            callback=on_audio,
        ):
            while True:
                block = await blocks.get()
                await session.send_realtime_input(
                    audio=types.Blob(data=block, mime_type=f"audio/pcm;rate={INPUT_RATE}")
                )

    # ── audio out ───────────────────────────────────────────────────────────
    async def _play(self, chunks: asyncio.Queue) -> None:
        stream = sd.RawOutputStream(
            samplerate=OUTPUT_RATE,
            blocksize=OUT_BLOCK,
            device=self.settings.output_device,
            dtype="int16",
            channels=CHANNELS,
        )
        stream.start()
        try:
            while True:
                chunk = await chunks.get()
                if chunk is None:  # turn finished
                    self._speaking.clear()
                    continue
                self._speaking.set()
                self._last_voice = time.monotonic()
                await asyncio.to_thread(stream.write, chunk)
        finally:
            stream.stop()
            stream.close()

    # ── the session ─────────────────────────────────────────────────────────
    async def _handle_tool_call(self, session, tool_call) -> None:
        from .tools import open_application, run_command

        responses = []
        for fc in tool_call.function_calls:
            name = fc.name
            args = fc.args or {}
            log.info("Gemini requested tool %s with args %s", name, args)

            if name == "run_command":
                cmd = args.get("command", "")
                result = await asyncio.to_thread(run_command, cmd)
                responses.append(
                    types.FunctionResponse(
                        name=name,
                        id=fc.id,
                        response={"output": result},
                    )
                )
            elif name == "open_application":
                app = args.get("app_name", "")
                result = await asyncio.to_thread(open_application, app)
                responses.append(
                    types.FunctionResponse(
                        name=name,
                        id=fc.id,
                        response={"output": result},
                    )
                )
            elif name == "end_session":
                self._ending = True
                responses.append(
                    types.FunctionResponse(
                        name=name,
                        id=fc.id,
                        response={"output": "Session ending now. Say a brief friendly goodbye."},
                    )
                )
            else:
                responses.append(
                    types.FunctionResponse(
                        name=name,
                        id=fc.id,
                        response={"error": f"Unknown tool: {name}"},
                    )
                )

        if responses:
            self._last_voice = time.monotonic()
            await session.send_tool_response(function_responses=responses)

    async def _receive(self, session, chunks: asyncio.Queue) -> None:
        async for message in session.receive():
            server = getattr(message, "server_content", None)

            if getattr(message, "data", None):
                await chunks.put(message.data)

            if getattr(message, "tool_call", None):
                await self._handle_tool_call(session, message.tool_call)

            if server is not None:
                if getattr(server, "input_transcription", None):
                    text = server.input_transcription.text
                    if text:
                        self._last_voice = time.monotonic()
                        log.info("you: %s", text)
                if getattr(server, "output_transcription", None):
                    text = server.output_transcription.text
                    if text:
                        log.info("gemini: %s", text)
                if getattr(server, "interrupted", False):
                    # The model was cut off: drop whatever is still queued so
                    # the old answer does not keep playing over the new one.
                    while not chunks.empty():
                        chunks.get_nowait()
                    self._speaking.clear()
                if getattr(server, "turn_complete", False):
                    await chunks.put(None)

    async def _watchdog(self) -> None:
        """End the conversation on silence, explicit ending, or hard cap."""
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if self._ending and not self._speaking.is_set():
                log.info("Session ended by user request.")
                return
            if now - self._started > self.settings.max_session_seconds:
                log.info("Session hit its time cap.")
                return
            if self._speaking.is_set():
                continue
            if now - self._last_voice > self.settings.idle_timeout:
                log.info("Nothing said for %.0fs — closing.", self.settings.idle_timeout)
                return

    async def run(self) -> None:
        config = build_config(self.settings)
        async with self.client.aio.live.connect(
            model=self.settings.model, config=config
        ) as session:
            chunks: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            tasks = [
                asyncio.create_task(self._pump_mic(session, loop)),
                asyncio.create_task(self._play(chunks)),
                asyncio.create_task(self._receive(session, chunks)),
                asyncio.create_task(self._watchdog()),
            ]
            try:
                # The watchdog is the only one expected to return; whichever
                # finishes first ends the conversation.
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()  # surface a real failure rather than hiding it
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


async def probe(settings: Settings, client: genai.Client) -> str:
    """One canned text turn, for --selftest. Returns what the model said."""
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.voice)
            ),
            language_code=settings.language,
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(parts=[types.Part(text=settings.system_instruction)]),
    )
    reply: list[str] = []
    async with client.aio.live.connect(model=settings.model, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user", parts=[types.Part(text="Reply with exactly: hello")]
            ),
            turn_complete=True,
        )
        async for message in session.receive():
            if getattr(message, "text", None):
                reply.append(message.text)
            server = getattr(message, "server_content", None)
            if server is not None:
                ot = getattr(server, "output_transcription", None)
                if ot and getattr(ot, "text", None):
                    reply.append(ot.text)
                if getattr(server, "turn_complete", False):
                    break
    return "".join(reply).strip()
