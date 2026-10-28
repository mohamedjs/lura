"""Skills: instructions (and files) the user adds, usable with every brain."""

from .application import SkillsService
from .domain import Skill, parse

__all__ = ["SkillsService", "Skill", "parse"]
