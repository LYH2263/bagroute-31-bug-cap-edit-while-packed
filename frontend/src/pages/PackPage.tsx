import { useEffect, useState } from "react";
import { api } from "../api/client";
type R = { id: number; name: string; max_weight_kg: number; max_volume_l: number; bag_count: number };
type Bag = { id: number; bag_index: number; weight_kg: number; volume_l: number; items: { stop_name: string }[] };
type ClearOut = { deleted_bags: number; deleted_items: number; deleted_rejects: number };
export default function PackPage() {
  const [routes, setRoutes] = useState<R[]>([]);
  const [rid, setRid] = useState<number | "">("");
  const [bags, setBags] = useState<Bag[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function loadRoutes() {
    const r = await api<R[]>("/routes");
    setRoutes(r);
    setRid(cur => (cur === "" && r[0] ? r[0].id : cur));
    return r;
  }
  useEffect(() => { loadRoutes(); }, []);
  const route = routes.find(r => r.id === rid);

  async function run() {
    setBusy(true); setMsg(""); setErr("");
    try {
      const out = await api<Bag[]>("/pack", { method: "POST", body: JSON.stringify({ route_id: rid }) });
      setBags(out);
      setMsg(`完成装袋：${out.length} 袋`);
      await loadRoutes();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function clear() {
    if (!route) return;
    setMsg(""); setErr("");
    if (!window.confirm(`确定清空「${route.name}」的全部装袋结果？袋、袋内站点明细与拒收都会被删除。`)) return;
    setBusy(true);
    try {
      const out = await api<ClearOut>(`/routes/${route.id}/clear`, { method: "POST" });
      setBags([]);
      setMsg(`已清空：删除 ${out.deleted_bags} 个袋、${out.deleted_items} 条站点明细、${out.deleted_rejects} 条拒收；改限额请前往路线页`);
      await loadRoutes();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (<>
    <h2>装袋</h2>
    <div className="toolbar">
      <select value={rid} onChange={e => setRid(Number(e.target.value))}>{routes.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
      <button onClick={run} disabled={busy || rid === ""}>按路线顺序双约束装袋</button>
      <button className="btn-danger" onClick={clear} disabled={busy || rid === "" || !route?.bag_count}>清空本路线装袋</button>
      {route && <span className="hint">
        上限 {route.max_weight_kg}kg / {route.max_volume_l}L
        {route.bag_count > 0 ? ` · 已有 ${route.bag_count} 袋（改限额需先清空）` : " · 尚无袋"}
      </span>}
    </div>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    {bags.map(b => (
      <div key={b.id}>
        <div className="mono">袋 {b.bag_index} · {b.weight_kg}kg / {b.volume_l}L</div>
        <div className="bag-row">{b.items.map((it, i) => <div className="bag-block" key={i}>{it.stop_name}</div>)}</div>
      </div>
    ))}
  </>);
}
