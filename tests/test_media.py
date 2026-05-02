from __future__ import annotations

from pathlib import Path
from unittest import TestCase

import yaml

from camera_mock.media import ffmpeg_publish_command, mediamtx_config
from camera_mock.models import AuthConfig, MockConfig, MockDevice, PortConfig, StreamProfile


class MediaTests(TestCase):
    def test_generates_plain_paths_for_eager_publishers(self) -> None:
        config = _config(auth=AuthConfig(enabled=False))
        raw = yaml.safe_load(mediamtx_config(config))

        self.assertEqual(raw["rtspAddress"], ":8554")
        self.assertEqual(raw["rtspTransports"], ["tcp"])
        self.assertIn("cam/main", raw["paths"])
        self.assertNotIn("runOnDemand", raw["paths"]["cam/main"])

    def test_generates_eager_publisher_command(self) -> None:
        config = _config(auth=AuthConfig(enabled=False))
        command = ffmpeg_publish_command("/usr/bin/ffmpeg", config, config.profiles[0])
        command_text = " ".join(command)

        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("-stream_loop -1", command_text)
        self.assertIn("-map 0:a:0?", command_text)
        self.assertIn("-rtsp_transport tcp", command_text)
        self.assertIn("-c:a aac", command_text)
        self.assertNotIn("-an", command)
        self.assertIn("rtsp://127.0.0.1:8554/cam/main", command)

    def test_auth_configures_read_credentials(self) -> None:
        config = _config(auth=AuthConfig(enabled=True, username="user", password="pass"))
        raw = yaml.safe_load(mediamtx_config(config))

        self.assertEqual(raw["paths"]["cam/main"], {})
        self.assertEqual(raw["authInternalUsers"][1]["user"], "user")
        self.assertEqual(raw["authInternalUsers"][1]["pass"], "pass")


def _config(*, auth: AuthConfig) -> MockConfig:
    profile = StreamProfile(
        token="main",
        name="Main",
        media_file=Path("/tmp/video.ts"),
        path="cam/main",
        width=640,
        height=360,
        fps=30,
    )
    return MockConfig(
        auth=auth,
        ports=PortConfig(),
        devices=(
            MockDevice(
                device_id="cam",
                uuid="urn:uuid:test",
                hostname="cam",
                http_port=8000,
                manufacturer="Camera Mock",
                model="Mock",
                serial="1",
                firmware="0.1.0",
                hardware="software",
                profiles=(profile,),
                scopes=("onvif://www.onvif.org/Profile/T",),
            ),
        ),
    )
