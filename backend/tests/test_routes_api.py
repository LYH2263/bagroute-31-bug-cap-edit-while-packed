import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import DeliveryRoute, PackBag, RejectRecord, SubscriberStop


def _make_route(session, name, stops, max_weight=8.0, max_volume=18.0):
    route = DeliveryRoute(name=name, max_weight_kg=max_weight, max_volume_l=max_volume)
    session.add(route)
    session.flush()
    for seq, (stop_name, weight, volume) in enumerate(stops, start=1):
        session.add(
            SubscriberStop(
                route_id=route.id,
                seq=seq,
                name=stop_name,
                weight_kg=weight,
                volume_l=volume,
            )
        )
    session.commit()
    return route.id


@pytest.fixture()
def ctx():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client, TestSession
    app.dependency_overrides.clear()
    engine.dispose()


def test_update_limit_fails_while_bags_exist(ctx):
    client, Session = ctx
    with Session() as db:
        rid = _make_route(
            db,
            "有袋禁改线",
            [("普通点", 2.0, 3.0), ("超大件", 9.5, 6.0)],
        )

    packed = client.post("/api/pack", json={"route_id": rid})
    assert packed.status_code == 200
    assert len(packed.json()) >= 1

    routes = {r["id"]: r for r in client.get("/api/routes").json()}
    assert routes[rid]["bag_count"] >= 1

    resp = client.patch(f"/api/routes/{rid}", json={"max_weight_kg": 20.0})
    assert resp.status_code == 409
    assert "先清空" in resp.json()["detail"]

    # limit must stay unchanged
    with Session() as db:
        route = db.get(DeliveryRoute, rid)
        assert route.max_weight_kg == 8.0


def test_update_limit_fails_while_rejects_exist_without_bags(ctx):
    client, Session = ctx
    with Session() as db:
        rid = _make_route(
            db,
            "仅拒收无袋线",
            [("超大件甲", 9.5, 6.0), ("超大件乙", 9.0, 19.0)],
        )

    packed = client.post("/api/pack", json={"route_id": rid})
    assert packed.status_code == 200
    assert packed.json() == []
    assert len(client.get("/api/rejects").json()) == 2

    routes = {r["id"]: r for r in client.get("/api/routes").json()}
    assert routes[rid]["bag_count"] == 0
    assert routes[rid]["reject_count"] == 2

    resp = client.patch(f"/api/routes/{rid}", json={"max_weight_kg": 20.0})
    assert resp.status_code == 409
    assert "先清空" in resp.json()["detail"]

    with Session() as db:
        route = db.get(DeliveryRoute, rid)
        assert route.max_weight_kg == 8.0

    cleared = client.post(f"/api/routes/{rid}/clear").json()
    assert cleared["deleted_rejects"] == 2
    assert client.get("/api/rejects").json() == []

    upd = client.patch(f"/api/routes/{rid}", json={"max_weight_kg": 20.0})
    assert upd.status_code == 200
    assert upd.json()["reject_count"] == 0


def test_clear_removes_bags_items_rejects_then_update_succeeds(ctx):
    client, Session = ctx
    with Session() as db:
        rid = _make_route(
            db,
            "清空后改线",
            [("普通点", 2.0, 3.0), ("超大件", 9.5, 6.0)],
        )
    client.post("/api/pack", json={"route_id": rid})

    resp = client.post(f"/api/routes/{rid}/clear")
    assert resp.status_code == 200
    cleared = resp.json()
    assert cleared["deleted_bags"] >= 1
    assert cleared["deleted_items"] >= 1
    assert cleared["deleted_rejects"] == 1

    # bags, weights and rejects pages no longer show this route
    assert [b for b in client.get("/api/bags").json() if b["route_id"] == rid] == []
    assert [w for w in client.get("/api/weights").json() if w["route_id"] == rid] == []
    assert client.get("/api/rejects").json() == []

    routes = {r["id"]: r for r in client.get("/api/routes").json()}
    assert routes[rid]["bag_count"] == 0

    resp = client.patch(
        f"/api/routes/{rid}",
        json={"max_weight_kg": 20.0, "max_volume_l": 30.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_weight_kg"] == 20.0
    assert body["max_volume_l"] == 30.0

    with Session() as db:
        route = db.get(DeliveryRoute, rid)
        assert route.max_weight_kg == 20.0
        assert route.max_volume_l == 30.0
        assert db.scalars(select(PackBag).where(PackBag.route_id == rid)).all() == []
        assert db.scalars(select(RejectRecord).where(RejectRecord.route_id == rid)).all() == []


def test_repack_after_raising_limit_accepts_previously_rejected(ctx):
    client, Session = ctx
    with Session() as db:
        rid = _make_route(
            db,
            "提限额再装线",
            [("超大件", 9.5, 6.0), ("普通点", 1.0, 1.0)],
        )

    first = client.post("/api/pack", json={"route_id": rid}).json()
    rejects = client.get("/api/rejects").json()
    assert len(rejects) == 1
    assert rejects[0]["stop_name"] == "超大件"
    packed_names = [i["stop_name"] for b in first for i in b["items"]]
    assert "超大件" not in packed_names

    client.post(f"/api/routes/{rid}/clear")
    upd = client.patch(f"/api/routes/{rid}", json={"max_weight_kg": 12.0})
    assert upd.status_code == 200

    second = client.post("/api/pack", json={"route_id": rid}).json()
    assert client.get("/api/rejects").json() == []
    names = [i["stop_name"] for b in second for i in b["items"]]
    assert names == ["超大件", "普通点"]
    assert all(b["weight_kg"] <= 12.0 + 1e-6 for b in second)


def test_repack_after_lowering_limit_rejects_follow_new_values(ctx):
    client, Session = ctx
    with Session() as db:
        rid = _make_route(
            db,
            "降限额再装线",
            [("超重件", 2.2, 4.0), ("超体积件", 1.0, 5.0), ("普通点", 1.0, 1.0)],
        )

    first = client.post("/api/pack", json={"route_id": rid}).json()
    assert client.get("/api/rejects").json() == []
    assert len(first) == 1

    client.post(f"/api/routes/{rid}/clear")
    upd = client.patch(
        f"/api/routes/{rid}",
        json={"max_weight_kg": 2.0, "max_volume_l": 4.0},
    )
    assert upd.status_code == 200

    bags = client.post("/api/pack", json={"route_id": rid}).json()
    rejects = client.get("/api/rejects").json()
    by_name = {r["stop_name"]: r["reason"] for r in rejects}
    assert "超重 2.2>2.0" in by_name["超重件"]
    assert "超体积 5.0>4.0" in by_name["超体积件"]

    names = [i["stop_name"] for b in bags for i in b["items"]]
    assert names == ["普通点"]
    assert all(b["weight_kg"] <= 2.0 + 1e-6 for b in bags)
    assert all(b["volume_l"] <= 4.0 + 1e-6 for b in bags)
