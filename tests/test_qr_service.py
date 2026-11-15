"""QR payload parsing and pairing-code primitives."""

import pytest

from app.services import qr_service


def test_pairing_code_has_expected_entropy_and_alphabet():
    code = qr_service.generate_pairing_code()
    assert len(code) == 24
    assert qr_service.PAIRING_CODE_PATTERN.match(code)
    assert qr_service.generate_pairing_code() != code


def test_payload_round_trip_normalises_input():
    code = qr_service.generate_pairing_code()
    spaced = "-".join(code[index : index + 4] for index in range(0, len(code), 4))

    payload = qr_service.build_payload("pa-esp32-0001", spaced)
    parsed = qr_service.parse_payload(payload)

    assert parsed.device_id == "PA-ESP32-0001"
    assert parsed.pairing_code == code
    assert parsed.version == qr_service.QR_VERSION


def test_payload_rejects_unknown_key():
    code = qr_service.generate_pairing_code()
    payload = qr_service.build_payload("PA-ESP32-0001", code)
    payload += "WIFI_PASSWORD:secret\n"

    with pytest.raises(qr_service.QRFormatError):
        qr_service.parse_payload(payload)


def test_payload_rejects_duplicate_key():
    code = qr_service.generate_pairing_code()
    payload = qr_service.build_payload("PA-ESP32-0001", code)
    payload += f"DEVICE_ID:PA-ESP32-0002\n"

    with pytest.raises(qr_service.QRFormatError):
        qr_service.parse_payload(payload)


def test_payload_rejects_wrong_version():
    code = qr_service.generate_pairing_code()
    payload = qr_service.build_payload("PA-ESP32-0001", code)
    payload = payload.replace("VERSION:1", "VERSION:9")

    with pytest.raises(qr_service.QRFormatError):
        qr_service.parse_payload(payload)


def test_payload_rejects_foreign_qr_text():
    with pytest.raises(qr_service.QRFormatError):
        qr_service.parse_payload("https://example.com/not-a-powerable-label")


def test_device_id_and_code_validation():
    with pytest.raises(qr_service.QRFormatError):
        qr_service.normalize_device_id("no spaces allowed")

    with pytest.raises(qr_service.QRFormatError):
        qr_service.normalize_pairing_code("too-short")

    assert (
        qr_service.normalize_pairing_code("abcd-efgh-ijkl-mnop")
        == "ABCDEFGHIJKLMNOP"
    )


def test_hash_is_not_plaintext_and_verifies():
    code = qr_service.generate_pairing_code()
    stored = qr_service.hash_pairing_code(code)

    assert stored != code
    assert code not in stored
    assert qr_service.verify_pairing_code(stored, code)
    assert not qr_service.verify_pairing_code(stored, qr_service.generate_pairing_code())
    assert not qr_service.verify_pairing_code(None, code)
