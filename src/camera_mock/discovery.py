from __future__ import annotations

import logging
import socket
import struct
import threading
from html import escape
from uuid import uuid4

from camera_mock.models import MockConfig, MockDevice

MULTICAST_GROUP = "239.255.255.250"
DISCOVERY_PORT = 3702


class DiscoveryResponder:
    def __init__(self, config: MockConfig, *, interface_host: str, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._interface_host = interface_host
        self._logger = logger or logging.getLogger("camera_mock.discovery")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="camera-mock-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._socket = sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        membership = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton(self._interface_host)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(1.0)
        self._logger.info("discovery_started interface=%s port=%s", self._interface_host, DISCOVERY_PORT)
        while not self._stop.is_set():
            try:
                data, address = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            text = data.decode(errors="ignore")
            if "Probe" in text:
                self._logger.info("discovery_probe client=%s", address[0])
                for device in self._config.devices:
                    sock.sendto(_probe_match(device, self._interface_host).encode(), address)
            elif "Resolve" in text:
                self._logger.info("discovery_resolve client=%s", address[0])
                for device in self._config.devices:
                    if device.uuid in text:
                        sock.sendto(_resolve_match(device, self._interface_host).encode(), address)


def _probe_match(device: MockDevice, host: str) -> str:
    scopes = " ".join(escape(scope) for scope in device.scopes)
    return _envelope(
        "ProbeMatches",
        f"""<d:ProbeMatches>
      <d:ProbeMatch>
        <wsa:EndpointReference><wsa:Address>{escape(device.uuid)}</wsa:Address></wsa:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>{scopes}</d:Scopes>
        <d:XAddrs>{escape(device.device_service_url(host))}</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>""",
    )


def _resolve_match(device: MockDevice, host: str) -> str:
    scopes = " ".join(escape(scope) for scope in device.scopes)
    return _envelope(
        "ResolveMatches",
        f"""<d:ResolveMatches>
      <d:ResolveMatch>
        <wsa:EndpointReference><wsa:Address>{escape(device.uuid)}</wsa:Address></wsa:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>{scopes}</d:Scopes>
        <d:XAddrs>{escape(device.device_service_url(host))}</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ResolveMatch>
    </d:ResolveMatches>""",
    )


def _envelope(action: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:wsa="http://www.w3.org/2005/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <wsa:MessageID>urn:uuid:{uuid4()}</wsa:MessageID>
    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/{action}</wsa:Action>
  </s:Header>
  <s:Body>
    {body}
  </s:Body>
</s:Envelope>
"""


def multicast_interface_available(interface_host: str) -> bool:
    try:
        socket.inet_aton(interface_host)
    except OSError:
        return False
    try:
        socket.inet_aton(MULTICAST_GROUP)
        struct.pack("=4s4s", socket.inet_aton(MULTICAST_GROUP), socket.inet_aton(interface_host))
    except OSError:
        return False
    return True
