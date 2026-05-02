from __future__ import annotations

import logging
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import unquote, urlparse

from camera_mock.models import MockConfig, MockDevice, StreamProfile
from camera_mock.soap import handle_soap


class OnvifHTTPServer(ThreadingHTTPServer):
    config: MockConfig
    device: MockDevice
    advertised_host: str
    ffmpeg_bin: str
    stream_started_at: dict[str, float]
    logger: logging.Logger


def make_server(
    config: MockConfig,
    device: MockDevice,
    *,
    bind_host: str,
    advertised_host: str,
    ffmpeg_bin: str = "ffmpeg",
    stream_started_at: dict[str, float] | None = None,
) -> OnvifHTTPServer:
    server = OnvifHTTPServer((bind_host, device.http_port), _Handler)
    server.config = config
    server.device = device
    server.advertised_host = advertised_host
    server.ffmpeg_bin = ffmpeg_bin
    server.stream_started_at = stream_started_at if stream_started_at is not None else {}
    server.logger = logging.getLogger("camera_mock.onvif")
    return server


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self._send_snapshot():
            return
        self._send(HTTPStatus.OK, "Camera Mock ONVIF endpoint\n", content_type="text/plain; charset=utf-8")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self._onvif_server
        result = handle_soap(
            body,
            config=server.config,
            device=server.device,
            host=server.advertised_host,
            logger=server.logger,
        )
        self._send(result.status, result.body, content_type="application/soap+xml; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        self._onvif_server.logger.debug("http_request client=%s message=%s", self.client_address[0], format % args)

    def _send(self, status: HTTPStatus, body: str, *, content_type: str) -> None:
        self._send_bytes(status, body.encode(), content_type=content_type)

    def _send_bytes(self, status: HTTPStatus, payload: bytes, *, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_snapshot(self) -> bool:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/snapshot/") or not parsed.path.endswith(".jpg"):
            return False
        token = unquote(parsed.path.removeprefix("/snapshot/").removesuffix(".jpg"))
        profile = next((item for item in self._onvif_server.device.profiles if item.token == token), None)
        if profile is None:
            self._send(HTTPStatus.NOT_FOUND, "Unknown snapshot profile\n", content_type="text/plain; charset=utf-8")
            return True
        try:
            jpeg = _snapshot_jpeg(
                profile,
                ffmpeg_bin=self._onvif_server.ffmpeg_bin,
                offset=_snapshot_offset(profile, started_at=self._onvif_server.stream_started_at.get(profile.token)),
            )
        except SnapshotError as exc:
            self._onvif_server.logger.warning("snapshot_failed profile=%s error=%s", profile.token, _compact_error(exc))
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE, "Snapshot unavailable\n", content_type="text/plain; charset=utf-8"
            )
            return True
        self._send_bytes(HTTPStatus.OK, jpeg, content_type="image/jpeg")
        return True

    @property
    def _onvif_server(self) -> OnvifHTTPServer:
        return cast("OnvifHTTPServer", self.server)


class SnapshotError(RuntimeError):
    pass


def _snapshot_jpeg(profile: StreamProfile, *, ffmpeg_bin: str, offset: float = 0.0) -> bytes:
    try:
        return _run_snapshot_command(profile, ffmpeg_bin=ffmpeg_bin, offset=offset)
    except SnapshotError as exc:
        if offset <= 0:
            raise
        return _run_snapshot_command(profile, ffmpeg_bin=ffmpeg_bin, offset=0.0, previous_error=exc)


def _run_snapshot_command(
    profile: StreamProfile,
    *,
    ffmpeg_bin: str,
    offset: float,
    previous_error: SnapshotError | None = None,
) -> bytes:
    result = subprocess.run(  # noqa: S603
        [
            ffmpeg_bin,
            "-v",
            "error",
            "-ss",
            f"{offset:.3f}",
            "-i",
            str(profile.media_file),
            "-frames:v",
            "1",
            "-vf",
            "scale=in_range=limited:out_range=full,format=yuvj420p",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "3",
            "-strict",
            "unofficial",
            "pipe:1",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode(errors="replace").strip()
        details = stderr or f"ffmpeg exited with code {result.returncode}"
        if previous_error is not None:
            details = f"offset snapshot failed ({_compact_error(previous_error)}); fallback failed ({details})"
        raise SnapshotError(details)
    return result.stdout


def _snapshot_offset(profile: StreamProfile, *, started_at: float | None) -> float:
    if started_at is None:
        return 0.0
    elapsed = max(0.0, time.monotonic() - started_at)
    if profile.duration is None:
        return elapsed
    return elapsed % profile.duration


def _compact_error(error: BaseException, *, max_lines: int = 3) -> str:
    lines = [line for line in str(error).splitlines() if line.strip()]
    if not lines:
        return error.__class__.__name__
    if len(lines) <= max_lines:
        return " | ".join(lines)
    return " | ".join([*lines[:max_lines], f"... ({len(lines) - max_lines} more lines)"])
