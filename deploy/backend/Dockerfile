# Python backend (free check API, report serving, Stripe webhook). Includes Chromium for the JS-render + PDF step.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV AIV_CHROME=/usr/bin/chromium PYTHONUNBUFFERED=1 AIV_ENV=production AIV_ENGINE_MODE=gemini-only
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aiv aiv
COPY landing landing
COPY scripts scripts
EXPOSE 8787
CMD ["sh", "-c", "python -m aiv serve --host 0.0.0.0 --port ${PORT:-8787}"]
