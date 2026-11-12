from __future__ import annotations


class PortfolioError(Exception):
    """Base class for portfolio domain errors."""


class ConfigurationError(PortfolioError):
    """Raised when configuration is missing or invalid."""


class DataValidationError(PortfolioError):
    """Raised when market data violates a quality invariant."""


class ExternalSourceError(PortfolioError):
    """Raised when an upstream market data source fails."""


class MarketDataUnavailableError(ExternalSourceError):
    """Raised when every configured market data source fails."""


class StorageError(PortfolioError):
    """Raised when a storage operation fails."""
