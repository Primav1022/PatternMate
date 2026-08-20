import React, { useEffect, useMemo, useState } from 'react';

type QueueItem = {
  case_id: string;
  issue: string;
  hint: string;
  cover_url: string;
  sleeve_style?: string | null;
  roles?: Record<string, number>;
  reviewed?: boolean;
};

type Piece = {
  piece_id: string;
  role: string;
  cad_name?: string;
  closed?: boolean;
  width_mm: number;
  height_mm: number;
  paths: number[][][];
};

type CaseDetail = QueueItem & {
  viewBox: string;
  pieces: Piece[];
  notes?: string;
};

const TSHIRT_ROLES = [
  ['front_body', '前片'],
  ['back_body', '后片'],
  ['sleeve', '袖片'],
  ['side_panel', '侧片'],
  ['neck_binding', '领条'],
  ['scrap', '废片/唛架'],
] as const;

const SHIRT_ROLES = [
  ['front_body', '前片'],
  ['front_left', '左前片'],
  ['front_right', '右前片'],
  ['front_yoke', '前育克'],
  ['back_body', '后片'],
  ['back_yoke', '后育克'],
  ['sleeve', '袖片'],
  ['collar', '领'],
  ['front_placket', '门襟'],
  ['cuff', '袖口'],
  ['scrap', '废片/唛架'],
] as const;

const SLEEVES = [
  ['sleeveless', '无袖'],
  ['flutter', '飞袖'],
  ['raglan', '插肩袖'],
  ['batwing', '蝙蝠袖'],
  ['set-in', '正肩袖'],
  ['puff', '泡泡袖'],
  ['unknown', '看不清'],
] as const;

const COLORS: Record<string, string> = {
  front_body: '#3f8f83',
  front_left: '#3f8f83',
  front_right: '#3f8f83',
  front_yoke: '#2f6f66',
  back_body: '#bd8d79',
  back_yoke: '#a45c48',
  sleeve: '#4d86b4',
  side_panel: '#c9843a',
  neck_binding: '#9b86d9',
  collar: '#9b86d9',
  front_placket: '#d29a45',
  cuff: '#4d86b4',
  scrap: '#b0a8a0',
  unlabeled: '#c2410c',
};

function api() {
  return String(import.meta.env.VITE_GEOMETRY_BASE_URL || '/geometry').replace(/\/$/, '');
}

function shirtMode() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash.startsWith('relabel-yoke') || hash === 'sandbox/relabel-yoke' || new URLSearchParams(window.location.search).get('family') === 'shirt';
}

