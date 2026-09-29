# Production Multi-Stage Hardened Container for Trad-Auto & Hugging Face Spaces
FROM python:3.12-slim AS runtime

# Set security and runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs \
    WEBHOOK_PORT=7860 \
    WEBHOOK_HOST=0.0.0.0 \
    TRADING_MODE=PAPER \
    ENABLE_WEB_DASHBOARD=true

# Create unprivileged system user (Hugging Face Spaces runs as user 1000)
RUN useradd -m -u 1000 user

# Create app and persistence directories
WORKDIR /app
RUN mkdir -p /app/data /app/data/models /app/logs && \
    chown -R user:user /app

# Install production dependencies
COPY --chown=user:user pyproject.toml /app/
RUN pip install --no-cache-dir \
    "pydantic>=2.8.0,<3.0.0" \
    "pydantic-settings>=2.4.0,<3.0.0" \
    "flask>=3.0.0,<4.0.0" \
    "numpy>=1.26.0,<3.0.0" \
    "scipy>=1.12.0,<2.0.0" \
    "scikit-learn>=1.4.0,<2.0.0" \
    "joblib>=1.3.0,<2.0.0"

# Copy application modules and trained models
COPY --chown=user:user config /app/config
COPY --chown=user:user trad_auto /app/trad_auto
COPY --chown=user:user data/models /app/data/models

# Switch to non-root execution
USER user

# Declare persistent data volumes
VOLUME ["/app/data", "/app/logs"]

# Expose Hugging Face Spaces port
EXPOSE 7860

# Built-in container health check querying subsystem vitals
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -m trad_auto.health || exit 1

# Production default command: daemon execution
ENTRYPOINT ["python", "-m", "trad_auto.main"]
CMD ["--daemon"]
