"""Optional image hosting for avatars and banners.

Plurapack stores only an image URL. This module lets the dashboard turn a picture the
user picks into such a URL by uploading it to a Chevereto-style host. It is configured
with two service variables and is entirely absent when they are not set:

``PLURAPACK_IMAGE_HOST_KEY``  the host's API key (required to enable uploads)
``PLURAPACK_IMAGE_HOST_URL``  the upload endpoint (default: freeimage.host)
``PLURAPACK_IMAGE_MAX_BYTES`` the largest accepted upload (default: 8 MiB)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable

DEFAULT_ENDPOINT = "https://freeimage.host/api/1/upload"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30


class ImageHostError(Exception):
    """A user-presentable reason an upload could not be completed.

    ``upstream`` is true when the fault is the host's (unreachable, refused, bad reply)
    rather than the user's file.
    """

    def __init__(self, message: str, upstream: bool = False) -> None:
        super().__init__(message)
        self.upstream = upstream


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """Return ``(extension, mime)`` for the formats the host accepts, else ``None``.

    The browser-supplied file name and content type are never trusted; the bytes decide.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _multipart(fields: dict[str, str], filename: str, mime: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="source"; filename="{filename}"\r\n'
         f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _default_send(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read(1_000_000)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1_000_000)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ImageHostError("The image host could not be reached. Try again shortly.", upstream=True) from error


@dataclass(frozen=True)
class ImageHost:
    api_key: str
    endpoint: str = DEFAULT_ENDPOINT
    max_bytes: int = DEFAULT_MAX_BYTES
    send: Callable[[urllib.request.Request], tuple[int, bytes]] = _default_send

    @classmethod
    def from_environment(cls) -> "ImageHost | None":
        key = os.getenv("PLURAPACK_IMAGE_HOST_KEY", "").strip()
        if not key:
            return None
        endpoint = os.getenv("PLURAPACK_IMAGE_HOST_URL", "").strip() or DEFAULT_ENDPOINT
        if urllib.parse.urlsplit(endpoint).scheme != "https":
            raise ValueError("PLURAPACK_IMAGE_HOST_URL must be an https URL.")
        try:
            maximum = int(os.getenv("PLURAPACK_IMAGE_MAX_BYTES", str(DEFAULT_MAX_BYTES)))
        except ValueError as error:
            raise ValueError("PLURAPACK_IMAGE_MAX_BYTES must be a whole number of bytes.") from error
        return cls(key, endpoint, maximum)

    def upload(self, data: bytes) -> str:
        """Upload image bytes and return the hosted image's direct HTTPS URL."""
        if not data:
            raise ImageHostError("That file is empty.")
        if len(data) > self.max_bytes:
            raise ImageHostError(f"Images can be at most {self.max_bytes // (1024 * 1024)} MB.")
        detected = sniff_image(data)
        if detected is None:
            raise ImageHostError("Only PNG, JPEG, GIF, and WebP images can be uploaded.")
        extension, mime = detected
        body, content_type = _multipart(
            {"key": self.api_key, "action": "upload", "format": "json"},
            f"image.{extension}", mime, data,
        )
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Content-Type": content_type, "Accept": "application/json",
                     "User-Agent": "Plurapack"},
        )
        status, payload = self.send(request)
        try:
            document = json.loads(payload)
        except ValueError as error:
            raise ImageHostError("The image host sent an unreadable reply.", upstream=True) from error
        if status != 200 or not isinstance(document, dict):
            # The host's message can include request details; give only a safe summary.
            raise ImageHostError(f"The image host refused the upload (HTTP {status}).", upstream=True)
        image = document.get("image")
        url = image.get("url") if isinstance(image, dict) else None
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != "https":
            raise ImageHostError("The image host did not return a usable link.", upstream=True)
        return url
