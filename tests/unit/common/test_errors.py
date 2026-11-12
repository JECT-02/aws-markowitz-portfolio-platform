from common.errors import (
    ConfigurationError,
    DataValidationError,
    ExternalSourceError,
    PortfolioError,
    StorageError,
)


def test_domain_errors_inherit_from_portfolio_error() -> None:
    error_types = (ConfigurationError, DataValidationError, ExternalSourceError, StorageError)
    for error_type in error_types:
        assert issubclass(error_type, PortfolioError)