export function RelabelQueue() {
  const [shirt, setShirt] = useState(() => shirtMode());
  const qs = shirt ? '?family=shirt' : '';
  const roleOptions = shirt ? SHIRT_ROLES : TSHIRT_ROLES;
  const [items, setItems] = useState<QueueItem[]>([]);
  const [caseId, setCaseId] = useState('');
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [sleeve, setSleeve] = useState('unknown');
  const [notes, setNotes] = useState('');
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  const selectedItem = items.find((row) => row.case_id === caseId);

  useEffect(() => {
    const sync = () => setShirt(shirtMode());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  useEffect(() => {
    let stop = false;
    setCaseId('');
    setDetail(null);
    const timer = window.setTimeout(() => {
      fetch(`${api()}/relabel/queue${qs}`)
        .then((r) => r.json())
        .then((data) => {
          if (stop) return;
          const rows = (data.items || []) as QueueItem[];
          setItems(rows);
          if (rows[0]) setCaseId(rows[0].case_id);
        })
        .catch((err) => { if (!stop) setError(String(err)); });
    }, 80);
    return () => { stop = true; window.clearTimeout(timer); };
  }, [qs]);

  useEffect(() => {
    if (!caseId) return;
    setError('');
    fetch(`${api()}/relabel/${caseId}${qs}`)
      .then(async (r) => {
        const data = await r.json().catch(() => null);
        if (!r.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${r.status}`);
        return data as CaseDetail;
      })
      .then((data: CaseDetail) => {
        setDetail(data);
        setRoles(Object.fromEntries((data.pieces || []).map((p) => [p.piece_id, p.role])));
        setSleeve(data.sleeve_style || 'unknown');
        setNotes(data.notes || '');
        setSelected(data.pieces?.[0]?.piece_id || '');
      })
      .catch((err) => setError(String(err)));
  }, [caseId, qs]);

  const remaining = useMemo(() => items.filter((row) => !row.reviewed).length, [items]);

  const save = async () => {
    if (!caseId) return;
    setSaving(true);
    setError('');
    try {
      const response = await fetch(`${api()}/relabel/${caseId}${qs}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ piece_roles: roles, sleeve_style: sleeve, notes, reviewer: 'expert' }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${response.status}`);
      setDetail(data);
      setItems((old) => old.map((row) => (row.case_id === caseId ? { ...row, reviewed: true, sleeve_style: sleeve } : row)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="compose-sandbox relabel-page">
      <header className="sandbox-top">
        <div>
          <strong>{shirt ? '衬衫后育克补标' : 'T恤袖片补标'}</strong>
          <small>原始 DXF {shirt ? '面料' : '闭合'}裁片 · 剩 {remaining}/{items.length || (shirt ? 31 : 10)}</small>
        </div>
        <div className="sandbox-meta">
          <a href={shirt ? '#/relabel' : '#/relabel-yoke'}>{shirt ? '袖片补标' : '后育克补标'}</a>
          <a href="#/shirt-sandbox">衬衫 sandbox</a>
          <a href="#/sandbox">compose</a>
          <a href="#/">回主站</a>
        </div>
      </header>
      <div className="relabel-body">
        <aside className="sandbox-side">
          {items.map((item) => (
            <button
              key={item.case_id}
              className={`relabel-case ${item.case_id === caseId ? 'active' : ''} ${item.reviewed ? 'done' : ''}`}
              onClick={() => setCaseId(item.case_id)}
              type="button"
            >
              <b>{item.case_id}</b>
              <span>{shirt ? (item.issue === 'unlabeled_yoke' ? '未标育克' : item.issue === 'has_yoke_name' ? '有育克名' : '无育克名') : (item.issue === 'no_sleeve' ? 'IR无袖' : '袖≈衣身')}</span>
              {item.reviewed ? <em>已标</em> : null}
            </button>
          ))}
        </aside>
        <section className="relabel-stage">
          {selectedItem && (
            <figure className="relabel-cover">
              <img src={selectedItem.cover_url || ''} alt={caseId} />
              <figcaption>{selectedItem.hint}</figcaption>
            </figure>
          )}
          {detail?.pieces?.length ? (
            <svg className="relabel-svg" viewBox={detail.viewBox} onClick={() => setSelected('')}>
              {detail.pieces.map((piece) => {
                const role = roles[piece.piece_id] || piece.role;
                const color = COLORS[role] || '#777286';
                const on = piece.piece_id === selected;
                const Tag = piece.closed ? 'polygon' : 'polyline';
                return piece.paths.map((path, index) => (
                  <Tag
                    key={`${piece.piece_id}-${index}`}
                    points={path.map((p) => p.join(',')).join(' ')}
                    fill={piece.closed ? `${color}${on ? '55' : '24'}` : on ? `${color}33` : 'none'}
                    stroke={color}
                    strokeWidth={on ? 4 : 1.6}
                    vectorEffect="non-scaling-stroke"
                    onClick={(event) => { event.stopPropagation(); setSelected(piece.piece_id); }}
                  />
                ));
              })}
            </svg>
          ) : <div className="sandbox-empty">加载纸样…</div>}
        </section>
        <aside className="sandbox-side relabel-edit">
          <p>{selectedItem?.hint}</p>
          {!shirt && (
          <fieldset>
            <legend>这件衣服的袖</legend>
            {SLEEVES.map(([slug, zh]) => (
              <label key={slug}><input type="radio" name="sleeve" checked={sleeve === slug} onChange={() => setSleeve(slug)} /> {zh}</label>
            ))}
          </fieldset>
          )}
          <fieldset>
            <legend>选中的片 {selected ? selected.split(':').slice(-2).join(':') : '（点图里的轮廓）'}</legend>
            {roleOptions.map(([slug, zh]) => (
              <button
                key={slug}
                type="button"
                className={selected && roles[selected] === slug ? 'on' : ''}
                disabled={!selected}
                onClick={() => selected && setRoles((old) => ({ ...old, [selected]: slug }))}
              >{zh}</button>
            ))}
            <div className="relabel-legend">
              {(detail?.pieces || []).map((piece) => (
                <button key={piece.piece_id} type="button" className={piece.piece_id === selected ? 'on' : ''} onClick={() => setSelected(piece.piece_id)}>
                  <i style={{ background: COLORS[roles[piece.piece_id] || piece.role] || '#777' }} />
                  {piece.cad_name || roles[piece.piece_id] || piece.role} · {Math.round(piece.width_mm)}×{Math.round(piece.height_mm)}
                  {piece.piece_id.startsWith('unlabeled:') ? ' · 未标注' : ''}
                </button>
              ))}
            </div>
          </fieldset>
          <label>备注
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} />
          </label>
          {error && <p className="sandbox-error">{error}</p>}
          <button className="primary" type="button" disabled={saving || !caseId} onClick={() => void save()}>{saving ? '保存中…' : '保存这件'}</button>
        </aside>
      </div>
    </div>
  );
}
