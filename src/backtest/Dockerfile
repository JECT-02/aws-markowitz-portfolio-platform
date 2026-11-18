FROM public.ecr.aws/lambda/python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR ${LAMBDA_TASK_ROOT}

COPY build/requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src/common ./common
COPY src/optimizer ./optimizer
COPY src/backtest ./backtest
COPY config ./config

RUN python -c "import cvxpy" \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser ${LAMBDA_TASK_ROOT}

USER appuser

CMD ["backtest.handler.handler"]
