"""Minimal ONVIF client: discovery, profiles, snapshots and motion events.

Everything a camera sends back is untrusted data: responses are size-limited,
parsed without entities and only used for display and notifications.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from sentinel.permissions.netguard import is_rfc1918

MAX_RESPONSE = 1_000_000
MAX_SNAPSHOT = 5_000_000
DISCOVERY_PORT = 3702
RTSP_PORTS = [554, 1024, 8554, 10554]

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wsa": "http://www.w3.org/2005/08/addressing",
}
_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0"
    "#PasswordDigest"
)
_XADDRS = re.compile(r"XAddrs>([^<]+)<")
# Factory names like "ProfileName001" say nothing: show "Camera 1" instead
_GENERIC_NAME = re.compile(r"(?i)(profile[\s_-]*(name)?|mainstream|substream)[\s_-]*\d*")


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True)
class Channel:
    index: int
    name: str
    profile: str
    source: str
    config: str = ""
    sub_profile: str = ""  # smaller second stream for the grid


@dataclass(frozen=True)
class MotionEvent:
    source: str
    active: bool


def private_url(url: str) -> bool:
    """Only plain IP addresses from the private ranges, never names or the internet."""
    parsed = urlparse(url)
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and is_rfc1918(parsed.hostname or "")
    )


def discover(host: str, timeout: float = 3.0) -> str | None:
    """Ask one device for its ONVIF device service URL (WS-Discovery, unicast)."""
    if not is_rfc1918(host):
        raise CameraError("Only devices on the private home network are allowed")
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        f"<e:Header><w:MessageID>uuid:{uuid.uuid4()}</w:MessageID>"
        "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
        "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        "</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:Probe></e:Body></e:Envelope>"
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(probe.encode(), (host, DISCOVERY_PORT))
        try:
            data, addr = sock.recvfrom(65535)
        except TimeoutError:
            return None
    if addr[0] != host:
        return None
    matches = _XADDRS.findall(data.decode(errors="ignore"))
    for url in matches[0].split() if matches else []:
        if private_url(url) and urlparse(url).hostname == host:
            return url
    return None


def security_header(user: str, password: str, now: datetime | None = None) -> str:
    """WS-Security UsernameToken with password digest (the password is never sent)."""
    nonce = os.urandom(16)
    created = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = hashlib.sha1(nonce + created.encode() + password.encode()).digest()  # noqa: S324
    return (
        f'<Security xmlns="{_WSSE}" s:mustUnderstand="1"><UsernameToken>'
        f"<Username>{_escape(user)}</Username>"
        f'<Password Type="{_DIGEST}">{base64.b64encode(digest).decode()}</Password>'
        f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        f'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        f'<Created xmlns="{_WSU}">{created}</Created></UsernameToken></Security>'
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse(text: str) -> ET.Element:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise CameraError("Unexpected answer from the camera")
    try:
        return ET.fromstring(text)  # noqa: S314 - size-limited, no DTD/entities (checked above)
    except ET.ParseError as exc:
        raise CameraError("Unreadable answer from the camera") from exc


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class OnvifClient:
    def __init__(
        self, device_url: str, user: str, password: str, http: httpx.Client | None = None
    ) -> None:
        if not private_url(device_url):
            raise CameraError("Camera address must be a private IP address (RFC 1918)")
        self.device_url = device_url
        self.host = urlparse(device_url).hostname
        self.user = user
        self.password = password
        self.http = http or httpx.Client(timeout=8, follow_redirects=False)
        self._services: dict[str, str] = {}
        self._snapshot_auth: int | None = None
        self._rtsp_port: int | None = None

    def _same_device(self, url: str) -> str:
        # The device names its own service URLs: they must stay on the same device
        parsed = urlparse(url)
        if not private_url(url) or parsed.hostname != self.host:
            raise CameraError("The camera pointed to a different address")
        device = urlparse(self.device_url)
        if parsed.port is None and device.port is not None:
            # Some recorders leave out their own port: keep the one we talk to
            url = parsed._replace(netloc=f"{parsed.hostname}:{device.port}").geturl()
        return url

    def call(self, url: str, body: str, *, auth: bool = True) -> ET.Element:
        header = f"<s:Header>{security_header(self.user, self.password)}</s:Header>" if auth else ""
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" '
            f'xmlns:tev="{NS["tev"]}" xmlns:tt="{NS["tt"]}" xmlns:wsnt="{NS["wsnt"]}" '
            f'xmlns:wsa="{NS["wsa"]}">{header}<s:Body>{body}</s:Body></s:Envelope>'
        )
        try:
            resp = self.http.post(
                url,
                content=envelope.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            )
        except httpx.HTTPError as exc:
            raise CameraError("Camera not reachable") from exc
        if resp.status_code in (400, 401, 403) and (
            "NotAuthorized" in resp.text or resp.status_code != 400
        ):
            raise CameraError("Camera rejected the username or password")
        if resp.status_code != 200:
            raise CameraError(f"Camera answered with error {resp.status_code}")
        if len(resp.content) > MAX_RESPONSE:
            raise CameraError("Camera answer too large")
        return _parse(resp.text)

    def services(self) -> dict[str, str]:
        if not self._services:
            root = self.call(
                self.device_url,
                "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>",
            )
            found: dict[str, str] = {}
            for node in root.iter():
                if _local(node.tag) in ("Media", "Events", "Device"):
                    addr = node.find("tt:XAddr", NS)
                    if addr is not None and _text(addr):
                        found[_local(node.tag).lower()] = self._same_device(_text(addr))
            if "media" not in found:
                raise CameraError("Camera offers no video (ONVIF media service missing)")
            self._services = found
        return self._services

    def channels(self) -> list[Channel]:
        root = self.call(self.services()["media"], "<trt:GetProfiles/>")
        channels: list[Channel] = []
        seen: set[str] = set()
        subs: dict[str, str] = {}
        for profile in root.iter(f"{{{NS['trt']}}}Profiles"):
            token = profile.get("token") or ""
            video = profile.find("tt:VideoSourceConfiguration", NS)
            source = _text(video.find("tt:SourceToken", NS)) if video is not None else ""
            config = (video.get("token") or "") if video is not None else ""
            # One entry per camera input; the first profile is usually the main stream
            if source in seen and token:
                subs.setdefault(source, token)
            if not token or not source or source in seen:
                continue
            seen.add(source)
            name = _text(profile.find("tt:Name", NS))[:40]
            if not name or _GENERIC_NAME.fullmatch(name):
                name = f"Camera {len(channels) + 1}"
            channels.append(Channel(len(channels) + 1, name, token, source, config))
        return [replace(c, sub_profile=subs.get(c.source, c.profile)) for c in channels]

    def stream_uri(self, profile: str) -> str:
        """RTSP address of a profile, with the RTSP port that really answers."""
        root = self.call(
            self.services()["media"],
            "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
            f"<trt:ProfileToken>{_escape(profile)}</trt:ProfileToken></trt:GetStreamUri>",
        )
        uri = _text(root.find(".//tt:Uri", NS))
        parsed = urlparse(uri)
        if parsed.scheme != "rtsp" or parsed.hostname != self.host:
            raise CameraError("Camera offers no video stream")
        if self._rtsp_port is None:
            self._rtsp_port = find_rtsp_port(self.host or "", [parsed.port or 554, *RTSP_PORTS])
        if self._rtsp_port is None:
            raise CameraError("Video stream (RTSP) is switched off on the camera")
        return parsed._replace(netloc=f"{parsed.hostname}:{self._rtsp_port}").geturl()

    def snapshot_uri(self, profile: str) -> str:
        root = self.call(
            self.services()["media"],
            f"<trt:GetSnapshotUri><trt:ProfileToken>{_escape(profile)}</trt:ProfileToken>"
            "</trt:GetSnapshotUri>",
        )
        uri = _text(root.find(".//tt:Uri", NS))
        if not uri:
            raise CameraError("Camera offers no still images")
        return self._same_device(uri)

    def snapshot(self, uri: str) -> bytes:
        uri = self._same_device(uri)
        # Many recorders never answer a request without login, so the login goes along
        # right away (Basic, home network only); Digest as fallback
        options: list[tuple[httpx.Auth, float]] = [
            (httpx.BasicAuth(self.user, self.password), 8),
            (httpx.DigestAuth(self.user, self.password), 4),
        ]
        if self._snapshot_auth is not None:
            options.insert(0, options.pop(self._snapshot_auth))
        for auth, timeout in options:
            try:
                resp = self.http.get(uri, auth=auth, timeout=timeout)
            except httpx.TimeoutException:
                continue
            except httpx.HTTPError as exc:
                raise CameraError("Camera not reachable") from exc
            if resp.status_code == 401:
                continue
            if resp.status_code != 200 or not resp.content.startswith(b"\xff\xd8"):
                raise CameraError("Camera sent no image")
            if len(resp.content) > MAX_SNAPSHOT:
                raise CameraError("Image too large")
            self._snapshot_auth = 0 if isinstance(auth, httpx.BasicAuth) else 1
            return resp.content
        raise CameraError("Camera rejected the username or password")

    def create_pull_point(self) -> str:
        events = self.services().get("events")
        if not events:
            raise CameraError("Camera offers no motion events")
        root = self.call(
            events,
            "<tev:CreatePullPointSubscription><tev:InitialTerminationTime>PT10M"
            "</tev:InitialTerminationTime></tev:CreatePullPointSubscription>",
        )
        address = _text(root.find(".//wsa:Address", NS))
        if not address:
            raise CameraError("Camera did not create a motion subscription")
        return self._same_device(address)

    def pull_motion(self, address: str, timeout_s: int = 10) -> list[MotionEvent]:
        root = self.call(
            address,
            f"<tev:PullMessages><tev:Timeout>PT{timeout_s}S</tev:Timeout>"
            "<tev:MessageLimit>20</tev:MessageLimit></tev:PullMessages>",
        )
        return parse_motion(root)

    def renew(self, address: str) -> None:
        self.call(
            address,
            "<wsnt:Renew><wsnt:TerminationTime>PT10M</wsnt:TerminationTime></wsnt:Renew>",
        )


def find_rtsp_port(host: str, ports: list[int], timeout: float = 2.0) -> int | None:
    """First port on this host that speaks RTSP (recorders often move it away from 554)."""
    if not is_rfc1918(host):
        return None
    for port in dict.fromkeys(ports):
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(f"OPTIONS rtsp://{host}:{port}/ RTSP/1.0\r\nCSeq: 1\r\n\r\n".encode())
                if sock.recv(16).startswith(b"RTSP/1.0"):
                    return port
        except OSError:
            continue
    return None


def parse_motion(root: ET.Element) -> list[MotionEvent]:
    """Motion on/off per video source from a PullMessages answer."""
    found: list[MotionEvent] = []
    for message in root.iter(f"{{{NS['wsnt']}}}NotificationMessage"):
        topic = _text(message.find("wsnt:Topic", NS)).lower()
        if "motion" not in topic:
            continue
        items: dict[str, str] = {}
        for item in message.iter(f"{{{NS['tt']}}}SimpleItem"):
            items[(item.get("Name") or "").lower()] = (item.get("Value") or "").lower()
        source = (
            items.get("videosourceconfigurationtoken")
            or items.get("source")
            or items.get("videosourcetoken")
            or ""
        )
        state = items.get("ismotion", items.get("state", items.get("motion", "")))
        if source and state in ("true", "false", "1", "0"):
            found.append(MotionEvent(source[:64], state in ("true", "1")))
    return found


def as_dict(channel: Channel) -> dict[str, Any]:
    return {"index": channel.index, "name": channel.name}
