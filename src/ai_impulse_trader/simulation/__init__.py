"""Simulation-only adapters excluded from production runtime imports."""

from .broker import BrokerSimulator

__all__ = ["BrokerSimulator"]
