"""Unit tests for the Smart Challenge Pose & Selfie Generator (slik_checker.pose_generator)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from PIL import Image

from slik_checker.pose_generator import (
    PoseGenerator,
    parse_challenge_gesture,
)


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

    # Unknown defaults to 5
    assert parse_challenge_gesture("UNKNOWN")[0] == "5"


def test_find_local_preset(tmp_path: Path):
    nik = "3517181901000002"
    user_dir = tmp_path / nik
    user_dir.mkdir(parents=True)

    p5 = user_dir / "pose_5.jpg"
    p5.write_bytes(b"fake_jpeg_data")

    pg = PoseGenerator(base_dir=tmp_path)

    found = pg.find_local_preset(nik, "5")
    assert found == p5

    # Not found for other gesture
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

    # Create a valid tiny image
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(selfie, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)

    # Mock AI to fail/return None -> should fallback to selfie
    with mock.patch.object(pg, "generate_ai_pose", return_value=None):
        resolved = pg.resolve_pose(nik, selfie, None, "2A_B")
        assert resolved == selfie


def test_resolve_selfie_from_ktp_fallback(tmp_path: Path):
    nik = "3517181901000002"
    ktp = tmp_path / "ktp.jpg"
    img = Image.new("RGB", (100, 100), color="green")
    img.save(ktp, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)
    with mock.patch.object(pg, "generate_selfie_from_ktp", return_value=None):
        resolved = pg.resolve_selfie(nik, None, ktp)
        assert resolved == ktp


def test_resolve_pose_from_ktp_only(tmp_path: Path):
    nik = "3517181901000002"
    ktp = tmp_path / "ktp.jpg"
    img = Image.new("RGB", (100, 100), color="green")
    img.save(ktp, format="JPEG")

    pg = PoseGenerator(base_dir=tmp_path)
    with mock.patch.object(pg, "generate_ai_pose", return_value=None):
        resolved = pg.resolve_pose(nik, None, ktp, "5A_B")
        assert resolved == ktp


def test_resolve_pose_no_inputs():
    pg = PoseGenerator()
    assert pg.resolve_pose("123", None, None, "5A_B") is None
    assert pg.resolve_selfie("123", None, None) is None
