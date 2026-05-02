from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import yaml

from camera_mock.config import ConfigError, load_config, parse_config


class ConfigTests(TestCase):
    def test_parse_single_device(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            media = base_dir / "video.ts"
            media.write_bytes(b"video")
            with _ffprobe():
                config = parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "video.ts",
                                        "path": "cam/main",
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

        self.assertEqual(config.ports.rtsp, 8554)
        self.assertEqual(config.devices[0].http_port, 8000)
        self.assertEqual(config.devices[0].profiles[0].media_file, media.resolve())
        self.assertEqual(config.devices[0].profiles[0].codec, "H265")
        self.assertEqual(config.devices[0].profiles[0].width, 640)
        self.assertEqual(config.devices[0].profiles[0].height, 360)
        self.assertEqual(config.devices[0].profiles[0].fps, 30)
        self.assertEqual(config.devices[0].profiles[0].bitrate, 1296000)
        self.assertEqual(config.devices[0].profiles[0].duration, 9.79)

    def test_accepts_explicit_h264_codec(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe(codec="h264"):
                config = parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "video.ts",
                                        "codec": "H264",
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

        self.assertEqual(config.devices[0].profiles[0].codec, "H264")

    def test_rejects_mismatched_explicit_codec(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe(), self.assertRaises(ConfigError):
                parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "video.ts",
                                        "codec": "H264",
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

    def test_accepts_inferred_h264_codec(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe(codec="h264"):
                config = parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "video.ts",
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

        self.assertEqual(config.devices[0].profiles[0].codec, "H264")

    def test_rejects_unsupported_codec(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe(codec="vp9"), self.assertRaises(ConfigError):
                parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "video.ts",
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

    def test_rejects_duplicate_paths(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe(), self.assertRaises(ConfigError):
                parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam-1",
                                "profiles": [{"token": "main", "media_file": "video.ts", "path": "same"}],
                            },
                            {
                                "id": "cam-2",
                                "profiles": [{"token": "main", "media_file": "video.ts", "path": "same"}],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

    def test_load_config_resolves_relative_media_files_from_cwd(self) -> None:
        with TemporaryDirectory() as directory:
            cwd = Path(directory)
            media_dir = cwd / "videos"
            media_dir.mkdir()
            media = media_dir / "video.ts"
            media.write_bytes(b"video")
            config_path = media_dir / "camera.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "token": "main",
                                        "media_file": "videos/video.ts",
                                    },
                                ],
                            },
                        ],
                    },
                ),
                encoding="utf-8",
            )

            with _working_directory(cwd), _ffprobe():
                config = load_config(config_path)

        self.assertEqual(config.devices[0].profiles[0].media_file, media.resolve())

    def test_explicit_metadata_overrides_ffprobe(self) -> None:
        with TemporaryDirectory() as directory:
            base_dir = Path(directory)
            (base_dir / "video.ts").write_bytes(b"video")

            with _ffprobe():
                config = parse_config(
                    {
                        "devices": [
                            {
                                "id": "cam",
                                "profiles": [
                                    {
                                        "media_file": "video.ts",
                                        "width": 320,
                                        "height": 180,
                                        "fps": 15,
                                        "bitrate": 500000,
                                        "duration": 3.5,
                                    },
                                ],
                            },
                        ],
                    },
                    base_dir=base_dir,
                )

        profile = config.devices[0].profiles[0]
        self.assertEqual(profile.width, 320)
        self.assertEqual(profile.height, 180)
        self.assertEqual(profile.fps, 15)
        self.assertEqual(profile.bitrate, 500000)
        self.assertEqual(profile.duration, 3.5)


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@contextmanager
def _ffprobe(*, codec: str = "hevc") -> Iterator[None]:
    payload = json.dumps(
        {
            "streams": [
                {
                    "codec_name": codec,
                    "width": 640,
                    "height": 360,
                    "avg_frame_rate": "30/1",
                    "r_frame_rate": "30/1",
                }
            ],
            "format": {
                "duration": "9.79",
                "bit_rate": "1296000",
            },
        }
    )
    with patch("camera_mock.config.subprocess.run") as run:
        run.return_value = CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")
        yield
