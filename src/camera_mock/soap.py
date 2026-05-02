from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from http import HTTPStatus
from logging import Logger
from typing import NamedTuple

from defusedxml import ElementTree

from camera_mock.auth import validate_ws_security
from camera_mock.models import MockConfig, MockDevice, StreamProfile


class SoapResult(NamedTuple):
    status: HTTPStatus
    body: str


class SoapContext(NamedTuple):
    body: bytes
    config: MockConfig
    device: MockDevice
    host: str
    logger: Logger


BodyHandler = Callable[[SoapContext], str]


class SoapAction(NamedTuple):
    name: str
    namespace: str


MEDIA1_NS = "http://www.onvif.org/ver10/media/wsdl"
MEDIA2_NS = "http://www.onvif.org/ver20/media/wsdl"


def handle_soap(body: bytes, *, config: MockConfig, device: MockDevice, host: str, logger: Logger) -> SoapResult:
    try:
        action = _soap_action(body)
    except ValueError as exc:
        logger.warning("soap_parse_failed device=%s error=%s", device.device_id, exc)
        return SoapResult(HTTPStatus.BAD_REQUEST, fault("InvalidRequest", str(exc)))

    if action.name not in _AUTH_EXEMPT_ACTIONS and not validate_ws_security(body, config.auth):
        logger.warning("soap_auth_failed device=%s action=%s", device.device_id, action.name)
        return SoapResult(HTTPStatus.UNAUTHORIZED, fault("NotAuthorized", "Invalid ONVIF credentials."))

    logger.info("soap_action device=%s action=%s namespace=%s", device.device_id, action.name, action.namespace)
    context = SoapContext(body=body, config=config, device=device, host=host, logger=logger)
    return _dispatch(action, context)


def _dispatch(action: SoapAction, context: SoapContext) -> SoapResult:  # noqa: PLR0911
    if action.name in _NOOP_SETTERS:
        context.logger.info("noop_setter device=%s action=%s", context.device.device_id, action.name)
        return ok(_NOOP_SETTERS[action.name])
    if action.name == "GetStreamUri":
        return _handle_get_stream_uri(context)
    if action.name == "GetSnapshotUri":
        return _handle_get_snapshot_uri(context)
    if action.name == "GetProfiles":
        return ok(_get_profiles(context.device, media2=action.namespace == MEDIA2_NS))
    if action.name == "GetVideoSources":
        return ok(_get_video_sources(context.device, media2=action.namespace == MEDIA2_NS))
    if action.name == "GetVideoEncoderConfigurations":
        return ok(_get_video_encoder_configurations(context.device, media2=action.namespace == MEDIA2_NS))
    handler = _BODY_HANDLERS.get(action.name)
    if handler is None:
        context.logger.warning("unsupported_soap_action device=%s action=%s", context.device.device_id, action.name)
        return SoapResult(
            HTTPStatus.NOT_IMPLEMENTED, fault("ActionNotSupported", f"Unsupported ONVIF action: {action.name}")
        )
    return ok(handler(context))


def _handle_get_stream_uri(context: SoapContext) -> SoapResult:
    token = _requested_profile_token(context.body) or context.device.profiles[0].token
    profile = _profile_by_token(context.device, token)
    if profile is None:
        return SoapResult(HTTPStatus.BAD_REQUEST, fault("InvalidArgVal", f"Unknown profile token: {token}"))
    context.logger.info(
        "stream_uri device=%s profile=%s path=%s", context.device.device_id, profile.token, profile.path
    )
    return ok(_get_stream_uri(context.config, profile, context.host))


def _handle_get_snapshot_uri(context: SoapContext) -> SoapResult:
    token = _requested_profile_token(context.body) or context.device.profiles[0].token
    profile = _profile_by_token(context.device, token)
    if profile is None:
        return SoapResult(HTTPStatus.BAD_REQUEST, fault("InvalidArgVal", f"Unknown profile token: {token}"))
    context.logger.info(
        "snapshot_uri device=%s profile=%s path=%s", context.device.device_id, profile.token, profile.path
    )
    return ok(_get_snapshot_uri(context.device, profile, context.host))


def soap_action(body: bytes) -> str:
    return _soap_action(body).name


def _soap_action(body: bytes) -> SoapAction:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid SOAP XML: {exc}") from exc
    body_element = next((element for element in root.iter() if _local_name(element.tag) == "Body"), None)
    if body_element is None:
        raise ValueError("SOAP Body is missing.")
    action = next((child for child in list(body_element) if isinstance(child.tag, str)), None)
    if action is None:
        raise ValueError("SOAP action is missing.")
    return SoapAction(name=_local_name(action.tag), namespace=_namespace(action.tag))


