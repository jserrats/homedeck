# HomeDeck container image.
# Multi-arch (built for linux/amd64 + linux/arm64 in CI); runs on a Raspberry Pi
# with a 64-bit OS. The Stream Deck is a USB HID device, so the container needs
# USB access (see docker-compose.yml).
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="HomeDeck" \
      org.opencontainers.image.description="Control Home Assistant from an Elgato Stream Deck XL" \
      org.opencontainers.image.source="https://github.com/OWNER/homedeck"

# System libraries the python-elgato-streamdeck backend needs to talk to USB HID.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 \
        libhidapi-libusb0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code, then vendor the fonts/icons (downloaded at build time).
COPY homedeck ./homedeck
COPY scripts ./scripts
RUN python scripts/fetch_assets.py

# Run as a module from /app (no editable install needed).
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "homedeck"]
