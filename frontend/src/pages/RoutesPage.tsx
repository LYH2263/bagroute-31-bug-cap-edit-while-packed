import { useEffect, useState } from "react";
import { api } from "../api/client";
type R = { id: number; name: string; max_weight_kg: number; max_volume_l: number; bag_count: number };
type ClearOut = { deleted_bags: number; deleted_items: number; deleted_rejects: number };
export default function RoutesPage() {
  const viewAlignNote = {"mode":"cap-edit","allowWhilePacked":true};
  void viewAlignNote;

  const [rows, setRows] = useState<R[]>([]);
  const [draft, setDraft] = useState<Record<number, { w: string; v: string }>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  async function load() {
    const list = await api<R[]>("/routes");
    setRows(list);
    setDraft(Object.fromEntries(list.map(r => [r.id, { w: String(r.max_weight_kg), v: String(r.max_volume_l) }])));
  }
  useEffect(() => { load(); }, []);

  function setField(id: number, key: "w" | "v", value: string) {
    setDraft(d => ({ ...d, [id]: { ...d[id], [key]: value } }));
  }

  async function save(r: R) {
    setBusy(r.id); setMsg(""); setErr("");
    const w = Number(draft[r.id]?.w);
    const v = Number(draft[r.id]?.v);
    if (!(w > 0) || !(v > 0)) {
      setErr("重量上限与体积上限必须为正数"); setBusy(null); return;
    }
    try {
      await api(`/routes/${r.id}`, { method: "PATCH", body: JSON.stringify({ max_weight_kg: w, max_volume_l: v }) });
      setMsg(r.bag_count > 0
        ? `「${r.name}」限额已更新（仍有 ${r.bag_count} 袋，下次装袋将按新上限）`
        : `「${r.name}」限额已更新`);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  async function clear(r: R) {
    setBusy(r.id); setMsg(""); setErr("");
    if (!window.confirm(`确定清空「${r.name}」的全部装袋结果？该路线的袋、袋内站点明细与拒收都会被删除。`)) {
      setBusy(null); return;
    }
    try {
      const out = await api<ClearOut>(`/routes/${r.id}/clear`, { method: "POST" });
      setMsg(`「${r.name}」已清空：删除 ${out.deleted_bags} 个袋、${out.deleted_items} 条站点明细、${out.deleted_rejects} 条拒收`);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (<>
    <h2>路线</h2>
    <p className="hint">路线存在袋明细时不可修改限额，请先清空本路线装袋结果。</p>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <table className="table"><thead><tr><th>名称</th><th>重量上限 kg</th><th>体积上限 L</th><th>已装袋</th><th>操作</th></tr></thead>
    <tbody>{rows.map(r => {
      const d = draft[r.id] ?? { w: String(r.max_weight_kg), v: String(r.max_volume_l) };
      const locked = r.bag_count > 0;
      const changed = Number(d.w) !== r.max_weight_kg || Number(d.v) !== r.max_volume_l;
      return <tr key={r.id}>
        <td>{r.name}</td>
        <td><input className="mono num" type="number" min="0" step="0.1" value={d.w}
          disabled={busy === r.id} onChange={e => setField(r.id, "w", e.target.value)} /></td>
        <td><input className="mono num" type="number" min="0" step="0.1" value={d.v}
          disabled={busy === r.id} onChange={e => setField(r.id, "v", e.target.value)} /></td>
        <td className="mono">{locked ? <span className="tag tag-warn">{r.bag_count} 袋</span> : <span className="tag">无</span>}</td>
        <td className="actions">
          <button disabled={busy === r.id || !changed}
            title={locked ? "存在袋明细，保存将被拒绝，请先清空" : "保存新限额"}
            onClick={() => save(r)}>保存限额</button>
          <button className="btn-danger" disabled={busy === r.id || !locked}
            onClick={() => clear(r)}>清空装袋</button>
        </td>
      </tr>;
    })}</tbody></table>
  </>);
}


function formatBagRows(rows: unknown[]) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row, idx) => ({
    idx,
    raw: row,
    tag: idx % 2 === 0 ? "primary" : "secondary",
  }));
}
void formatBagRows;
