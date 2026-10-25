"""ONVIF camera: private addresses only, untrusted answers, live images, motion notifications."""

from __future__ import annotations

import httpx
import pytest

from sentinel.camera import onvif, service
from sentinel.camera.onvif import CameraError, OnvifClient
from sentinel.services import Services

DEVICE = "http://192.168.0.20:1000/onvif/device_service"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"

CAPABILITIES = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<s:Body><tds:GetCapabilitiesResponse><tds:Capabilities>
<tt:Events><tt:XAddr>http://192.168.0.20:1000/onvif/events_service</tt:XAddr></tt:Events>
<tt:Media><tt:XAddr>http://192.168.0.20:1000/onvif/media_service</tt:XAddr></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse></s:Body></s:Envelope>"""

PROFILES = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<s:Body><trt:GetProfilesResponse>
<trt:Profiles token="main1"><tt:Name>Driveway</tt:Name>
 <tt:VideoSourceConfiguration token="vsc1"><tt:SourceToken>src1</tt:SourceToken>
 </tt:VideoSourceConfiguration></trt:Profiles>
<trt:Profiles token="sub1"><tt:Name>Driveway sub</tt:Name>
 <tt:VideoSourceConfiguration token="vsc1"><tt:SourceToken>src1</tt:SourceToken>
 </tt:VideoSourceConfiguration></trt:Profiles>
<trt:Profiles token="main2"><tt:Name>Garden</tt:Name>
 <tt:VideoSourceConfiguration token="vsc2"><tt:SourceToken>src2</tt:SourceToken>
 </tt:VideoSourceConfiguration></trt:Profiles>
</trt:GetProfilesResponse></s:Body></s:Envelope>"""


def snapshot_answer(uri: str) -> str:
    return (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tt="http://www.onvif.org/ver10/schema" '
        'xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><s:Body>'
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        f"<tt:Uri>{uri}</tt:Uri></trt:MediaUri></trt:GetSnapshotUriResponse>"
        "</s:Body></s:Envelope>"
    )


MOTION = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">
<s:Body><wsnt:NotificationMessage>
 <wsnt:Topic>tns1:RuleEngine/CellMotionDetector/Motion</wsnt:Topic>
 <wsnt:Message><tt:Message><tt:Source>
  <tt:SimpleItem Name="VideoSourceConfigurationToken" Value="vsc2"/></tt:Source>
  <tt:Data><tt:SimpleItem Name="IsMotion" Value="true"/></tt:Data></tt:Message></wsnt:Message>
</wsnt:NotificationMessage>
<wsnt:NotificationMessage><wsnt:Topic>tns1:VideoSource/SignalLoss</wsnt:Topic>
 <wsnt:Message><tt:Message><tt:Data><tt:SimpleItem Name="State" Value="true"/></tt:Data>
 </tt:Message></wsnt:Message></wsnt:NotificationMessage>
</s:Body></s:Envelope>"""


def fake_device(snapshot_uri: str = "http://192.168.0.20/snap.jpg", password: str = "secret"):  # noqa: S107 - fake test login
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = request.content.decode()
        if request.method == "GET":
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Basic"):
                return httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="cam"'})
            return httpx.Response(200, content=JPEG)
        if password not in ("secret",) or "UsernameToken" not in body:
            return httpx.Response(401, text="NotAuthorized")
        if "GetCapabilities" in body:
            return httpx.Response(200, text=CAPABILITIES)
        if "GetProfiles" in body:
            return httpx.Response(200, text=PROFILES)
        if "GetSnapshotUri" in body:
            return httpx.Response(200, text=snapshot_answer(snapshot_uri))
        return httpx.Response(500)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.mark.parametrize(
    "url",
    [
        "http://203.0.113.5/onvif/device_service",
        "http://camera.example.com/onvif",
        "ftp://192.168.0.20/x",
        "http://127.0.0.1:1000/onvif",
    ],
)
def test_only_private_ip_addresses(url: str) -> None:
    assert not onvif.private_url(url)
    with pytest.raises(CameraError):
        OnvifClient(url, "admin", "secret")


def test_discover_refuses_public_hosts() -> None:
    with pytest.raises(CameraError):
        onvif.discover("203.0.113.5")


def test_password_is_never_sent_in_clear() -> None:
    header = onvif.security_header("admin", "hunter2-secret")
    assert "hunter2-secret" not in header and "PasswordDigest" in header


