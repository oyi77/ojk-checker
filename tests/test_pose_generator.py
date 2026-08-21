"""Unit tests for the Smart Challenge Pose & Selfie Generator with Facial Identity Locking."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image
from pydantic import SecretStr

from slik_checker.config import settings
from slik_checker.pose_generator import (
    PoseGenerator,
    extract_ktp_portrait,
    parse_challenge_gesture,
)


def _agnes_resp(status: int, payload: dict, text: str = "{}") -> mock.Mock:
    r = mock.Mock()
    r.status_code = status
    r.text = text
    r.json.return_value = payload
    return r


@pytest.fixture(autouse=True)
def _reset_agnes_state():
    """Isolate the class-level Agnes key pool / dead-key cache per test."""
    settings.agnes_api_key = None
    settings.agnes_api_keys = []
    settings.agnes_keys_file = None
    PoseGenerator._agnes_dead.clear()
    PoseGenerator._agnes_cursor = 0
    yield
    settings.agnes_api_key = None
    settings.agnes_api_keys = []
    settings.agnes_keys_file = None
    PoseGenerator._agnes_dead.clear()
    PoseGenerator._agnes_cursor = 0


def test_parse_challenge_gesture():
    assert parse_challenge_gesture("1A_B")[0] == "1"
    assert "1 finger" in parse_challenge_gesture("1A_B")[1]

    assert parse_challenge_gesture("2A_B")[0] == "2"
    assert "2 fingers" in parse_challenge_gesture("2A_B")[1]

    assert parse_challenge_gesture("3A_B")[0] == "3"
    assert "3 fingers" in parse_challenge_gesture("3A_B")[1]

    assert parse_challenge_gesture("5A_B")[0] == "5"
    assert "5 fingers" in parse_challenge_gesture("5A_B")[1]

    assert parse_challenge_gesture("JA_B")[0] == "J"
    assert "thumbs-up" in parse_challenge_gesture("JA_B")[1]

    assert parse_challenge_gesture("UNKNOWN")[0] == "5"


def test_extract_ktp_portrait():
    img = Image.new("RGB", (856, 540), color="blue")
    crop = extract_ktp_portrait(img)
    assert crop is not None
    assert crop.size[0] > 100
    assert crop.size[1] > 100


def test_find_local_preset(tmp_path: Path):
    nik = "3517181901000002"
    user_dir = tmp_path / nik
    user_dir.mkdir(parents=True)

    p5 = user_dir / "pose_5.jpg"
    p5.write_bytes(b"fake_jpeg_data")

    pg = PoseGenerator(base_dir=tmp_path)

    found = pg.find_local_preset(nik, "5")
    assert found == p5
    assert pg.find_local_preset(nik, "1") is None


def test_find_local_preset_thumbs(tmp_path: Path):
    nik = "1234567890123456"
    user_dir = tmp_path / nik
    user_dir.mkdir(parents=True)

    pj = user_dir / "pose_jempol.png"
    pj.write_bytes(b"fake_png_data")

    pg = PoseGenerator(base_dir=tmp_path)
    found = pg.find_local_preset(nik, "J")
    assert found == pj


def test_resolve_pose_uses_preset_first(tmp_path: Path):
    nik = "3517181901000002"
    user_dir = tmp_path / nik
    user_dir.mkdir(parents=True)
    p3 = user_dir / "pose_3.jpg"
    p3.write_bytes(b"preset_3_data")

    selfie = tmp_path / "selfie.jpg"
    selfie.write_bytes(b"selfie_data")

    pg = PoseGenerator(base_dir=tmp_path)
    resolved = pg.resolve_pose(nik, selfie, None, "3A_B")
    assert resolved == p3


def test_resolve_pose_fallback_to_selfie(tmp_path: Path):
    nik = "3517181901000002"
    selfie = tmp_path / "selfie.jpg"

    img = Image.new("RGB", (100, 100), color="blue")
    img.save(selfie, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)

    with mock.patch.object(pg, "generate_ai_pose", return_value=None):
        resolved = pg.resolve_pose(nik, selfie, None, "2A_B")
        assert resolved == selfie


def test_resolve_selfie_from_ktp_composite(tmp_path: Path):
    nik = "3517181901000002"
    ktp = tmp_path / "ktp.jpg"
    img = Image.new("RGB", (856, 540), color="green")
    img.save(ktp, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)
    with mock.patch.object(pg, "generate_selfie_from_ktp", return_value=None):
        resolved = pg.resolve_selfie(nik, None, ktp)
        assert resolved is not None
        assert resolved.exists()
        assert "selfie_composite" in resolved.name


def test_resolve_pose_from_ktp_only(tmp_path: Path):
    nik = "3517181901000002"
    ktp = tmp_path / "ktp.jpg"
    img = Image.new("RGB", (856, 540), color="green")
    img.save(ktp, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)
    with mock.patch.object(pg, "generate_ai_pose", return_value=None):
        resolved = pg.resolve_pose(nik, None, ktp, "5A_B")
        assert resolved is not None
        assert resolved.exists()
        assert "selfie_composite" in resolved.name
def test_resolve_pose_no_inputs():
    pg = PoseGenerator()
    assert pg.resolve_pose("123", None, None, "5A_B") is None
    assert pg.resolve_selfie("123", None, None) is None




def _tiny_png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_call_agnes_direct_success(tmp_path: Path):
    settings.agnes_api_key = SecretStr("agnes-test-key")
    try:
        pg = PoseGenerator(base_dir=tmp_path)
        out = tmp_path / "agn1" / "selfie.jpg"
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"data": [{"b64_json": _tiny_png_b64()}]}
        with mock.patch("slik_checker.pose_generator.requests.post", return_value=fake) as m:
            res = pg._call_agnes_direct([(Image.new("RGB", (10, 10)), "x")], "prompt", out)
        assert res == out
        assert out.exists()
        m.assert_called_once()
    finally:
        settings.agnes_api_key = None


def test_call_agnes_direct_no_key(tmp_path: Path):
    settings.agnes_api_key = None
    pg = PoseGenerator(base_dir=tmp_path)
    out = tmp_path / "agn2" / "selfie.jpg"
    with mock.patch("slik_checker.pose_generator.requests.post") as m:
        res = pg._call_agnes_direct([(Image.new("RGB", (10, 10)), "x")], "prompt", out)
    assert res is None
    m.assert_not_called()


def test_call_agnes_direct_401(tmp_path: Path):
    settings.agnes_api_key = SecretStr("agnes-test-key")
    try:
        pg = PoseGenerator(base_dir=tmp_path)
        out = tmp_path / "agn3" / "selfie.jpg"
        fake = mock.Mock()
        fake.status_code = 401
        fake.text = "unauthorized"
        fake.json.return_value = {}
        with mock.patch("slik_checker.pose_generator.requests.post", return_value=fake):
            res = pg._call_agnes_direct([(Image.new("RGB", (10, 10)), "x")], "prompt", out)
        assert res is None
        assert not out.exists()
    finally:
        settings.agnes_api_key = None


def test_call_agnes_direct_rotates_past_invalid_key(tmp_path: Path):
    settings.agnes_api_keys = ["k-bad", "k-good"]
    pg = PoseGenerator(base_dir=tmp_path)
    out = tmp_path / "agn_rot" / "selfie.jpg"
    side = [
        _agnes_resp(401, {}, "unauthorized"),
        _agnes_resp(200, {"data": [{"b64_json": _tiny_png_b64()}]}),
    ]
    with mock.patch(
        "slik_checker.pose_generator.requests.post", side_effect=side
    ) as m:
        res = pg._call_agnes_direct([(Image.new("RGB", (10, 10)), "x")], "prompt", out)
    assert res == out
    assert out.exists()
    assert m.call_count == 2
    assert "k-bad" in PoseGenerator._agnes_dead


def test_call_agnes_direct_all_keys_fail(tmp_path: Path):
    settings.agnes_api_keys = ["k1", "k2"]
    pg = PoseGenerator(base_dir=tmp_path)
    out = tmp_path / "agn_all" / "selfie.jpg"
    with mock.patch(
        "slik_checker.pose_generator.requests.post",
        return_value=_agnes_resp(503, {}, "server error"),
    ) as m:
        res = pg._call_agnes_direct([(Image.new("RGB", (10, 10)), "x")], "prompt", out)
    assert res is None
    assert not out.exists()
    assert m.call_count == 2


def test_agnes_key_pool_reads_file(tmp_path: Path):
    kf = tmp_path / "keys.txt"
    kf.write_text("sk-a\n# comment\n\nsk-b\n")
    settings.agnes_api_keys = ["sk-a", "sk-c"]
    settings.agnes_keys_file = str(kf)
    PoseGenerator._agnes_dead.add("sk-b")  # simulate a previously-failed key
    pg = PoseGenerator(base_dir=tmp_path)
    assert pg._agnes_key_pool() == ["sk-a", "sk-c"]
