"""The composition root: the one place that knows every module.

Modules depend on each other's *packages* (their public API); only this file
constructs them and connects them, so a module never reaches for another's
infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .modules.assistant import Assistant
from .modules.billing import BillingService
from .modules.brain import BrainService
from .modules.connectors import ConnectorService
from .modules.memory import MemoryService
from .modules.settings import SettingsService
from .modules.skills import SkillsService
from .modules.tools import ToolService
from .modules.voice import VoiceService
from .shared import desktop, events
from .shared.events import EventBus
from .shared.presence import Presence

log = logging.getLogger(__name__)


@dataclass
class Container:
    bus: EventBus
    settings: SettingsService
    billing: BillingService
    connectors: ConnectorService
    tools: ToolService
    brain: BrainService
    voice: VoiceService
    memory: MemoryService
    skills: SkillsService
    assistant: Assistant


def build(presence: Presence | None = None) -> Container:
    bus = EventBus()
    settings = SettingsService(bus=bus)
    billing = BillingService(bus=bus)
    connectors = ConnectorService(billing.is_pro, bus=bus)
    tools = ToolService(connectors, language=lambda: settings.current.language)
    brain = BrainService(settings.key)
    voice = VoiceService(settings.key, settings.has_key)
    memory = MemoryService(lambda: settings.current.memory_enabled,
                           lambda: settings.current.context_budget_tokens,
                           lambda prompt: brain.complete(settings.current, prompt),
                           lambda: settings.current.user_name)
    skills = SkillsService()
    tools.add_provider(memory)
    tools.add_provider(skills)
    assistant = Assistant(settings, brain, voice, tools, bus, presence, memory=memory,
                          extra_prompt=skills.prompt_block)

    bus.subscribe(events.PLAN_CHANGED, lambda _: connectors.refresh_plan())

    def on_settings(payload: dict) -> None:
        if "start_at_login" in payload.get("changed", ()):
            desktop.apply_autostart(settings.current.start_at_login)

    bus.subscribe(events.SETTINGS_CHANGED, on_settings)
    return Container(bus, settings, billing, connectors, tools, brain, voice, memory, skills, assistant)
