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


class SolverError(PortfolioError):
    """Raised when the numerical solver fails or returns invalid output."""


class InfeasibleError(PortfolioError):
    """Raised when the optimization problem has no feasible solution."""


class StorageError(PortfolioError):
    """Raised when a storage operation fails."""


class SecretError(PortfolioError):
    """Raised when a secret cannot be read or is empty."""


class IdempotencyConflict(PortfolioError):
    """Raised when a conditional state write finds an existing item."""


class NotFoundError(PortfolioError):
    """Raised when a requested artifact or state does not exist."""


class BrokerError(PortfolioError):
    """Raised when the broker rejects or fails a trading operation."""


class ProposalExpired(PortfolioError):
    """Raised when a proposal is no longer valid for execution."""


class ProposalNotPending(PortfolioError):
    """Raised when a proposal is not in the PENDING state for execution."""


class MarketClosed(PortfolioError):
    """Raised when order submission is attempted while the market is closed."""


class PriceDriftExceeded(PortfolioError):
    """Raised when current prices deviate beyond the configured threshold."""