def test_channels_one_per_camera_input() -> None:
    http, seen = fake_device()
    client = OnvifClient(DEVICE, "admin", "secret", http)
    channels = client.channels()
    assert [(c.index, c.name, c.profile) for c in channels] == [
        (1, "Driveway", "main1"),
        (2, "Garden", "main2"),
    ]
    assert all(b"secret" not in r.content for r in seen)


def test_snapshot_with_basic_fallback() -> None:
    http, _ = fake_device()
    client = OnvifClient(DEVICE, "admin", "secret", http)
    uri = client.snapshot_uri("main1")
    assert client.snapshot(uri) == JPEG


def test_snapshot_pointing_elsewhere_is_refused() -> None:
    http, _ = fake_device(snapshot_uri="http://203.0.113.9/steal.jpg")
    client = OnvifClient(DEVICE, "admin", "secret", http)
    with pytest.raises(CameraError, match="different address"):
        client.snapshot_uri("main1")


def test_wrong_password_is_reported() -> None:
    http, _ = fake_device(password="wrong")
    with pytest.raises(CameraError, match="username or password"):
        OnvifClient(DEVICE, "admin", "wrong", http).channels()


def test_entities_are_rejected() -> None:
    with pytest.raises(CameraError):
        onvif._parse('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')


def test_parse_motion_only_motion_topics() -> None:
    events = onvif.parse_motion(onvif._parse(MOTION))
    assert events == [onvif.MotionEvent("vsc2", True)]


def camera_svc(svc: Services) -> None:
    http, _ = fake_device()
    service.save_login(DEVICE, "admin", "secret")
    svc.camera = service.CameraService(
        svc, client_factory=lambda url, user, pw: OnvifClient(url, user, pw, http)
    )


def test_login_lives_only_in_the_vault(svc: Services) -> None:
    assert not svc.camera.configured
    with pytest.raises(CameraError):
        service.save_login("http://203.0.113.5/onvif", "admin", "secret")
    service.save_login(DEVICE, "admin", "secret")
    assert svc.camera.configured
    assert "secret" not in str(svc.store.get("camera_motion"))
    service.delete_login()
    assert not svc.camera.configured


def test_motion_notifies_once_per_cooldown_and_runs_no_action(svc: Services) -> None:
    camera_svc(svc)
    svc.camera.channels()
    audit_before = len(svc.audit.list(100))
    assert svc.camera.handle_motion("vsc2", now=1000)
    assert not svc.camera.handle_motion("vsc2", now=1050)
    assert svc.camera.handle_motion("vsc2", now=1000 + service.MOTION_COOLDOWN_S + 1)
    assert svc.camera.last_motion["name"] == "Garden"
    notes = [e for e in svc.bus.history if e["kind"] == "notification"]
    assert len(notes) == 2 and "Garden" in notes[0]["data"]["message"]
    assert [e["kind"] for e in svc.bus.history].count("camera_motion") == 2
    assert len(svc.audit.list(100)) == audit_before


def test_camera_api(svc: Services) -> None:
    from fastapi.testclient import TestClient

    from sentinel.api.app import create_app

    api = TestClient(
        create_app(svc, token="t-1", start_background=False), base_url="http://127.0.0.1:8765"
    )
    h = {"Authorization": "Bearer t-1"}
    assert api.get("/camera", headers=h).json()["configured"] is False
    assert api.get("/camera/1/snapshot", headers=h).status_code == 503
    camera_svc(svc)
    state = api.get("/camera", headers=h).json()
    assert [c["name"] for c in state["channels"]] == ["Driveway", "Garden"]
    r = api.get("/camera/2/snapshot", headers=h)
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["cache-control"] == "no-store"
    assert api.get("/camera/9/snapshot", headers=h).status_code == 503
    assert api.get("/camera/1/snapshot").status_code == 401
    assert api.put("/camera/motion", json={"enabled": False}, headers=h).json() == {"motion": False}
    assert api.get("/setup", headers=h).json()["connected"]["camera"] is True


def test_voice_opens_camera_view() -> None:
    from sentinel.chat import quick

    assert quick.match("Show me the camera").params == {"view": "camera"}
    assert quick.match("Jarvis, open the security cameras").params == {"view": "camera"}
