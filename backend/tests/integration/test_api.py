from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from muda.api import create_app
from muda.domain.job_state import JobState
from muda.domain.operations import UpdateDesign, UpdateMenuPrice, parse_operation
from muda.persistence import jobs, publishing
from muda.persistence.models import utcnow
from muda.preview_token import create_preview_token
from muda.settings import Settings

SECRET = "p" * 40
USER = 42


@pytest.fixture
def settings() -> Settings:
    return Settings(preview_secret=SecretStr(SECRET), _env_file=None)


@pytest.fixture
def client(engine: Engine, settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(engine, settings)) as c:
        yield c


def _draft(engine: Engine, op: UpdateMenuPrice | UpdateDesign) -> tuple[str, str]:
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=USER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
        return jid, publishing.create_draft(s, jid, op)


def _price_op() -> UpdateMenuPrice:
    op = parse_operation(
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
    )
    assert isinstance(op, UpdateMenuPrice)
    return op


def _design_op() -> UpdateDesign:
    op = parse_operation({"operation": "update_design", "changes": {"theme": "dark"}})
    assert isinstance(op, UpdateDesign)
    return op


def _token(jid: str, draft: str, ttl: timedelta = timedelta(minutes=30)) -> str:
    return create_preview_token(SECRET, jid, draft, ttl, now=utcnow())


def test_healthz_checks_database(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_site_serves_seeded_published_content(client: TestClient) -> None:
    r = client.get("/api/site")
    assert r.status_code == 200
    body = r.json()
    assert body["content"]["hero"]["headline"] == "Every Cup Tells a Story"
    prices = {i["id"]: i["price"] for i in body["content"]["menu"]}
    assert prices == {"espresso": "90", "americano": "110", "latte": "130"}
    assert body["content"]["design"]["theme"] == "light"
    assert r.headers["cache-control"] == "no-cache"


def test_site_does_not_expose_drafts(client: TestClient, engine: Engine) -> None:
    _draft(engine, _price_op())
    prices = {i["id"]: i["price"] for i in client.get("/api/site").json()["content"]["menu"]}
    assert prices["latte"] == "130"


def test_site_updates_after_publish(client: TestClient, engine: Engine) -> None:
    jid, draft = _draft(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        publishing.approve(s, jid, draft, USER)
    prices = {i["id"]: i["price"] for i in client.get("/api/site").json()["content"]["menu"]}
    assert prices["latte"] == "150"


def test_preview_shows_old_and_new_values(client: TestClient, engine: Engine) -> None:
    jid, draft = _draft(engine, _price_op())
    r = client.get(f"/api/preview/{_token(jid, draft)}")
    assert r.status_code == 200
    body = r.json()
    assert {i["id"]: i["price"] for i in body["published"]["menu"]}["latte"] == "130"
    assert {i["id"]: i["price"] for i in body["draft"]["menu"]}["latte"] == "150"
    assert body["changes"] == [{"field": "menu.latte.price", "old": "130", "new": "150"}]
    assert r.headers["x-robots-tag"] == "noindex"
    assert r.headers["cache-control"] == "no-store"


def test_preview_shows_design_changes(client: TestClient, engine: Engine) -> None:
    jid, draft = _draft(engine, _design_op())
    body = client.get(f"/api/preview/{_token(jid, draft)}").json()
    assert body["draft"]["design"]["theme"] == "dark"
    assert body["published"]["design"]["theme"] == "light"
    assert body["changes"] == [{"field": "design.theme", "old": "light", "new": "dark"}]


@pytest.mark.parametrize("bad", ["garbage", "a.b", "x" * 300])
def test_preview_rejects_invalid_tokens(client: TestClient, bad: str) -> None:
    assert client.get(f"/api/preview/{bad}").status_code == 404


def test_preview_rejects_forged_and_expired_tokens(client: TestClient, engine: Engine) -> None:
    jid, draft = _draft(engine, _price_op())
    forged = create_preview_token("z" * 40, jid, draft, timedelta(minutes=30), now=utcnow())
    assert client.get(f"/api/preview/{forged}").status_code == 404
    expired = create_preview_token(
        SECRET, jid, draft, timedelta(minutes=30), now=utcnow() - timedelta(hours=1)
    )
    assert client.get(f"/api/preview/{expired}").status_code == 404


def test_preview_is_dead_once_job_is_decided(client: TestClient, engine: Engine) -> None:
    jid, draft = _draft(engine, _price_op())
    token = _token(jid, draft)
    assert client.get(f"/api/preview/{token}").status_code == 200
    with Session(engine) as s, jobs.write_tx(s):
        publishing.reject(s, jid, draft, USER)
    assert client.get(f"/api/preview/{token}").status_code == 404


def test_preview_token_for_other_draft_is_stale(client: TestClient, engine: Engine) -> None:
    jid, _ = _draft(engine, _price_op())
    assert client.get(f"/api/preview/{_token(jid, 'f' * 32)}").status_code == 404


def test_responses_carry_correlation_id(client: TestClient) -> None:
    r = client.get("/healthz")
    assert len(r.headers["x-request-id"]) >= 8
    r2 = client.get("/healthz", headers={"x-request-id": "my-trace-1"})
    assert r2.headers["x-request-id"] == "my-trace-1"


def test_hostile_request_id_is_not_reflected(client: TestClient) -> None:
    r = client.get("/healthz", headers={"x-request-id": "bad id\twith spaces <script>"})
    assert "<" not in r.headers["x-request-id"]
    assert " " not in r.headers["x-request-id"]


def test_internal_errors_do_not_leak_details(engine: Engine, settings: Settings) -> None:
    app = create_app(engine, settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500
    assert "secret internal detail" not in r.text


def test_security_headers_and_no_docs_exposed(client: TestClient) -> None:
    r = client.get("/api/site")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_settings_reject_weak_preview_secret() -> None:
    with pytest.raises(ValueError):
        Settings(preview_secret=SecretStr("short"), _env_file=None)
