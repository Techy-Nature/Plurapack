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
import struct
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


# ---- metadata removal -------------------------------------------------------------------
# The image host keeps whatever is in the file, so Plurapack removes metadata itself before
# every upload. This works on the file's structure and leaves the picture data untouched,
# except for photos that are only upright because of an orientation tag: those are
# rotated and re-encoded so they still display the right way up.

_JPEG_KEEP_APP = {0xE0, 0xEE}            # JFIF, Adobe (colour transform)
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
_GIF_KEEP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


def _unreadable(error: Exception | None = None) -> ImageHostError:
    failure = ImageHostError("That image could not be read or is too large to process.")
    if error is not None:
        failure.__cause__ = error
    return failure


def _orientation(data: bytes) -> int:
    """The EXIF orientation tag (1 = upright), read with Pillow when it is available."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            return int(image.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 - unreadable tags simply mean "no rotation"
        return 1


def _upright_reencode(data: bytes, image_format: str) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as source:
        icc = source.info.get("icc_profile")
        upright = ImageOps.exif_transpose(source)
        buffer = io.BytesIO()
        options = {"icc_profile": icc} if icc else {}
        if image_format == "JPEG":
            upright.convert("RGB").save(buffer, "JPEG", quality=92, **options)
        else:
            upright.save(buffer, "PNG", **options)
        return buffer.getvalue()


def _strip_jpeg(data: bytes) -> bytes:
    out, i, n = bytearray(b"\xff\xd8"), 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise _unreadable()
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            raise _unreadable()
        marker = data[i]
        i += 1
        if marker == 0xD9:                   # end of image: drop any trailer after it
            out += b"\xff\xd9"
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += bytes((0xFF, marker))
            continue
        if i + 2 > n:
            raise _unreadable()
        length = struct.unpack(">H", data[i:i + 2])[0]
        segment = data[i + 2:i + length]
        if length < 2 or i + length > n:
            raise _unreadable()
        keep = (marker not in range(0xE0, 0xF0) and marker != 0xFE) or marker in _JPEG_KEEP_APP \
            or (marker == 0xE2 and segment.startswith(b"ICC_PROFILE\x00"))
        if keep:
            out += bytes((0xFF, marker)) + data[i:i + length]
        i += length
        if marker == 0xDA:                   # start of scan: copy entropy data to the next marker
            start = i
            while i + 1 < n and not (data[i] == 0xFF and data[i + 1] not in (0x00, *range(0xD0, 0xD8))):
                i += 1
            if i + 1 >= n:
                raise _unreadable()
            out += data[start:i]
    raise _unreadable()


def _strip_png(data: bytes) -> bytes:
    out, i = bytearray(data[:8]), 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            raise _unreadable()
        if kind not in _PNG_DROP:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            return bytes(out)
    raise _unreadable()


def _strip_webp(data: bytes) -> bytes:
    chunks, i, flags_at = bytearray(), 12, None
    while i + 8 <= len(data):
        kind = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + size + (size & 1)
        if i + 8 + size > len(data):
            raise _unreadable()
        if kind not in (b"EXIF", b"XMP "):
            if kind == b"VP8X":
                flags_at = len(chunks) + 8
            chunks += data[i:min(end, len(data))]
        i = end
    if flags_at is not None:
        chunks[flags_at] &= ~(0x08 | 0x04) & 0xFF   # clear the EXIF and XMP flags
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WEBP" + bytes(chunks)


def _gif_subblocks(data: bytes, i: int) -> int:
    while True:
        if i >= len(data):
            raise _unreadable()
        size = data[i]
        i += 1 + size
        if size == 0:
            return i


def _strip_gif(data: bytes) -> bytes:
    if len(data) < 13:
        raise _unreadable()
    packed = data[10]
    i = 13 + (3 * 2 ** ((packed & 7) + 1) if packed & 0x80 else 0)
    out = bytearray(data[:i])
    while i < len(data):
        block = data[i]
        if block == 0x3B:                    # trailer: drop anything after it
            return bytes(out + b";")
        if block == 0x21:
            label = data[i + 1] if i + 1 < len(data) else None
            end = _gif_subblocks(data, i + 2)
            app_id = data[i + 3:i + 14] if label == 0xFF else b""
            if label != 0xFE and (label != 0xFF or app_id in _GIF_KEEP_APPS):
                out += data[i:end]
            i = end
        elif block == 0x2C:
            if i + 10 > len(data):
                raise _unreadable()
            local = data[i + 9]
            body = i + 10 + (3 * 2 ** ((local & 7) + 1) if local & 0x80 else 0)
            end = _gif_subblocks(data, body + 1)   # skip the LZW minimum code size
            out += data[i:end]
            i = end
        else:
            raise _unreadable()
    raise _unreadable()


def strip_metadata(data: bytes) -> bytes:
    """Remove camera, location, editing, and comment metadata from a supported image.

    JPEG keeps its colour profile; PNG and WebP keep everything needed to draw the
    picture, including animation; GIF keeps its looping instruction. Anything appended
    after the end of the image (some phones store extra photos or tags there) is dropped.
    """
    detected = sniff_image(data)
    if detected is None:
        raise ImageHostError("Only PNG, JPEG, GIF, and WebP images can be uploaded.")
    extension = detected[0]
    try:
        if extension in ("jpg", "png") and _orientation(data) != 1:
            data = _upright_reencode(data, "JPEG" if extension == "jpg" else "PNG")
        stripper = {"jpg": _strip_jpeg, "png": _strip_png, "webp": _strip_webp, "gif": _strip_gif}
        return stripper[extension](data)
    except ImageHostError:
        raise
    except (IndexError, struct.error, OSError, ValueError) as error:
        raise _unreadable(error) from error


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

    @property
    def site(self) -> str:
        """The host's name, e.g. ``freeimage.host``, for messages to people."""
        return urllib.parse.urlsplit(self.endpoint).hostname or "the image host"

    @property
    def notice(self) -> str:
        """What people must know before uploading: the upload is anonymous and permanent."""
        return (f"Uploads go to {self.site} anonymously and are permanent: anyone with the link "
                f"can see the picture, and neither you nor Plurapack can delete it later. "
                f"If you want to be able to delete it, upload it yourself at https://{self.site} "
                f"with your own account and use that link instead.")

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
        data = strip_metadata(data)
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
