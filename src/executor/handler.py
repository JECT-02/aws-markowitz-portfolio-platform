from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.adapters.alpaca import AlpacaPaperBroker
from common.adapters.secrets import load_alpaca_credentials
from common.clock import SystemClock
from common.config import ExecutorConfig, RuntimeSettings
from common.env import load_env_file
from common.errors import (
    AuthorizationError,
    BrokerError,
    ConfigurationError,
    DataValidationError,
    IdempotencyConflict,
    MarketClosed,
    NotFoundError,
    PortfolioError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
    StorageError,
)
from common.logging import configure_logging, get_logger
from executor.adapters.notifications import SnsResultNotifier
from executor.core.execution import RebalanceService
from executor.core.models import ExecutionResult
from executor.core.state import DynamoExecutionState

logger = get_logger(__name__)

_CONTENT_TYPE = "application/json"
_REBALANCE_ROUTE = "POST /rebalance"

_ALPACA_PAPER_ENV = "ALPACA_PAPER"
_ALPACA_API_KEY_PARAMETER_ENV = "ALPACA_API_KEY_PARAMETER"
# B105 false positive: the constant holds the *name* of an environment
# variable (an SSM parameter path), never the secret itself.
_ALPACA_SECRET_KEY_PARAMETER_ENV = "ALPACA_SECRET_KEY_PARAMETER"  # nosec B105
_NOTIFICATIONS_TOPIC_ENV = "NOTIFICATIONS_TOPIC_ARN"


@dataclass(frozen=True)
class ExecutorDependencies:
    service: RebalanceService
    config: ExecutorConfig
    notifier: SnsResultNotifier | None = None


def handler(event: Mapping[str, Any], _context: object | None = None) -> dict[str, Any]:
    """AWS Lambda entry point behind the authenticated rebalance endpoint."""
    load_env_file(Path(".env"))
    configure_logging()
    deps = build_dependencies()
    return run_executor(event, deps)


def run_executor(event: Mapping[str, Any], deps: ExecutorDependencies) -> dict[str, Any]:
    """Route one HTTP API v2 rebalance request to the execution service.

    Precondition: ``event`` follows the API Gateway HTTP API v2 payload.
    Postcondition: returns a Lambda proxy response; rejected executions map to
    4xx with safe messages and broker failures to 502.
    """
    route = event.get("routeKey")
    if route != _REBALANCE_ROUTE:
        return _response(404, {"message": f"no route for {route}"})
    try:
        _authorize(event, deps.config)
        proposal_id, idempotency_key = _parse_request(event)
        result = deps.service.execute(proposal_id, idempotency_key)
    except AuthorizationError as exc:
        # N-16 / AGENTS.md 6.1: authenticated but not authorized callers get
        # a 403 instead of reaching the execution flow.
        logger.warning("rebalance caller not authorized", extra={"route": route})
        return _response(403, {"message": str(exc)})
    except NotFoundError as exc:
        return _response(404, {"message": str(exc)})
    except DataValidationError as exc:
        return _response(400, {"message": str(exc)})
    except (ProposalExpired, ProposalNotPending, MarketClosed, PriceDriftExceeded) as exc:
        return _response(409, {"message": str(exc)})
    except IdempotencyConflict as exc:
        # The key is claimed by another in-flight execution: the caller must
        # not retry blindly (AGENTS.md section 6.2).
        return _response(409, {"message": str(exc)})
    except BrokerError as exc:
        logger.exception("broker rejected the execution", extra={"route": route})
        return _response(502, {"message": str(exc)})
    except (StorageError, ConfigurationError):
        logger.exception("executor request failed", extra={"route": route})
        return _response(500, {"message": "internal error"})
    except PortfolioError as exc:
        logger.exception("executor request failed", extra={"route": route})
        return _response(500, {"message": str(exc)})
    except Exception:
        logger.exception("unexpected executor failure", extra={"route": route})
        return _response(500, {"message": "internal error"})
    _notify(deps, result, route)
    logger.info(
        "rebalance executed",
        extra={
            "proposal_id": result.proposal_id,
            "status": result.status.value,
        },
    )
    return _response(200, result.model_dump(mode="json"))


def _notify(
    deps: ExecutorDependencies,
    result: ExecutionResult,
    route: str,
) -> None:
    """Publish the final outcome to SNS; notification failures never mask it.

    AGENTS.md section 6.13 requires notifying every final status; a failed
    publish is logged at ERROR level with context but the execution result
    already reached the caller.
    """
    if deps.notifier is None:
        return
    try:
        deps.notifier.notify(result)
    except PortfolioError:
        logger.exception(
            "rebalance notification failed",
            extra={"route": route, "proposal_id": result.proposal_id},
        )


