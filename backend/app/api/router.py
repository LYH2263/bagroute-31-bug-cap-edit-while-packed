from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import BagItem, DeliveryRoute, PackBag, RejectRecord, SubscriberStop
from app.schemas.schemas import (
    BagItemOut,
    BagOut,
    PackRequest,
    RejectOut,
    RouteClearOut,
    RouteOut,
    RouteUpdate,
    StopOut,
    WeightOut,
)
from app.services.pack_engine import StopItem, pack_route

api_router = APIRouter()


def _clear_route_pack(db: Session, route_id: int) -> tuple[int, int, int]:
    """Delete a route's bags, bag items and rejects. Returns deleted counts."""
    bag_ids = [
        bid for (bid,) in db.execute(
            select(PackBag.id).where(PackBag.route_id == route_id)
        ).all()
    ]
    deleted_items = 0
    if bag_ids:
        deleted_items = (
            db.query(BagItem).filter(BagItem.bag_id.in_(bag_ids)).delete(synchronize_session=False)
        )
    deleted_bags = (
        db.query(PackBag).filter(PackBag.route_id == route_id).delete(synchronize_session=False)
    )
    deleted_rejects = (
        db.query(RejectRecord).filter(RejectRecord.route_id == route_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return deleted_bags, deleted_items, deleted_rejects


def _route_occupied(db: Session, route_id: int) -> tuple[bool, int, int]:
    """A route is occupied while it still has bags or reject records."""
    bag_count = db.scalar(select(func.count(PackBag.id)).where(PackBag.route_id == route_id)) or 0
    rej_count = db.scalar(
        select(func.count(RejectRecord.id)).where(RejectRecord.route_id == route_id)
    ) or 0
    return (bag_count > 0 or rej_count > 0), bag_count, rej_count


def _get_route_locked(db: Session, route_id: int) -> DeliveryRoute | None:
    """Fetch a route with a row lock so cap edits and packing can't interleave."""
    return db.scalar(
        select(DeliveryRoute).where(DeliveryRoute.id == route_id).with_for_update()
    )


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/routes", response_model=list[RouteOut])
def routes(db: Session = Depends(get_db)):
    rows = db.scalars(select(DeliveryRoute).order_by(DeliveryRoute.id)).all()
    bag_counts = dict(
        db.execute(
            select(PackBag.route_id, func.count(PackBag.id)).group_by(PackBag.route_id)
        ).all()
    )
    rej_counts = dict(
        db.execute(
            select(RejectRecord.route_id, func.count(RejectRecord.id)).group_by(
                RejectRecord.route_id
            )
        ).all()
    )
    return [
        RouteOut(
            id=r.id,
            name=r.name,
            max_weight_kg=r.max_weight_kg,
            max_volume_l=r.max_volume_l,
            bag_count=bag_counts.get(r.id, 0),
            reject_count=rej_counts.get(r.id, 0),
        )
        for r in rows
    ]


@api_router.patch("/routes/{route_id}", response_model=RouteOut)
def update_route(route_id: int, body: RouteUpdate, db: Session = Depends(get_db)):
    route = _get_route_locked(db, route_id)
    if not route:
        raise HTTPException(404, "路线不存在")
    occupied, bag_count, rej_count = _route_occupied(db, route_id)
    if occupied:
        # Caps are immutable while bags or rejects occupy the route; the row is
        # never written on this path, so an error response can't leave the DB
        # half-updated.
        bits = []
        if bag_count:
            bits.append(f"{bag_count} 个袋")
        if rej_count:
            bits.append(f"{rej_count} 条拒收")
        raise HTTPException(409, f"该路线仍有{'、'.join(bits)}，请先清空装袋后再修改限额")
    if body.max_weight_kg is not None:
        route.max_weight_kg = body.max_weight_kg
    if body.max_volume_l is not None:
        route.max_volume_l = body.max_volume_l
    db.commit()
    db.refresh(route)
    return RouteOut(
        id=route.id,
        name=route.name,
        max_weight_kg=route.max_weight_kg,
        max_volume_l=route.max_volume_l,
        bag_count=0,
        reject_count=0,
    )


@api_router.post("/routes/{route_id}/clear", response_model=RouteClearOut)
def clear_route(route_id: int, db: Session = Depends(get_db)):
    route = _get_route_locked(db, route_id)
    if not route:
        raise HTTPException(404, "路线不存在")
    deleted_bags, deleted_items, deleted_rejects = _clear_route_pack(db, route_id)
    db.commit()
    return RouteClearOut(
        route_id=route_id,
        deleted_bags=deleted_bags,
        deleted_items=deleted_items,
        deleted_rejects=deleted_rejects,
    )


@api_router.get("/stops", response_model=list[StopOut])
def stops(route_id: int | None = None, db: Session = Depends(get_db)):
    q = select(SubscriberStop).order_by(SubscriberStop.route_id, SubscriberStop.seq)
    if route_id is not None:
        q = q.where(SubscriberStop.route_id == route_id)
    return db.scalars(q).all()


@api_router.post("/pack", response_model=list[BagOut])
def pack(body: PackRequest, db: Session = Depends(get_db)):
    route = _get_route_locked(db, body.route_id)
    if not route:
        raise HTTPException(404, "路线不存在")
    # clear previous pack for route (bags, bag items and rejects)
    _clear_route_pack(db, route.id)

    stops = db.scalars(
        select(SubscriberStop).where(SubscriberStop.route_id == route.id).order_by(SubscriberStop.seq)
    ).all()
    items = [
        StopItem(s.id, s.seq, s.weight_kg, s.volume_l, s.name) for s in stops
    ]
    result = pack_route(items, route.max_weight_kg, route.max_volume_l)
    out_bags: list[PackBag] = []
    for bag in result.bags:
        row = PackBag(
            route_id=route.id,
            bag_index=bag.bag_index,
            weight_kg=round(bag.weight_kg, 3),
            volume_l=round(bag.volume_l, 3),
        )
        db.add(row)
        db.flush()
        for it in bag.items:
            db.add(
                BagItem(
                    bag_id=row.id,
                    stop_id=it.stop_id,
                    stop_name=it.label,
                    weight_kg=it.weight_kg,
                    volume_l=it.volume_l,
                )
            )
        out_bags.append(row)
    for stop, reason in result.rejects:
        db.add(
            RejectRecord(
                route_id=route.id,
                stop_id=stop.stop_id,
                stop_name=stop.label,
                reason=reason,
            )
        )
    db.commit()
    return [
        BagOut(
            id=b.id,
            route_id=b.route_id,
            bag_index=b.bag_index,
            weight_kg=b.weight_kg,
            volume_l=b.volume_l,
            items=[
                BagItemOut(
                    stop_id=i.stop_id,
                    stop_name=i.stop_name,
                    weight_kg=i.weight_kg,
                    volume_l=i.volume_l,
                )
                for i in db.scalars(select(BagItem).where(BagItem.bag_id == b.id)).all()
            ],
        )
        for b in out_bags
    ]


@api_router.get("/bags", response_model=list[BagOut])
def bags(db: Session = Depends(get_db)):
    rows = db.scalars(select(PackBag).order_by(PackBag.route_id, PackBag.bag_index)).all()
    out = []
    for b in rows:
        items = db.scalars(select(BagItem).where(BagItem.bag_id == b.id)).all()
        out.append(
            BagOut(
                id=b.id,
                route_id=b.route_id,
                bag_index=b.bag_index,
                weight_kg=b.weight_kg,
                volume_l=b.volume_l,
                items=[
                    BagItemOut(
                        stop_id=i.stop_id,
                        stop_name=i.stop_name,
                        weight_kg=i.weight_kg,
                        volume_l=i.volume_l,
                    )
                    for i in items
                ],
            )
        )
    return out


@api_router.get("/rejects", response_model=list[RejectOut])
def rejects(db: Session = Depends(get_db)):
    return db.scalars(select(RejectRecord).order_by(RejectRecord.id.desc())).all()


@api_router.get("/weights", response_model=list[WeightOut])
def weights(db: Session = Depends(get_db)):
    bags = db.scalars(select(PackBag).order_by(PackBag.id)).all()
    out = []
    for b in bags:
        route = db.get(DeliveryRoute, b.route_id)
        assert route
        out.append(
            WeightOut(
                bag_id=b.id,
                bag_index=b.bag_index,
                route_id=b.route_id,
                weight_kg=b.weight_kg,
                volume_l=b.volume_l,
                fill_weight_pct=round(100 * b.weight_kg / route.max_weight_kg, 1),
                fill_volume_pct=round(100 * b.volume_l / route.max_volume_l, 1),
            )
        )
    return out
