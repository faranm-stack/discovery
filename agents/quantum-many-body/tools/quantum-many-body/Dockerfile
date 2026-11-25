FROM python:3.12-slim-bookworm@sha256:44cf1793efa7c7a476bb0f2cff81443f4a072c90c1cb127e2924933daa3e8c64
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r requirements.txt \
    && mkdir -p /input /output && chown 10001:10001 /output
COPY reference.py action.py ./
USER 10001:10001
ENTRYPOINT ["python", "/app/action.py"]