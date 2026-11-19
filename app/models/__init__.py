"""Database models.

Import every model here so Flask-Migrate can discover them when it
inspects the app for schema changes.
"""

from app.models.admin_action import AdminAction
from app.models.device_event import DeviceEvent
from app.models.energy_reading import EnergyReading
from app.models.relay_command import RelayCommand
from app.models.smart_outlet import SmartOutlet
from app.models.user import User
from app.models.wifi_credential import WifiCredential

__all__ = [
    "User",
    "SmartOutlet",
    "DeviceEvent",
    "AdminAction",
    "RelayCommand",
    "EnergyReading",
    "WifiCredential",
]
