from symgov_backend.auth import hash_pin, validate_pin, verify_pin


def test_hash_pin_does_not_store_raw_pin():
    hashed = hash_pin("4590")

    assert "4590" not in hashed
    assert hashed.startswith("pbkdf2_sha256$")


def test_verify_pin_accepts_matching_pin_and_rejects_other_pin():
    hashed = hash_pin("4590")

    assert verify_pin("4590", hashed) is True
    assert verify_pin("1234", hashed) is False


def test_validate_pin_requires_exactly_four_digits():
    assert validate_pin("4590") == "4590"

    for value in ["", "123", "12345", "12a4", " 4590 "]:
        try:
            validate_pin(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid PIN: {value!r}")
