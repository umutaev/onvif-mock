from __future__ import annotations

import base64
import hashlib
from unittest import TestCase

from camera_mock.auth import validate_ws_security
from camera_mock.models import AuthConfig


class AuthTests(TestCase):
    def test_disabled_auth_allows_request(self) -> None:
        self.assertTrue(validate_ws_security(b"<Envelope/>", AuthConfig(enabled=False)))

    def test_password_text(self) -> None:
        body = b"""
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Header>
            <Security><UsernameToken><Username>admin</Username><Password>secret</Password></UsernameToken></Security>
          </s:Header>
          <s:Body/>
        </s:Envelope>
        """
        self.assertTrue(validate_ws_security(body, AuthConfig(enabled=True, username="admin", password="secret")))

    def test_password_digest(self) -> None:
        nonce = b"abc"
        created = "2026-05-02T00:00:00Z"
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + b"secret", usedforsecurity=False).digest()
        ).decode()
        nonce_text = base64.b64encode(nonce).decode()
        body = f"""
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
          <s:Header>
            <Security>
              <UsernameToken>
                <Username>admin</Username>
                <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
                <Nonce>{nonce_text}</Nonce>
                <Created>{created}</Created>
              </UsernameToken>
            </Security>
          </s:Header>
          <s:Body/>
        </s:Envelope>
        """.encode()
        self.assertTrue(validate_ws_security(body, AuthConfig(enabled=True, username="admin", password="secret")))
