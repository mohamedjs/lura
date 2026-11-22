"""The composition root: the one place that knows every module.

Modules depend on each other's *packages* (their public API); only this file
constructs them and connects them, so a module never reaches for another's
infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .modules.air import AirController, AirService
from .modules.assistant import Assistant
from .modules.brain import BrainService
from .modules.computer import ComputerService, default_backend
from .modules.connectors import ConnectorService
from .modules.memory import MemoryService
from .modules.settings import SettingsService
from .modules.skills import SkillsService
from .modules.tools import ToolService
from .modules.updates import UpdateService
from .modules.voice import VoiceService
from .shared import desktop, events
from .shared.events import EventBus
from .shared.presence import Presence

log = logging.getLogger(__name__)


@dataclass
class Container:
    bus: EventBus
    settings: SettingsService
    connectors: ConnectorService
    tools: ToolService
    brain: BrainService
    voice: VoiceService
    memory: MemoryService
    skills: SkillsService
    updates: UpdateService
    assistant: Assistant
    computer: ComputerService
    air: AirService
    air_control: AirController


def build(presence: Presence | None = None) -> Container:
    bus = EventBus()
    settings = SettingsService(bus=bus)
    connectors = ConnectorService(bus=bus)
    tools = ToolService(connectors, language=lambda: settings.current.language)
    brain = BrainService(settings.key)
    voice = VoiceService(settings.key, settings.has_key)
    memory = MemoryService(lambda: settings.current.memory_enabled,
                           lambda: settings.current.context_budget_tokens,
                           lambda prompt: brain.complete(settings.current, prompt),
                           lambda: settings.current.user_name)
    skills = SkillsService()
    computer = ComputerService(
        default_backend(),
        launcher=lambda name: tools.dispatch("open_application", {"app_name": name})[0],
    )
    air = AirService(lambda name, payload: bus.publish(name, **payload),
                     camera=settings.current.air_camera,
                     thumb_click=settings.current.air_thumb_click)
    air_control = AirController(
        air,
        computer,
        lambda name, payload: bus.publish(name, **payload),
        app_kinds=lambda: settings.current.air_app_kinds,
    )
    from . import __version__
    updates = UpdateService(__version__, lambda: settings.current.check_updates,
                            lambda how: bus.publish(events.RESTART_REQUESTED if how == "restart"
                                                    else events.EXIT_REQUESTED))
    tools.add_provider(memory)
    tools.add_provider(skills)
    tools.add_provider(computer)
    tools.add_provider(air_control)
    assistant = Assistant(settings, brain, voice, tools, bus, presence, memory=memory,
                          extra_prompt=skills.prompt_block)

    def on_settings(payload: dict) -> None:
        if "start_at_login" in payload.get("changed", ()):
            desktop.apply_autostart(settings.current.start_at_login)

    bus.subscribe(events.SETTINGS_CHANGED, on_settings)
    return Container(bus, settings, connectors, tools, brain, voice, memory, skills, updates, assistant,
                     computer, air, air_control)
