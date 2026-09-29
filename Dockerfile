# Production Multi-Stage Hardened Container for Trad-Auto
FROM python:3.12-slim AS runtime

# Set security and runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs

# Create unprivileged system user and group (Defense in Depth)
RUN groupadd -g 10001 tradauto && \
    useradd -u 10001 -g tradauto -m -s /sbin/nologin -d /home/tradauto tradauto

# Create app and persistence directories
WORKDIR /app
RUN mkdir -p /app/data /app/logs && \
    chown -R tradauto:tradauto /app

# Install minimal production dependencies
COPY --chown=tradauto:tradauto pyproject.toml /app/
RUN pip install --no-cache-dir "pydantic>=2.8.0,<3.0.0" "pydantic-settings>=2.4.0,<3.0.0"

# Copy application modules
COPY --chown=tradauto:tradauto config /app/config
COPY --chown=tradauto:tradauto trad_auto /app/trad_auto

# Switch to non-root execution
USER 10001:10001

# Declare persistent data volumes
VOLUME ["/app/data", "/app/logs"]

# Built-in container health check querying subsystem vitals
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -m trad_auto.health || exit 1

# Production default command: daemon execution
ENTRYPOINT ["python", "-m", "trad_auto.main"]
CMD ["--daemon"]
