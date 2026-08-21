"""SQLAlchemy model definitions.

Importing this package exposes all models on the ``db`` declarative base so
Flask-Migrate / Alembic autogenerate sees the complete schema.
"""
from .user import User, Role
from .device_type import DeviceType
from .device import Device
from .profile import (
    ConfigurationProfile,
    ConfigurationTemplate,
    InterfaceTemplate,
    ConfigurationVariable,
    validate_profile_name,
    validate_variable_key,
)
from .deployment import DeploymentRecord, DeploymentDevice, ZTPProvision
from .credential import ServiceCredential
from .setting import SystemSetting

__all__ = [
    "User",
    "Role",
    "DeviceType",
    "Device",
    "ConfigurationProfile",
    "ConfigurationTemplate",
    "InterfaceTemplate",
    "ConfigurationVariable",
    "validate_profile_name",
    "validate_variable_key",
    "DeploymentRecord",
    "DeploymentDevice",
    "ZTPProvision",
    "ServiceCredential",
    "SystemSetting",
]
