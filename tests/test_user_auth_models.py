from symgov_backend.models import User, UserRole, UserSession


def test_user_model_has_auth_columns():
    columns = User.__table__.columns

    assert "pin_hash" in columns
    assert "pin_set_at" in columns
    assert "must_change_pin" in columns
    assert "is_active" in columns
    assert "updated_at" in columns
    assert "role" not in columns


def test_user_email_and_display_name_are_case_insensitive_unique():
    index_names = {index.name for index in User.__table__.indexes}

    assert "uq_users_email_lower" in index_names
    assert "uq_users_display_name_lower" in index_names


def test_user_role_model_is_additive():
    columns = UserRole.__table__.columns

    assert UserRole.__tablename__ == "user_roles"
    assert "user_id" in columns
    assert "role" in columns
    assert {column.name for column in UserRole.__table__.primary_key.columns} == {"user_id", "role"}


def test_user_session_model_stores_token_hash_not_raw_token():
    columns = UserSession.__table__.columns

    assert UserSession.__tablename__ == "user_sessions"
    assert "token_hash" in columns
    assert "expires_at" in columns
    assert "revoked_at" in columns
    assert "token" not in columns
