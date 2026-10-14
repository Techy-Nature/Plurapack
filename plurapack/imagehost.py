"""Optional image hosting for avatars and banners.

Plurapack stores only an image URL. This module lets the dashboard turn a picture the
user picks into such a URL by uploading it to a Chevereto-style host. It is configured
with two service variables and is entirely absent when they are not set:

``PLURAPACK_IMAGE_HOST_KEY``  the host's API key (required to enable uploads)
``PLURAPACK_IMAGE_HOST_URL``  the upload endpoint (default: freeimage.host)
``PLURAPACK_IMAGE_MAX_BYTES`` the largest file accepted before shrinking (default: 20 MiB)
``PLURAPACK_IMAGE_COMPRESS_OVER`` shrink pictures bigger than this many bytes into WebP
                              (default: 1,000,000, i.e. 1 MB; 0 turns shrinking off)
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable

DEFAULT_ENDPOINT = "https://freeimage.host/api/1/upload"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_COMPRESS_OVER = 1_000_000
MAX_PIXELS = 60_000_000
MAX_FRAMES = 300
QUALITY_STEPS = (85, 78, 70, 62, 55, 48)
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


def shrink_image(data: bytes, target_bytes: int) -> tuple[bytes, str, str]:
    """Re-encode a picture as WebP, stepping quality and size down until it fits.

    Avatars and banners never need camera resolution, so large pictures are limited to
    2048 px on the long side (1024 px when animated). Metadata, including any GPS tags,
    is dropped. If nothing reaches ``target_bytes`` the smallest result is used, and the
    original is kept if every attempt came out bigger. Raises ``ImageHostError`` for a
    file Pillow cannot read.
    """
    try:
        from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
    except ImportError as error:  # Pillow is a declared dependency; this is a broken install
        raise ImageHostError("Image shrinking is unavailable on this server.", upstream=True) from error

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        source = Image.open(io.BytesIO(data))
        animated = bool(getattr(source, "is_animated", False))
        if animated and getattr(source, "n_frames", 1) > MAX_FRAMES:
            raise ImageHostError(f"Animated images can have at most {MAX_FRAMES} frames.")
        longest = 1024 if animated else 2048
        if animated:
            frames = [(frame.convert("RGBA"), frame.info.get("duration", 100))
                      for frame in ImageSequence.Iterator(source)]
            loop = source.info.get("loop", 0)
        else:
            flat = ImageOps.exif_transpose(source)
            has_alpha = flat.mode in ("RGBA", "LA", "PA") or "transparency" in flat.info
            frames = [(flat.convert("RGBA" if has_alpha else "RGB"), 0)]
            loop = 0
    except ImageHostError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ImageHostError("That image could not be read or is too large to process.") from error

    best = data
    scale = 1.0
    for _ in range(4):
        sized = []
        for frame, duration in frames:
            copy = frame.copy()
            side = max(1, int(longest * scale))
            copy.thumbnail((side, side), Image.LANCZOS)
            sized.append((copy, duration))
        for quality in QUALITY_STEPS:
            buffer = io.BytesIO()
            first, rest = sized[0][0], [frame for frame, _ in sized[1:]]
            if animated:
                first.save(buffer, "WEBP", quality=quality, save_all=True, append_images=rest,
                           duration=[duration for _, duration in sized], loop=loop, method=4)
            else:
                first.save(buffer, "WEBP", quality=quality, method=4)
            candidate = buffer.getvalue()
            if len(candidate) < len(best):
                best = candidate
            if len(candidate) <= target_bytes:
                return candidate, "webp", "image/webp"
        scale *= 0.75
    return (best, "webp", "image/webp") if best is not data else (data, *sniff_image(data))


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
    compress_over: int = DEFAULT_COMPRESS_OVER
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
        try:
            compress_over = int(os.getenv("PLURAPACK_IMAGE_COMPRESS_OVER", str(DEFAULT_COMPRESS_OVER)))
        except ValueError as error:
            raise ValueError("PLURAPACK_IMAGE_COMPRESS_OVER must be a whole number of bytes.") from error
        return cls(key, endpoint, maximum, max(0, compress_over))

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
        if self.compress_over and len(data) > self.compress_over:
            data, extension, mime = shrink_image(data, self.compress_over)
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
