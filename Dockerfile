FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src/ ./src/
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BUDDY_HOSTED=1 BUDDY_DATA=/data BUDDY_PORT=8000
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tesseract-ocr libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 buddy \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home buddy \
    && install -d -m 0700 -o buddy -g buddy /data
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY sample/ ./sample/
COPY --from=frontend /build/dist ./frontend/dist
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=30s CMD ["python", "-c", "import os, urllib.request; port = int(os.getenv('PORT') or os.getenv('BUDDY_PORT', '8000')); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=3).close()"]
CMD ["sh", "-c", "umask 077; exec python -m backend.launch"]
