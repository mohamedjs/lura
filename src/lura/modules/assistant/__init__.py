"""The assistant: orchestrates wake word, brain, voice and tools into a conversation."""

from .application import Assistant, PipelineConversation
from .domain import briefing_prompt, language_rule, system_prompt, trim_history

__all__ = ["Assistant", "PipelineConversation", "briefing_prompt", "language_rule",
           "system_prompt", "trim_history"]
