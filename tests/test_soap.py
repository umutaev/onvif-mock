from __future__ import annotations

from http import HTTPStatus
from logging import getLogger
from pathlib import Path
from unittest import TestCase

from camera_mock.models import AuthConfig, MockConfig, MockDevice, PortConfig, StreamProfile
from camera_mock.soap import handle_soap, soap_action


class SoapTests(TestCase):
    def test_detects_action(self) -> None:
        self.assertEqual(soap_action(_request("GetServices")), "GetServices")

    def test_get_services_mentions_media_services(self) -> None:
        result = handle_soap(
            _request("GetServices"), config=_config(), device=_device(), host="127.0.0.1", logger=getLogger()
        )

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("http://www.onvif.org/ver10/media/wsdl", result.body)
        self.assertIn("http://www.onvif.org/ver20/media/wsdl", result.body)
        self.assertIn("http://127.0.0.1:8000/onvif/media_service", result.body)
        self.assertIn("http://127.0.0.1:8000/onvif/media2_service", result.body)

    def test_media1_get_profiles_advertises_stream_profiles(self) -> None:
        result = handle_soap(
            _request("GetProfiles", namespace="http://www.onvif.org/ver10/media/wsdl", prefix="trt"),
            config=_config(),
            device=_device(),
            host="127.0.0.1",
            logger=getLogger(),
        )

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("trt:GetProfilesResponse", result.body)
        self.assertIn('trt:Profiles token="main"', result.body)
        self.assertIn("tt:VideoSourceConfiguration", result.body)
        self.assertIn("tt:VideoEncoderConfiguration", result.body)
        self.assertIn("tt:AudioSourceConfiguration", result.body)
        self.assertIn("tt:AudioEncoderConfiguration", result.body)
        self.assertIn("H265", result.body)
        self.assertIn("AAC", result.body)

    def test_media1_get_profiles_advertises_h264_profiles(self) -> None:
        device = _device(codec="H264")
        result = handle_soap(
            _request("GetProfiles", namespace="http://www.onvif.org/ver10/media/wsdl", prefix="trt"),
            config=_config(device),
            device=device,
            host="127.0.0.1",
            logger=getLogger(),
        )

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("H264", result.body)
        self.assertNotIn("H265", result.body)

    def test_media2_get_profiles_advertises_stream_profiles(self) -> None:
        result = handle_soap(
            _request("GetProfiles", namespace="http://www.onvif.org/ver20/media/wsdl", prefix="tr2"),
            config=_config(),
            device=_device(),
            host="127.0.0.1",
            logger=getLogger(),
        )

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("tr2:GetProfilesResponse", result.body)
        self.assertIn('tr2:Profiles token="main"', result.body)

    def test_get_stream_uri_uses_requested_profile(self) -> None:
        body = b"""
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:ProfileToken>main</trt:ProfileToken></trt:GetStreamUri></s:Body>
        </s:Envelope>
        """
        result = handle_soap(body, config=_config(), device=_device(), host="127.0.0.1", logger=getLogger())

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("trt:MediaUri", result.body)
        self.assertIn("rtsp://127.0.0.1:8554/cam/main", result.body)

    def test_get_snapshot_uri_uses_requested_profile(self) -> None:
        body = b"""
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Body><trt:GetSnapshotUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:ProfileToken>main</trt:ProfileToken></trt:GetSnapshotUri></s:Body>
        </s:Envelope>
        """
        result = handle_soap(body, config=_config(), device=_device(), host="127.0.0.1", logger=getLogger())

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("trt:MediaUri", result.body)
        self.assertIn("http://127.0.0.1:8000/snapshot/main.jpg", result.body)

    def test_unsupported_action_faults(self) -> None:
        result = handle_soap(
            _request("RebootNow"), config=_config(), device=_device(), host="127.0.0.1", logger=getLogger()
        )

        self.assertEqual(result.status, HTTPStatus.NOT_IMPLEMENTED)
        self.assertIn("ActionNotSupported", result.body)

    def test_auth_failure(self) -> None:
        config = MockConfig(
            auth=AuthConfig(enabled=True, username="admin", password="secret"), ports=PortConfig(), devices=(_device(),)
        )
        result = handle_soap(
            _request("GetServices"), config=config, device=_device(), host="127.0.0.1", logger=getLogger()
        )

        self.assertEqual(result.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn("NotAuthorized", result.body)

    def test_get_system_date_and_time_is_allowed_without_auth(self) -> None:
        config = MockConfig(
            auth=AuthConfig(enabled=True, username="admin", password="secret"), ports=PortConfig(), devices=(_device(),)
        )
        result = handle_soap(
            _request("GetSystemDateAndTime"),
            config=config,
            device=_device(),
            host="127.0.0.1",
            logger=getLogger(),
        )

        self.assertEqual(result.status, HTTPStatus.OK)
        self.assertIn("GetSystemDateAndTimeResponse", result.body)


def _request(action: str, *, namespace: str = "http://www.onvif.org/ver10/device/wsdl", prefix: str = "tds") -> bytes:
    return f"""
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
      <s:Body><{prefix}:{action} xmlns:{prefix}="{namespace}"/></s:Body>
    </s:Envelope>
    """.encode()


def _config(device: MockDevice | None = None) -> MockConfig:
    return MockConfig(auth=AuthConfig(), ports=PortConfig(), devices=(device or _device(),))


def _device(*, codec: str = "H265") -> MockDevice:
    profile = StreamProfile(
        token="main",
        name="Main",
        media_file=Path("/tmp/video.ts"),
        path="cam/main",
        width=640,
        height=360,
        fps=30,
        codec=codec,
    )
    return MockDevice(
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
    )
