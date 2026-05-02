from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest import TestCase
from unittest.mock import patch

from camera_mock.models import StreamProfile
from camera_mock.server import SnapshotError, _compact_error, _snapshot_jpeg, _snapshot_offset


class ServerTests(TestCase):
    def test_snapshot_jpeg_uses_ffmpeg(self) -> None:
        profile = _profile()
        with patch("camera_mock.server.subprocess.run") as run:
            run.return_value = CompletedProcess(args=[], returncode=0, stdout=b"jpeg", stderr=b"")

            result = _snapshot_jpeg(profile, ffmpeg_bin="/usr/bin/ffmpeg")

        self.assertEqual(result, b"jpeg")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("-ss", command)
        self.assertIn("0.000", command)
        self.assertIn(str(profile.media_file), command)
        self.assertIn("format=yuvj420p", " ".join(command))
        self.assertIn("unofficial", command)
        self.assertIn("mjpeg", command)

    def test_snapshot_jpeg_raises_on_failure(self) -> None:
        with patch("camera_mock.server.subprocess.run") as run:
            run.return_value = CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"failed")

            with self.assertRaises(SnapshotError):
                _snapshot_jpeg(_profile(), ffmpeg_bin="/usr/bin/ffmpeg")

    def test_snapshot_jpeg_falls_back_to_start_when_offset_fails(self) -> None:
        with patch("camera_mock.server.subprocess.run") as run:
            run.side_effect = [
                CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"seek failed"),
                CompletedProcess(args=[], returncode=0, stdout=b"jpeg", stderr=b""),
            ]

            result = _snapshot_jpeg(_profile(), ffmpeg_bin="/usr/bin/ffmpeg", offset=4.25)

        self.assertEqual(result, b"jpeg")
        first_command = run.call_args_list[0].args[0]
        second_command = run.call_args_list[1].args[0]
        self.assertEqual(first_command[first_command.index("-ss") + 1], "4.250")
        self.assertEqual(second_command[second_command.index("-ss") + 1], "0.000")

    def test_snapshot_jpeg_uses_offset(self) -> None:
        with patch("camera_mock.server.subprocess.run") as run:
            run.return_value = CompletedProcess(args=[], returncode=0, stdout=b"jpeg", stderr=b"")

            _snapshot_jpeg(_profile(), ffmpeg_bin="/usr/bin/ffmpeg", offset=4.25)

        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "4.250")

    def test_snapshot_offset_wraps_by_duration(self) -> None:
        profile = _profile(duration=10.0)
        with patch("camera_mock.server.time.monotonic", return_value=25.5):
            offset = _snapshot_offset(profile, started_at=12.0)

        self.assertAlmostEqual(offset, 3.5)

    def test_compact_error_truncates_long_decoder_output(self) -> None:
        message = "\n".join(f"line {index}" for index in range(6))

        self.assertEqual(_compact_error(RuntimeError(message)), "line 0 | line 1 | line 2 | ... (3 more lines)")


def _profile(*, duration: float | None = None) -> StreamProfile:
    return StreamProfile(
        token="main",
        name="Main",
        media_file=Path("/tmp/video.ts"),
        path="cam/main",
        width=640,
        height=360,
        fps=30,
        duration=duration,
    )
