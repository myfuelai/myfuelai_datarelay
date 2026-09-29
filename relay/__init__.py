"""MyFuel.AI Connector - Windows service that relays data between on-site systems and MyFuel.

Each integration lives in relay/integrations/<name>/ and exposes build_tasks(ctx). Whether an
integration actually does anything is decided by the MyFuel database (see each module), not by
local config.
"""
__version__ = "2.0.0"
