# 0001 - Container images for the ingest and clean Lambdas

Status: accepted (human approved in the Phase 3 implementation conversation).

## Context

The Phase 3 plan specified zip deployment packages for the ingest and clean Lambdas, built from
per-function `requirements.txt` files. Packaging was implemented and measured on 2026-11-12:

| Package | Zipped | Unzipped |
|---|---|---|
| `ingest.zip` | 106 MB | 347 MB |
| `clean.zip` | 84 MB | ~280 MB |

AWS Lambda limits zip deployment packages to 50 MB zipped and 250 MB unzipped, including layers.
`pyarrow` alone is 165 MB unzipped in the manylinux wheel, and `clean` requires it to write
Silver Parquet. Removing `pyarrow` is not possible without changing the storage contract.
A shared layer would still exceed the 250 MB unzipped limit.

## Decision

Deploy ingest, clean and optimizer as container images on `public.ecr.aws/lambda/python:3.12`,
with one ECR repository per function and the image built and pushed outside CDK. CDK references
`DockerImageCode.from_ecr(repository, tag_or_digest=..., cmd=[...])`.

Images use `x86_64`, a non-root user, pinned requirements copied from each function directory,
and a build-time import check.

## Alternatives considered

- Keep zips and drop `pyarrow`: breaks the Silver Parquet contract and the tests that enforce it.
- Shared Lambda layer: pyarrow + pandas + numpy exceed the 250 MB unzipped layer limit.
- Switch the Parquet writer: changes the serialized format and the pinned dependency set.
- Slim wheels built from source: adds build tooling and a long, fragile build for no contract gain.

## Consequences

- The 50 MB zip limit no longer applies; images are pulled from ECR at cold start.
- Three ECR repositories are added and must keep images under the 14-day untagged lifecycle rule.
  Estimated cost stays below one dollar per month for this project's image sizes.
- The deployment order changes: images must be pushed before `cdk deploy` creates or updates the
  functions, otherwise the image tag is missing.
- `make build-images` builds all three images locally; the CI workflow builds them and verifies
  that `ingest.handler`, `clean.handler` and `optimizer.handler` import inside the containers.
- The plan's "zip and Docker bundling" step is superseded by this ADR; all other Phase 3
  decisions remain unchanged.
