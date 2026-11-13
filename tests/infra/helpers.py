from __future__ import annotations

from infra.config import DeployConfig


def deploy_config(
    *,
    notification_email: str | None = None,
    spa_allowed_origin: str | None = None,
) -> DeployConfig:
    """Build a deterministic deploy configuration for template tests."""
    return DeployConfig(
        env_name="test",
        owner="portfolio-team",
        cost_center="university",
        code_version="test-sha",
        image_tag="test",
        notification_email=notification_email,
        api_key_parameter="/portfolio-markowitz/test/alpaca/api-key",
        secret_key_parameter="/portfolio-markowitz/test/alpaca/secret-key",
        strategy_environment={
            "RISK_AVERSION": "2.0",
            "TRANSACTION_COST_LAMBDA": "0.001",
            "RETURN_SHRINKAGE_ALPHA": "0.5",
            "RISK_FREE_RATE_ANNUAL": "0.04",
            "MAX_FORWARD_FILL_DAYS": "2",
            "WINDOW_DAYS": "252",
            "INGEST_LOOKBACK_DAYS": "420",
            "REBALANCE_L1_THRESHOLD": "0.10",
            "VALIDATION_GAMMAS": "1.0,2.0",
            "VALIDATION_LAMBDAS": "0.0,0.001",
            "VALIDATION_ALPHAS": "0.0,0.5",
            "VALIDATION_WINDOWS": "126,252",
        },
        spa_allowed_origin=spa_allowed_origin,
    )
