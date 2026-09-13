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


def build_config(settings: Settings, history: str = "") -> types.LiveConnectConfig:
    from .tools import GEMINI_TOOLS, get_machine_context

    system_instruction = f"{settings.system_instruction}\n{get_machine_context()}"
    if history:
        system_instruction += (
            "\n\n[Conversation so far — you are resuming mid-conversation]\n"
            + history
            + "\n[Continue naturally from here. Do not repeat greetings or "
            "re-introduce yourself.]"
        )
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
    """A persistent voice conversation, wake-to-goodbye.

    The Gemini Live server may close the WebSocket after each turn.  This
    class auto-reconnects transparently so the user experiences one
    uninterrupted session until they say "bye" or the hard time cap fires.
    """

    def __init__(self, settings: Settings, client: genai.Client):
        self.settings = settings
        self.client = client
        self._speaking = asyncio.Event()
        self._ending = False
        self._last_voice = time.monotonic()
        self._started = time.monotonic()
        # Conversation transcript for context across reconnections
        self._transcript: list[str] = []
        self._user_buf: list[str] = []
        self._model_buf: list[str] = []

    def _flush_user(self) -> None:
        """Finalize accumulated user speech fragments into the transcript."""
        if self._user_buf:
            text = "".join(self._user_buf).strip()
            if text:
                self._transcript.append(f"User: {text}")
            self._user_buf.clear()

    def _flush_model(self) -> None:
        """Finalize accumulated model speech fragments into the transcript."""
        if self._model_buf:
            text = "".join(self._model_buf).strip()
            if text:
                self._transcript.append(f"Assistant: {text}")
            self._model_buf.clear()

    def _get_history(self) -> str:
        """Return the conversation transcript, capped at ~4000 chars."""
        if not self._transcript:
            return ""
        full = "\n".join(self._transcript)
        if len(full) > 4000:
            full = "…" + full[-4000:]
        return full

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
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except Exception as exc:
                    log.warning("Audio playback write warning: %s", exc)
        except Exception as exc:
            log.exception("_play task failed: %s", exc)
            raise
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
        try:
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
                            # New user speech → flush any pending model turn
                            if self._model_buf:
                                self._flush_model()
                            self._user_buf.append(text)
                    if getattr(server, "output_transcription", None):
                        text = server.output_transcription.text
                        if text:
                            log.info("gemini: %s", text)
                            # Model speaking → flush any pending user turn
                            if self._user_buf:
                                self._flush_user()
                            self._model_buf.append(text)
                    if getattr(server, "interrupted", False):
                        # The model was cut off: drop whatever is still queued so
                        # the old answer does not keep playing over the new one.
                        while not chunks.empty():
                            chunks.get_nowait()
                        self._speaking.clear()
                        self._flush_model()
                    if getattr(server, "turn_complete", False):
                        self._flush_model()
                        await chunks.put(None)
            # Session closed — flush any remaining buffers
            self._flush_user()
            self._flush_model()
            log.info("session.receive() completed.")
        except Exception as exc:
            self._flush_user()
            self._flush_model()
            log.exception("_receive task failed: %s", exc)
            raise

    async def _watchdog(self) -> None:
        """End the conversation on explicit ending or hard cap only."""
        log.info("Watchdog started (session stays open until 'bye' or %.0fs cap)",
                 self.settings.max_session_seconds)
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if self._ending and not self._speaking.is_set():
                log.info("Session ended by user request.")
                return
            if now - self._started > self.settings.max_session_seconds:
                log.info("Session hit its time cap.")
                return

    async def run(self) -> None:
        chunks: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        watchdog = asyncio.create_task(self._watchdog(), name="watchdog")
        play_task = asyncio.create_task(self._play(chunks), name="play")

        try:
            while not self._ending and not watchdog.done():
                config = build_config(self.settings, history=self._get_history())
                log.info("Opening Gemini Live session… (history: %d turns)",
                         len(self._transcript))
                try:
                    async with self.client.aio.live.connect(
                        model=self.settings.model, config=config
                    ) as session:
                        mic = asyncio.create_task(
                            self._pump_mic(session, loop), name="pump_mic"
                        )
                        recv = asyncio.create_task(
                            self._receive(session, chunks), name="receive"
                        )

                        done, _ = await asyncio.wait(
                            [mic, recv, watchdog],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        mic.cancel()
                        recv.cancel()
                        await asyncio.gather(mic, recv, return_exceptions=True)

                        if watchdog in done:
                            break  # user said bye or hit time cap

                        # Log why the session closed
                        for t in done:
                            exc = t.exception() if not t.cancelled() else None
                            if exc:
                                log.warning("Task '%s' failed: %s", t.get_name(), exc)
                            else:
                                log.info("Task '%s' completed, server closed session.",
                                         t.get_name())

                except Exception as exc:
                    log.warning("Session error: %s", exc)

                if self._ending or watchdog.done():
                    break

                # Reset state before transparent reconnect
                self._speaking.clear()
                log.info("Reconnecting in 1s…")
                await asyncio.sleep(1)

        finally:
            watchdog.cancel()
            play_task.cancel()
            await asyncio.gather(watchdog, play_task, return_exceptions=True)


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
