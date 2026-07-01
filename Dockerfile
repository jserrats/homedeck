# HomeDeck container image.
# Multi-arch: linux/amd64, linux/arm64 (64-bit Raspberry Pi OS) and linux/arm/v7
# (32-bit Raspberry Pi OS). The Stream Deck is a USB HID device, so the container
# needs USB access (see docker-compose.yml).
#
# Python 3.11 (not 3.12) is used so that 32-bit ARM can install prebuilt wheels
# from piwheels — Raspberry Pi OS ships Python 3.11, which is what piwheels
# builds for. Without it, armv7 would compile pydantic-core (Rust) and Pillow
# from source under emulation.
FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="HomeDeck" \
      org.opencontainers.image.description="Control Home Assistant from an Elgato Stream Deck XL" \
      org.opencontainers.image.source="https://github.com/jserrats/homedeck"

# Runtime libraries:
#  - libusb / libhidapi: the python-elgato-streamdeck USB HID backend
#  - the rest: shared libs the piwheels Pillow wheels link against (the PyPI
#    manylinux wheels used on amd64/arm64 bundle these; piwheels' 32-bit wheels
#    don't, so _imaging / _imagingft need them present at runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 \
        libhidapi-libusb0 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libtiff6 \
        libwebp7 \
        libwebpdemux2 \
        libwebpmux3 \
        liblcms2-2 \
        libfreetype6 \
        libharfbuzz0b \
        libfribidi0 \
        libimagequant0 \
        libraqm0 \
        libxcb1 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# piwheels provides prebuilt armv7 wheels; it's ignored on amd64/arm64 (those
# use PyPI's manylinux wheels). Install deps first so the layer caches.
COPY requirements.txt ./
RUN pip install --no-cache-dir \
        --extra-index-url https://www.piwheels.org/simple \
        -r requirements.txt

# App code, then vendor the fonts/icons (downloaded at build time).
COPY homedeck ./homedeck
COPY scripts ./scripts
RUN python scripts/fetch_assets.py

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "homedeck"]
