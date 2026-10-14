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
