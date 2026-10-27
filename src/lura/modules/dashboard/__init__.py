"""The dashboard: a local web page to set keys, models, voice, language and connectors."""

from .api import DashboardApi
from .server import DashboardServer, running_url

__all__ = ["DashboardApi", "DashboardServer", "running_url"]