def ok(body: str) -> SoapResult:
    return SoapResult(HTTPStatus.OK, envelope(body))


def envelope(body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsa="http://www.w3.org/2005/08/addressing"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
            xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
            xmlns:tr2="http://www.onvif.org/ver20/media/wsdl"
            xmlns:tt="http://www.onvif.org/ver10/schema">
  <s:Body>
    {body}
  </s:Body>
</s:Envelope>
"""


def fault(code: str, reason: str) -> str:
    return envelope(
        f"""<s:Fault>
      <s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:{escape(code)}</s:Value></s:Subcode></s:Code>
      <s:Reason><s:Text xml:lang="en">{escape(reason)}</s:Text></s:Reason>
    </s:Fault>""",
    )


def _get_services(device: MockDevice, host: str) -> str:
    return f"""<tds:GetServicesResponse>
      <tds:Service>
        <tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
        <tds:XAddr>{escape(device.device_service_url(host))}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>
      </tds:Service>
      <tds:Service>
        <tds:Namespace>{MEDIA1_NS}</tds:Namespace>
        <tds:XAddr>{escape(device.media_service_url(host))}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>
      </tds:Service>
      <tds:Service>
        <tds:Namespace>{MEDIA2_NS}</tds:Namespace>
        <tds:XAddr>{escape(device.media2_service_url(host))}</tds:XAddr>
        <tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>
      </tds:Service>
    </tds:GetServicesResponse>"""


def _get_capabilities(device: MockDevice, host: str) -> str:
    return f"""<tds:GetCapabilitiesResponse>
      <tds:Capabilities>
        <tt:Device>
          <tt:XAddr>{escape(device.device_service_url(host))}</tt:XAddr>
        </tt:Device>
        <tt:Media>
          <tt:XAddr>{escape(device.media_service_url(host))}</tt:XAddr>
          <tt:StreamingCapabilities>
            <tt:RTPMulticast>false</tt:RTPMulticast>
            <tt:RTP_TCP>true</tt:RTP_TCP>
            <tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
          </tt:StreamingCapabilities>
        </tt:Media>
        <tt:Extension>
          <tt:Media2>
            <tt:XAddr>{escape(device.media2_service_url(host))}</tt:XAddr>
          </tt:Media2>
          <tt:ProfileCapabilities>
            <tt:MaximumNumberOfProfiles>{len(device.profiles)}</tt:MaximumNumberOfProfiles>
          </tt:ProfileCapabilities>
        </tt:Extension>
      </tds:Capabilities>
    </tds:GetCapabilitiesResponse>"""


def _get_device_information(device: MockDevice) -> str:
    return f"""<tds:GetDeviceInformationResponse>
      <tds:Manufacturer>{escape(device.manufacturer)}</tds:Manufacturer>
      <tds:Model>{escape(device.model)}</tds:Model>
      <tds:FirmwareVersion>{escape(device.firmware)}</tds:FirmwareVersion>
      <tds:SerialNumber>{escape(device.serial)}</tds:SerialNumber>
      <tds:HardwareId>{escape(device.hardware)}</tds:HardwareId>
    </tds:GetDeviceInformationResponse>"""


def _get_hostname(device: MockDevice) -> str:
    return f"""<tds:GetHostnameResponse>
      <tds:HostnameInformation>
        <tt:FromDHCP>false</tt:FromDHCP>
        <tt:Name>{escape(device.hostname)}</tt:Name>
      </tds:HostnameInformation>
    </tds:GetHostnameResponse>"""


def _get_system_date_and_time() -> str:
    now = datetime.now(UTC)
    return f"""<tds:GetSystemDateAndTimeResponse>
      <tds:SystemDateAndTime>
        <tt:DateTimeType>NTP</tt:DateTimeType>
        <tt:DaylightSavings>false</tt:DaylightSavings>
        <tt:TimeZone><tt:TZ>UTC</tt:TZ></tt:TimeZone>
        <tt:UTCDateTime>
          <tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>
          <tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>
        </tt:UTCDateTime>
      </tds:SystemDateAndTime>
    </tds:GetSystemDateAndTimeResponse>"""


def _get_network_interfaces(device: MockDevice, host: str) -> str:
    return f"""<tds:GetNetworkInterfacesResponse>
      <tds:NetworkInterfaces token="{escape(device.device_id)}">
        <tt:Enabled>true</tt:Enabled>
        <tt:Info><tt:Name>{escape(device.device_id)}</tt:Name><tt:HwAddress>00:00:00:00:00:00</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>
        <tt:IPv4>
          <tt:Enabled>true</tt:Enabled>
          <tt:Config><tt:Manual><tt:Address>{escape(host)}</tt:Address><tt:PrefixLength>24</tt:PrefixLength></tt:Manual><tt:DHCP>false</tt:DHCP></tt:Config>
        </tt:IPv4>
      </tds:NetworkInterfaces>
    </tds:GetNetworkInterfacesResponse>"""


def _get_scopes(device: MockDevice) -> str:
    scopes = "\n".join(
        f"        <tds:Scopes><tt:ScopeItem>{escape(scope)}</tt:ScopeItem></tds:Scopes>" for scope in device.scopes
    )
    return f"""<tds:GetScopesResponse>
{scopes}
    </tds:GetScopesResponse>"""


def _get_profiles(device: MockDevice, *, media2: bool) -> str:
    if not media2:
        profiles = "\n".join(_media1_profile_xml(profile) for profile in device.profiles)
        return f"""<trt:GetProfilesResponse>
{profiles}
    </trt:GetProfilesResponse>"""
    profiles = "\n".join(_media2_profile_xml(profile) for profile in device.profiles)
    return f"""<tr2:GetProfilesResponse>
{profiles}
    </tr2:GetProfilesResponse>"""


def _media1_profile_xml(profile: StreamProfile) -> str:
    return f"""      <trt:Profiles token="{escape(profile.token)}" fixed="true">
        <tt:Name>{escape(profile.name)}</tt:Name>
        <tt:VideoSourceConfiguration token="{escape(profile.token)}-source">
          <tt:Name>{escape(profile.name)} Source</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:SourceToken>{escape(profile.token)}-source</tt:SourceToken>
          <tt:Bounds x="0" y="0" width="{profile.width}" height="{profile.height}"/>
        </tt:VideoSourceConfiguration>
        <tt:VideoEncoderConfiguration token="{escape(profile.token)}-encoder">
          <tt:Name>{escape(profile.name)} Encoder</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:Encoding>{profile.codec}</tt:Encoding>
          <tt:Resolution><tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height></tt:Resolution>
          <tt:Quality>5</tt:Quality>
          <tt:RateControl>
            <tt:FrameRateLimit>{profile.fps:g}</tt:FrameRateLimit>
            <tt:EncodingInterval>1</tt:EncodingInterval>
            <tt:BitrateLimit>{_bitrate_kbps(profile)}</tt:BitrateLimit>
          </tt:RateControl>
          <tt:SessionTimeout>PT60S</tt:SessionTimeout>
        </tt:VideoEncoderConfiguration>
        <tt:AudioSourceConfiguration token="{escape(profile.token)}-audio-source">
          <tt:Name>{escape(profile.name)} Audio Source</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:SourceToken>{escape(profile.token)}-audio-source</tt:SourceToken>
        </tt:AudioSourceConfiguration>
        <tt:AudioEncoderConfiguration token="{escape(profile.token)}-audio-encoder">
          <tt:Name>{escape(profile.name)} Audio Encoder</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:Encoding>AAC</tt:Encoding>
          <tt:Bitrate>128</tt:Bitrate>
          <tt:SampleRate>48</tt:SampleRate>
          <tt:SessionTimeout>PT60S</tt:SessionTimeout>
        </tt:AudioEncoderConfiguration>
      </trt:Profiles>"""


def _media2_profile_xml(profile: StreamProfile) -> str:
    return f"""      <tr2:Profiles token="{escape(profile.token)}" fixed="true">
        <tt:Name>{escape(profile.name)}</tt:Name>
        <tr2:Configurations token="{escape(profile.token)}-source" type="VideoSource">
          <tt:Name>{escape(profile.name)} Source</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:SourceToken>{escape(profile.token)}-source</tt:SourceToken>
          <tt:Bounds x="0" y="0" width="{profile.width}" height="{profile.height}"/>
        </tr2:Configurations>
        <tr2:Configurations token="{escape(profile.token)}-encoder" type="VideoEncoder">
          <tt:Name>{escape(profile.name)} Encoder</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:Encoding>{profile.codec}</tt:Encoding>
          <tt:Resolution><tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height></tt:Resolution>
          <tt:RateControl><tt:FrameRateLimit>{profile.fps:g}</tt:FrameRateLimit></tt:RateControl>
        </tr2:Configurations>
        <tr2:Configurations token="{escape(profile.token)}-audio-source" type="AudioSource">
          <tt:Name>{escape(profile.name)} Audio Source</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:SourceToken>{escape(profile.token)}-audio-source</tt:SourceToken>
        </tr2:Configurations>
        <tr2:Configurations token="{escape(profile.token)}-audio-encoder" type="AudioEncoder">
          <tt:Name>{escape(profile.name)} Audio Encoder</tt:Name>
          <tt:UseCount>1</tt:UseCount>
          <tt:Encoding>AAC</tt:Encoding>
          <tt:Bitrate>128</tt:Bitrate>
          <tt:SampleRate>48</tt:SampleRate>
        </tr2:Configurations>
      </tr2:Profiles>"""


def _get_video_sources(device: MockDevice, *, media2: bool) -> str:
    prefix = "tr2" if media2 else "trt"
    sources = "\n".join(
        f"""      <{prefix}:VideoSources token="{escape(profile.token)}-source">
        <tt:Framerate>{profile.fps:g}</tt:Framerate>
        <tt:Resolution><tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height></tt:Resolution>
      </{prefix}:VideoSources>"""
        for profile in device.profiles
    )
    return f"""<{prefix}:GetVideoSourcesResponse>
{sources}
    </{prefix}:GetVideoSourcesResponse>"""


def _get_stream_uri(config: MockConfig, profile: StreamProfile, host: str) -> str:
    uri = config.rtsp_uri(host, profile)
    return f"""<trt:GetStreamUriResponse>
      <trt:MediaUri>
        <tt:Uri>{escape(uri)}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT60S</tt:Timeout>
      </trt:MediaUri>
      <tr2:Uri>{escape(uri)}</tr2:Uri>
    </trt:GetStreamUriResponse>"""


def _get_snapshot_uri(device: MockDevice, profile: StreamProfile, host: str) -> str:
    uri = device.snapshot_uri(host, profile)
    return f"""<trt:GetSnapshotUriResponse>
      <trt:MediaUri>
        <tt:Uri>{escape(uri)}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT60S</tt:Timeout>
      </trt:MediaUri>
      <tr2:Uri>{escape(uri)}</tr2:Uri>
    </trt:GetSnapshotUriResponse>"""


def _get_video_encoder_configurations(device: MockDevice, *, media2: bool) -> str:
    prefix = "tr2" if media2 else "trt"
    configs = "\n".join(
        f"""      <{prefix}:Configurations token="{escape(profile.token)}-encoder">
        <tt:Name>{escape(profile.name)} Encoder</tt:Name>
        <tt:UseCount>1</tt:UseCount>
        <tt:Encoding>{profile.codec}</tt:Encoding>
        <tt:Resolution><tt:Width>{profile.width}</tt:Width><tt:Height>{profile.height}</tt:Height></tt:Resolution>
        <tt:RateControl>
          <tt:FrameRateLimit>{profile.fps:g}</tt:FrameRateLimit>
          <tt:EncodingInterval>1</tt:EncodingInterval>
          <tt:BitrateLimit>{_bitrate_kbps(profile)}</tt:BitrateLimit>
        </tt:RateControl>
      </{prefix}:Configurations>"""
        for profile in device.profiles
    )
    return f"""<{prefix}:GetVideoEncoderConfigurationsResponse>
{configs}
    </{prefix}:GetVideoEncoderConfigurationsResponse>"""


def _requested_profile_token(body: bytes) -> str | None:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    for element in root.iter():
        if _local_name(element.tag) in {"ProfileToken", "Profile"} and element.text:
            return element.text
    return None


def _profile_by_token(device: MockDevice, token: str) -> StreamProfile | None:
    return next((profile for profile in device.profiles if profile.token == token), None)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]


def _bitrate_kbps(profile: StreamProfile) -> int:
    if profile.bitrate is None:
        return 0
    return max(1, profile.bitrate // 1000)


_BODY_HANDLERS: dict[str, BodyHandler] = {
    "GetServices": lambda context: _get_services(context.device, context.host),
    "GetCapabilities": lambda context: _get_capabilities(context.device, context.host),
    "GetDeviceInformation": lambda context: _get_device_information(context.device),
    "GetHostname": lambda context: _get_hostname(context.device),
    "GetSystemDateAndTime": lambda _: _get_system_date_and_time(),
    "GetNetworkInterfaces": lambda context: _get_network_interfaces(context.device, context.host),
    "GetScopes": lambda context: _get_scopes(context.device),
}

_NOOP_SETTERS = {
    "SetHostname": "<tds:SetHostnameResponse/>",
    "SetSystemDateAndTime": "<tds:SetSystemDateAndTimeResponse/>",
    "SetSynchronizationPoint": "<tr2:SetSynchronizationPointResponse/>",
}

_AUTH_EXEMPT_ACTIONS = {"GetSystemDateAndTime"}
