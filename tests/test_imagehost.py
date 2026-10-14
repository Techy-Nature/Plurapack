import json

import pytest

from plurapack.imagehost import ImageHost, ImageHostError, sniff_image

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def host(reply, **kwargs):
    seen = []

    def send(request):
        seen.append(request)
        return reply

    return ImageHost("secret-key", send=send, **kwargs), seen


def ok(url="https://iili.io/abc.png"):
    return 200, json.dumps({"status_code": 200, "image": {"url": url}}).encode()


def test_sniff_recognises_each_supported_format_by_bytes_not_name():
    assert sniff_image(PNG) == ("png", "image/png")
    assert sniff_image(b"\xff\xd8\xff\xe0abc") == ("jpg", "image/jpeg")
    assert sniff_image(b"GIF89a....") == ("gif", "image/gif")
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("webp", "image/webp")
    assert sniff_image(b"<svg></svg>") is None


def test_upload_posts_a_multipart_form_and_returns_the_https_url():
    image_host, seen = host(ok())
    assert image_host.upload(PNG) == "https://iili.io/abc.png"
    request = seen[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://freeimage.host/api/1/upload"
    assert request.headers["Content-type"].startswith("multipart/form-data; boundary=")
    body = request.data
    assert b'name="key"\r\n\r\nsecret-key' in body
    assert b'name="action"\r\n\r\nupload' in body
    assert b'name="source"; filename="image.png"' in body and PNG in body


@pytest.mark.parametrize("data, message", [
    (b"", "empty"),
    (b"not an image at all", "PNG, JPEG, GIF, and WebP"),
])
def test_upload_rejects_bad_files_before_contacting_the_host(data, message):
    image_host, seen = host(ok())
    with pytest.raises(ImageHostError, match=message) as caught:
        image_host.upload(data)
    assert not caught.value.upstream and not seen


def test_upload_enforces_the_size_limit():
    image_host, seen = host(ok(), max_bytes=16)
    with pytest.raises(ImageHostError, match="at most") as caught:
        image_host.upload(PNG)
    assert not caught.value.upstream and not seen


@pytest.mark.parametrize("reply", [
    (400, b'{"error": {"message": "Invalid API v1 key"}}'),
    (200, b"<html>maintenance</html>"),
    (200, b'{"image": {}}'),
    (200, b'{"image": {"url": "http://insecure.example/a.png"}}'),
])
def test_host_failures_are_reported_as_upstream_without_leaking_details(reply):
    image_host, _ = host(reply)
    with pytest.raises(ImageHostError) as caught:
        image_host.upload(PNG)
    assert caught.value.upstream
    assert "secret-key" not in str(caught.value) and "Invalid API" not in str(caught.value)


def test_environment_configuration(monkeypatch):
    monkeypatch.delenv("PLURAPACK_IMAGE_HOST_KEY", raising=False)
    assert ImageHost.from_environment() is None
    monkeypatch.setenv("PLURAPACK_IMAGE_HOST_KEY", " abc ")
    configured = ImageHost.from_environment()
    assert configured.api_key == "abc" and configured.endpoint == "https://freeimage.host/api/1/upload"
    monkeypatch.setenv("PLURAPACK_IMAGE_HOST_URL", "http://insecure.example/upload")
    with pytest.raises(ValueError, match="https"):
        ImageHost.from_environment()
    monkeypatch.setenv("PLURAPACK_IMAGE_HOST_URL", "https://images.example/api/1/upload")
    monkeypatch.setenv("PLURAPACK_IMAGE_MAX_BYTES", "lots")
    with pytest.raises(ValueError, match="whole number"):
        ImageHost.from_environment()


# ---- shrinking pictures over the threshold ----------------------------------------------

Image = pytest.importorskip("PIL.Image")


def noisy_png(side=1200):
    import os

    buffer = __import__("io").BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buffer, "PNG")
    return buffer.getvalue()


def sent_image(request):
    """Return the raw file bytes inside the multipart body of a fake upload."""
    body = request.data
    start = body.index(b"\r\n\r\n", body.index(b'name="source"')) + 4
    return body[start:body.rindex(b"\r\n--")]


def test_pictures_under_the_threshold_are_sent_unchanged():
    image_host, seen = host(ok(), compress_over=10_000)
    image_host.upload(PNG)
    assert sent_image(seen[0]) == PNG


def test_large_pictures_become_webp_under_the_threshold():
    original = noisy_png()
    assert len(original) > 1_000_000
    image_host, seen = host(ok(), compress_over=1_000_000)
    assert image_host.upload(original) == "https://iili.io/abc.png"
    shrunk = sent_image(seen[0])
    assert b'filename="image.webp"' in seen[0].data and b"Content-Type: image/webp" in seen[0].data
    assert len(shrunk) <= 1_000_000 < len(original)
    assert Image.open(__import__("io").BytesIO(shrunk)).format == "WEBP"


def test_huge_dimensions_are_limited_to_2048_pixels():
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (6000, 3000), (10, 120, 200)).save(buffer, "PNG")
    image_host, seen = host(ok(), compress_over=1000)
    image_host.upload(buffer.getvalue())
    width, height = Image.open(io.BytesIO(sent_image(seen[0]))).size
    assert max(width, height) <= 2048 and width == 2 * height


def test_shrinking_drops_metadata_and_applies_orientation():
    import io

    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x0112] = 6  # rotate 90 degrees when displayed
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), (200, 30, 30)).save(buffer, "JPEG", exif=exif)
    image_host, seen = host(ok(), compress_over=100)
    image_host.upload(buffer.getvalue())
    result = Image.open(io.BytesIO(sent_image(seen[0])))
    assert b"SecretCameraMaker" not in seen[0].data
    assert result.size == (200, 400)


def test_animated_pictures_stay_animated():
    import io

    frames = [Image.frombytes("RGB", (300, 300), __import__("os").urandom(300 * 300 * 3)) for _ in range(12)]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
    assert len(buffer.getvalue()) > 100_000
    image_host, seen = host(ok(), compress_over=100_000)
    image_host.upload(buffer.getvalue())
    result = Image.open(io.BytesIO(sent_image(seen[0])))
    assert result.format == "WEBP" and getattr(result, "n_frames", 1) == 12


def test_shrinking_can_be_turned_off():
    original = noisy_png(300)
    image_host, seen = host(ok(), compress_over=0)
    image_host.upload(original)
    assert sent_image(seen[0]) == original


def test_a_corrupt_picture_over_the_threshold_is_refused_clearly():
    broken = PNG + b"not really image data" * 50
    image_host, seen = host(ok(), compress_over=100)
    with pytest.raises(ImageHostError, match="could not be read") as caught:
        image_host.upload(broken)
    assert not caught.value.upstream and not seen


def test_compress_over_environment_setting(monkeypatch):
    monkeypatch.setenv("PLURAPACK_IMAGE_HOST_KEY", "abc")
    monkeypatch.delenv("PLURAPACK_IMAGE_HOST_URL", raising=False)
    monkeypatch.delenv("PLURAPACK_IMAGE_MAX_BYTES", raising=False)
    monkeypatch.delenv("PLURAPACK_IMAGE_COMPRESS_OVER", raising=False)
    assert ImageHost.from_environment().compress_over == 1_000_000
    monkeypatch.setenv("PLURAPACK_IMAGE_COMPRESS_OVER", "0")
    assert ImageHost.from_environment().compress_over == 0
    monkeypatch.setenv("PLURAPACK_IMAGE_COMPRESS_OVER", "big")
    with pytest.raises(ValueError, match="whole number"):
        ImageHost.from_environment()