def build_dependencies() -> ExecutorDependencies:
    """Build the execution dependencies from the environment."""
    runtime = RuntimeSettings.from_environment()
    config = ExecutorConfig.from_environment()
    table_name = os.environ.get("STATE_TABLE")
    if table_name is None or not table_name.strip():
        raise ConfigurationError("missing environment variable: STATE_TABLE")
    state = DynamoExecutionState(table_name.strip(), region_name=runtime.aws_region)
    broker = _build_broker(runtime)
    service = RebalanceService(
        state=state,
        broker=broker,
        config=config,
        clock=SystemClock(),
    )
    topic_arn = os.environ.get(_NOTIFICATIONS_TOPIC_ENV, "")
    notifier = (
        SnsResultNotifier(
            topic_arn=topic_arn,
            region_name=runtime.aws_region,
        )
        if topic_arn.strip()
        else None
    )
    return ExecutorDependencies(service=service, config=config, notifier=notifier)


def _authorize(event: Mapping[str, Any], config: ExecutorConfig) -> None:
    """Reject callers outside the configured Cognito groups (N-16, 6.1).

    Precondition: API Gateway's JWT authorizer authenticated the token and
    forwarded the claims under ``requestContext.authorizer.jwt.claims``.
    Postconditions: silent when the caller belongs to at least one allowed
    group; ``AuthorizationError`` otherwise (fail closed on missing claims
    and on an empty allowlist).
    """
    request_context = event.get("requestContext")
    authorizer = request_context.get("authorizer") if isinstance(request_context, Mapping) else None
    jwt = authorizer.get("jwt") if isinstance(authorizer, Mapping) else None
    claims = jwt.get("claims") if isinstance(jwt, Mapping) else None
    if not isinstance(claims, Mapping) or not claims:
        raise AuthorizationError("the request is not authenticated")
    if not config.allowed_groups:
        raise AuthorizationError("no executor group is configured; approvals are disabled")
    raw_groups = claims.get("cognito:groups", [])
    if isinstance(raw_groups, str):
        groups = {group.strip() for group in raw_groups.split(",") if group.strip()}
    elif isinstance(raw_groups, list):
        groups = {str(group).strip() for group in raw_groups if str(group).strip()}
    else:
        raise AuthorizationError("unsupported group claim in the access token")
    if not groups.intersection(config.allowed_groups):
        logger.warning(
            "caller is not in an allowed executor group",
            extra={"groups": sorted(groups)},
        )
        raise AuthorizationError("the caller is not authorized to approve rebalances")


def _build_broker(runtime: RuntimeSettings) -> AlpacaPaperBroker:
    """Build the Alpaca paper trading adapter from the environment.

    Precondition: ALPACA_PAPER is exactly "true" (Terraform writes it as an
    immutable literal) and both SSM parameter paths are configured.
    Postcondition: the returned broker refuses any non-paper endpoint.
    """
    paper = os.environ.get(_ALPACA_PAPER_ENV, "")
    if paper.strip().lower() != "true":
        raise ConfigurationError(f"{_ALPACA_PAPER_ENV} must be 'true'; live trading is forbidden")
    api_key_parameter = os.environ.get(_ALPACA_API_KEY_PARAMETER_ENV, "")
    secret_key_parameter = os.environ.get(_ALPACA_SECRET_KEY_PARAMETER_ENV, "")
    if not api_key_parameter.strip() or not secret_key_parameter.strip():
        raise ConfigurationError(
            f"missing environment variables: {_ALPACA_API_KEY_PARAMETER_ENV}, "
            f"{_ALPACA_SECRET_KEY_PARAMETER_ENV}"
        )
    credentials = load_alpaca_credentials(
        api_key_parameter=api_key_parameter.strip(),
        secret_key_parameter=secret_key_parameter.strip(),
        region_name=runtime.aws_region,
    )
    return AlpacaPaperBroker(
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        paper=True,
    )


def _parse_request(event: Mapping[str, Any]) -> tuple[str, str]:
    headers = event.get("headers") or {}
    idempotency_key = None
    for name, value in headers.items():
        if name.lower() == "idempotency-key":
            idempotency_key = value
            break
    if not idempotency_key or not str(idempotency_key).strip():
        raise DataValidationError("the idempotency-key header is required")
    body = event.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else {}
    except json.JSONDecodeError as exc:
        raise DataValidationError("the request body must be valid json") from exc
    if not isinstance(payload, dict):
        raise DataValidationError("the request body must be a json object")
    proposal_id = payload.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise DataValidationError("proposal_id is required")
    return proposal_id.strip(), str(idempotency_key).strip()


def _response(status_code: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": _CONTENT_TYPE},
        "body": json.dumps(dict(payload), default=str),
    }
