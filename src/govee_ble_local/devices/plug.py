"""Govee smart-plug family (``com.govee.h5080``).

On/off only. Uses the relay power encoding (0x10/0x11), requires the
secret-key check after handshake, and needs a sync-time follow-up after every
power command for the relay to actuate.
"""
from __future__ import annotations

from typing import ClassVar

from ..models import Capability
from .base import PowerMixin


class GoveePlug(PowerMixin):
    """H5080 family smart plug (H5080/H5082/H5083/H5085/H5089/H5160/H5161)."""

    skus: ClassVar[tuple[str, ...]] = (
        "H5080",
        "H5082",
        "H5083",
        "H5085",
        "H5089",
        "H5160",
        "H5161",
    )
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.POWER})
    requires_secret: ClassVar[bool] = True
    _relay_power: ClassVar[bool] = True
