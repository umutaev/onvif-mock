from __future__ import annotations

import base64
import hashlib

from defusedxml import ElementTree

from camera_mock.models import AuthConfig


def validate_ws_security(body: bytes, auth: AuthConfig) -> bool:
    if not auth.enabled:
        return True
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return False

    username = _first_text(root, "Username")
    password = _first_text(root, "Password")
    if username != auth.username or password is None:
        return False

    password_type = _password_type(root)
    if password_type and password_type.endswith("#PasswordDigest"):
        return _validate_digest(root, password, auth.password)
    return password == auth.password


def _validate_digest(root: ElementTree.Element, password: str, expected_password: str) -> bool:
    nonce = _first_text(root, "Nonce")
    created = _first_text(root, "Created")
    if nonce is None or created is None:
        return False
    try:
        nonce_bytes = base64.b64decode(nonce)
    except ValueError:
        return False
    digest = hashlib.sha1(nonce_bytes + created.encode() + expected_password.encode(), usedforsecurity=False).digest()
    expected = base64.b64encode(digest).decode()
    return password == expected


def _first_text(root: ElementTree.Element, local_name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == local_name:
            return element.text
    return None


def _password_type(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == "Password":
            return element.attrib.get("Type")
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
