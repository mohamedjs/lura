"""The dashboard: a local web page to set keys, models, voice, language and connectors."""

from .api import DashboardApi
from .server import DashboardServer, running_url
from .window import launch as open_window, run as run_window, show as show_window

__all__ = ["DashboardApi", "DashboardServer", "running_url", "open_window", "run_window", "show_window"]
