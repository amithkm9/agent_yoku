from yoku.core.storage import tenancy


def test_current_tenant_requires_explicit_binding():
    tenancy.set_tenant(None)

    try:
        tenancy.current_tenant()
    except RuntimeError as exc:
        assert "tenant context is not set" in str(exc)
    else:
        raise AssertionError("expected current_tenant() to require an explicit tenant")


def test_tenant_maps_to_suffixed_db(monkeypatch):
    monkeypatch.setattr(tenancy.settings, "mongo_db", "yoku")

    assert tenancy.tenant_db_name("demo") == "yoku_demo"
