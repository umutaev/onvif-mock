from __future__ import annotations

import logging
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from camera_mock.discovery import DiscoveryResponder
from camera_mock.media import ffmpeg_publish_command, write_mediamtx_config
from camera_mock.models import MockConfig, StreamProfile
from camera_mock.server import OnvifHTTPServer, make_server


class RuntimeErrorMessage(RuntimeError):
    pass


class CameraMockRuntime:
    def __init__(
        self,
        config: MockConfig,
        *,
        interface_host: str,
        mediamtx_bin: str | None = None,
        ffmpeg_bin: str = "ffmpeg",
        bind_host: str = "0.0.0.0",  # noqa: S104
    ) -> None:
        self._config = config
        self._interface_host = interface_host
        self._mediamtx_bin = mediamtx_bin
        self._ffmpeg_bin = ffmpeg_bin
        self._bind_host = bind_host
        self._logger = logging.getLogger("camera_mock.runtime")
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._mediamtx: subprocess.Popen[str] | None = None
        self._publishers: list[tuple[StreamProfile, subprocess.Popen[str]]] = []
        self._publisher_started_at: dict[str, float] = {}
        self._servers: list[OnvifHTTPServer] = []
        self._threads: list[threading.Thread] = []
        self._discovery: DiscoveryResponder | None = None

    def run_forever(self) -> None:
        self.start()
        self._logger.info("camera_mock_started")
        try:
            while True:
                if self._mediamtx is not None and self._mediamtx.poll() is not None:
                    raise RuntimeErrorMessage(f"MediaMTX exited with code {self._mediamtx.returncode}.")
                for profile, publisher in self._publishers:
                    if publisher.poll() is not None:
                        raise RuntimeErrorMessage(
                            f"ffmpeg publisher for {profile.path} exited with code {publisher.returncode}.",
                        )
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._logger.info("shutdown_requested")
        finally:
            self._stop_after_interrupts()

    def start(self) -> None:
        mediamtx = _require_binary(self._mediamtx_bin or "mediamtx", "mediamtx")
        ffmpeg = _require_binary(self._ffmpeg_bin, "ffmpeg")
        self._tempdir = tempfile.TemporaryDirectory(prefix="camera-mock-")
        config_path = Path(self._tempdir.name) / "mediamtx.yml"
        write_mediamtx_config(self._config, config_path)
        self._logger.info("mediamtx_config path=%s", config_path)
        self._mediamtx = subprocess.Popen(  # noqa: S603
            [mediamtx, str(config_path)],
            text=True,
        )
        self._logger.info("mediamtx_started pid=%s", self._mediamtx.pid)
        _wait_for_port("127.0.0.1", self._config.ports.rtsp, process=self._mediamtx)
        self._start_publishers(ffmpeg)

        for device in self._config.devices:
            server = make_server(
                self._config,
                device,
                bind_host=self._bind_host,
                advertised_host=self._interface_host,
                ffmpeg_bin=ffmpeg,
                stream_started_at=self._publisher_started_at,
            )
            thread = threading.Thread(target=server.serve_forever, name=f"onvif-{device.device_id}", daemon=True)
            thread.start()
            self._servers.append(server)
            self._threads.append(thread)
            self._logger.info("onvif_started device=%s port=%s", device.device_id, device.http_port)

        self._discovery = DiscoveryResponder(self._config, interface_host=self._interface_host)
        self._discovery.start()

    def stop(self) -> None:
        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None
        for server in self._servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=2)
        self._servers.clear()
        self._threads.clear()
        for profile, publisher in self._publishers:
            self._logger.info("publisher_stopping path=%s pid=%s", profile.path, publisher.pid)
            _terminate(publisher, logger=self._logger)
        self._publishers.clear()
        self._publisher_started_at.clear()
        if self._mediamtx is not None:
            _terminate(self._mediamtx, logger=self._logger)
            self._mediamtx = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def _start_publishers(self, ffmpeg: str) -> None:
        for profile in self._config.profiles:
            command = ffmpeg_publish_command(ffmpeg, self._config, profile)
            publisher = subprocess.Popen(command, text=True)  # noqa: S603
            self._publisher_started_at[profile.token] = time.monotonic()
            self._publishers.append((profile, publisher))
            self._logger.info("publisher_started path=%s pid=%s", profile.path, publisher.pid)

    def _stop_after_interrupts(self) -> None:
        while True:
            try:
                self.stop()
            except KeyboardInterrupt:
                self._logger.warning("shutdown_interrupted_continuing")
                continue
            return


def _require_binary(binary: str, name: str) -> str:
    resolved = shutil.which(binary) if "/" not in binary else binary
    if resolved is None or not Path(resolved).exists():
        raise RuntimeErrorMessage(
            f"{name} binary not found. Install {name} or pass an explicit path with the CLI option.",
        )
    return resolved


def _wait_for_port(host: str, port: int, *, process: subprocess.Popen[str], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeErrorMessage(f"MediaMTX exited with code {process.returncode}.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
            except OSError:
                time.sleep(0.1)
            else:
                return
    raise RuntimeErrorMessage(f"MediaMTX did not open RTSP port {port} within {timeout:g}s.")


def _terminate(process: subprocess.Popen[str], *, logger: logging.Logger) -> None:
    if process.poll() is not None:
        logger.info("child_already_exited pid=%s code=%s", process.pid, process.returncode)
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("child_kill pid=%s", process.pid)
        process.kill()
        process.wait(timeout=5)
    logger.info("child_stopped pid=%s code=%s", process.pid, process.returncode)


def endpoints(config: MockConfig, *, interface_host: str) -> Sequence[str]:
    lines: list[str] = []
    for device in config.devices:
        lines.append(f"{device.device_id} ONVIF {device.device_service_url(interface_host)}")
        lines.append(f"{device.device_id} Media {device.media_service_url(interface_host)}")
        lines.append(f"{device.device_id} Media2 {device.media2_service_url(interface_host)}")
        lines.extend(
            f"{device.device_id} profile={profile.token} rtsp={config.rtsp_uri(interface_host, profile)}"
            for profile in device.profiles
        )
    return lines
