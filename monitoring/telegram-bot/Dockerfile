FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --disable-pip-version-check .
USER 10001:10001
CMD ["python", "-m", "router_monitor_bot"]
