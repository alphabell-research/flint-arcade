FROM python:3.11.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir fastapi==0.159.1 httpx==0.28.1 uvicorn==0.54.0 \
    && useradd --create-home --uid 1000 arcade
WORKDIR /app
COPY --chown=arcade:arcade proxy.py /app/proxy.py
USER 1000
EXPOSE 7860
CMD ["uvicorn", "proxy:create_app", "--factory", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
