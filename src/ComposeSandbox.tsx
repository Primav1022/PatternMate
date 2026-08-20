import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CompositionReviewPanel } from './CompositionReviewPanel';
import { defaultSelections, fabricOptions, GarmentFamily, groupLabels, groupOrder, optionsForFamily } from './catalogs';

const FABRIC_COLORS = ['#ffffff', '#f4f1e8', '#22242a', '#aeb6c2', '#b8d3e8', '#6f8fae', '#d9c3ad', '#b86f62', '#d8a6b7', '#8ea88c', '#5f705e', '#d6b85f'];

type CatalogItem = {
  case_id: string;
  category: string;
  supported: boolean;
  cover_url?: string;
  semantics?: Record<string, any>;
  base_option_ids?: Record<string, string | null>;
};

type ComposeResponse = {
  status: string;
  recipe_hash: string;
  svg: string;
  pieces: { piece_id: string; role: string; width_mm?: number; height_mm?: number; source_case_id?: string }[];
  validation: { valid: boolean; trial_ready: boolean; errors: string[]; warnings: string[]; standard?: string };
  sources?: Record<string, any>;
  component_results?: any[];
  review_ledger?: any;
  batch_plan?: any;
  sizing_profile?: Record<string, string | number>;
  paper_info?: { width_mm?: number; height_mm?: number; recommended_sheet?: string };
  execution_mode?: string;
};

type ComposeRecipe = {
  family: GarmentFamily;
  sex: 'female' | 'male_general';
  base_case_id: string;
  measurements_cm: Record<string, string>;
  fit: string;
  ease_cm: number;
  material_id: string;
  fabric_color: string;
  selections: Record<string, string | null>;
  base_option_ids: Record<string, string | null>;
  intent_constraints: Record<string, never>;
  execution_mode: string;
};

const GEOMETRY = import.meta.env.VITE_GEOMETRY_BASE_URL || 'http://127.0.0.1:8788';

const defaultMeasures = {
  height: '160', chest: '85', waist: '60', shoulder: '38', neck: '32', sleeveLength: '50.5', upperArm: '25',
};

function familyOf(item?: CatalogItem | null): GarmentFamily {
  return item?.category === 'shirt' || item?.category === 'blouse' ? 'shirt' : 'tshirt';
}

export function ComposeSandbox() {
  const [health, setHealth] = useState<string>('checking…');
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [caseId, setCaseId] = useState('');
  const [sex, setSex] = useState<'female' | 'male_general'>('female');
  const [fit, setFit] = useState('regular');
  const [measures, setMeasures] = useState(defaultMeasures);
  const [selections, setSelections] = useState<Record<string, string | null>>(defaultSelections('tshirt'));
  const [materialId, setMaterialId] = useState(fabricOptions.find((item) => item.family === 'tshirt')?.id || '');
  const [fabricColor, setFabricColor] = useState('#ffffff');
  const [auto, setAuto] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ComposeResponse | null>(null);
  const [previewMode, setPreviewMode] = useState<'base' | 'compose' | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [showJson, setShowJson] = useState(false);
  const panDrag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const revision = useRef(0);
  const skipAutoAfterBase = useRef(false);

  const selected = items.find((item) => item.case_id === caseId) || null;
  const family = familyOf(selected);
  const options = optionsForFamily(family);
  const fabrics = fabricOptions.filter((item) => item.family === family);
  const selectedFabric = fabrics.find((item) => item.id === materialId) || fabrics[0];

  const recipe = useMemo<ComposeRecipe>(() => ({
    family,
    sex,
    base_case_id: caseId,
    measurements_cm: measures,
    fit,
    ease_cm: fit === 'relaxed' || fit === 'oversized' ? 12 : 8,
    material_id: materialId,
    fabric_color: fabricColor,
    selections,
    base_option_ids: selected?.base_option_ids || {},
    intent_constraints: {},
    execution_mode: family === 'tshirt' ? 'simple_piece_swap' : 'shirt_strategy',
  }), [family, sex, caseId, measures, fit, materialId, fabricColor, selections, selected]);

  const runCompose = useCallback(async (payload?: ComposeRecipe, mode: 'base' | 'compose' = 'compose') => {
    const body = payload || recipe;
    if (!body.base_case_id) return;
    const rev = ++revision.current;
    setLoading(true);
    setError('');
    const started = performance.now();
    try {
      const response = await fetch(`${GEOMETRY}/compose`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => null);
      if (rev !== revision.current) return;
      setElapsedMs(Math.round(performance.now() - started));
      if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${response.status}`);
      setResult(data);
      setPreviewMode(mode);
    } catch (err) {
      if (rev === revision.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (rev === revision.current) setLoading(false);
    }
  }, [recipe]);

  useEffect(() => {
    fetch(`${GEOMETRY}/health`).then((r) => r.json()).then((d) => {
      setHealth(d.ok ? `ok · ir ${d.ir_count} · tshirt ${d.tshirt_v2_count} · shirt ${d.shirt_v2_count}` : 'unhealthy');
    }).catch(() => setHealth('offline'));
    fetch(`${GEOMETRY}/catalog`).then((r) => r.json()).then((d) => {
      const rows = (d.items || []) as CatalogItem[];
      const supported = rows.filter((item) => item.supported);
      const nextItems = supported.length ? supported : rows;
      setItems(nextItems);
      const first = nextItems[0];
      if (first) setCaseId(first.case_id);
    }).catch((err) => setError(String(err)));
  }, []);

  // Selecting a catalog case resets components to base and previews the base pattern.
  useEffect(() => {
    const item = items.find((row) => row.case_id === caseId);
    if (!item || !caseId) return;
    const nextFamily = familyOf(item);
    const baseSelections = { ...defaultSelections(nextFamily), ...(item.base_option_ids || {}) };
    const nextMaterial = fabricOptions.some((row) => row.id === materialId && row.family === nextFamily)
      ? materialId
      : (fabricOptions.find((row) => row.family === nextFamily)?.id || '');
    setSelections(baseSelections);
    setMaterialId(nextMaterial);
    setResult(null);
    setPreviewMode(null);
    setZoom(1);
    setPan({ x: 0, y: 0 });
    skipAutoAfterBase.current = true;
    const baseRecipe: ComposeRecipe = {
      family: nextFamily,
      sex,
      base_case_id: caseId,
      measurements_cm: measures,
      fit,
      ease_cm: fit === 'relaxed' || fit === 'oversized' ? 12 : 8,
      material_id: nextMaterial,
      fabric_color: fabricColor,
      selections: baseSelections,
      base_option_ids: item.base_option_ids || {},
      intent_constraints: {},
      execution_mode: nextFamily === 'tshirt' ? 'simple_piece_swap' : 'shirt_strategy',
    };
    void runCompose(baseRecipe, 'base');
  }, [caseId, items]);

  useEffect(() => {
    document.documentElement.style.setProperty('--fabric-color', fabricColor);
  }, [fabricColor]);

  useEffect(() => {
    if (!auto || !caseId) return;
    if (skipAutoAfterBase.current) {
      skipAutoAfterBase.current = false;
      return;
    }
    const timer = window.setTimeout(() => { void runCompose(undefined, 'compose'); }, 350);
    return () => window.clearTimeout(timer);
  }, [auto, JSON.stringify(recipe), runCompose, caseId]);

  const setSelection = (group: string, optionId: string | null) => {
    setSelections((old) => ({ ...old, [group]: optionId }));
  };

  return (
    <div className="compose-sandbox">
      <header className="sandbox-top">
        <div>
          <strong>Compose Sandbox</strong>
          <small>DXF geometry · {GEOMETRY}</small>
        </div>
        <div className="sandbox-meta">
          <span>{health}</span>
          {elapsedMs != null && <span>{elapsedMs} ms</span>}
          {result && <span className={result.status === 'valid' ? 'ok' : 'bad'}>{result.status}</span>}
          {previewMode && <span className="sandbox-preview-mode">{previewMode === 'base' ? 'base 预览' : 'compose'}</span>}
          <a href="#/relabel-yoke">后育克补标</a>
          <a href="#/relabel">袖片补标</a>
          <a href="#/sleeve-vlm">袖片 VLM</a>
          <a href="#/shirt-sandbox">衬衫 sandbox</a>
          <a href="/">← 回主站</a>
        </div>
      </header>

      <div className="sandbox-body">
        <aside className="sandbox-side">
          <label>Base case
            <select value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={!items.length}>
              {!items.length && <option value="">加载 catalog…</option>}
              {items.map((item) => (
                <option key={item.case_id} value={item.case_id}>
                  {item.case_id} · {item.category}{item.supported ? '' : ' (unsupported)'}
                </option>
              ))}
            </select>
          </label>

          <div className="sandbox-row">
            <label>Sex
              <select value={sex} onChange={(e) => setSex(e.target.value as 'female' | 'male_general')}>
                <option value="female">female</option>
                <option value="male_general">male_general</option>
              </select>
            </label>
            <label>Fit
              <select value={fit} onChange={(e) => setFit(e.target.value)}>
                {['regular', 'relaxed', 'oversized', 'fitted'].map((value) => <option key={value} value={value}>{value}</option>)}
              </select>
            </label>
          </div>

          <fieldset>
            <legend>Measurements (cm)</legend>
            <div className="sandbox-measures">
              {Object.entries(measures).map(([key, value]) => (
                <label key={key}>{key}
                  <input value={value} onChange={(e) => setMeasures((old) => ({ ...old, [key]: e.target.value }))} />
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>面料 · Fabric</legend>
            <label>材质
              <select
                value={materialId}
                onChange={(e) => setMaterialId(e.target.value)}
                disabled={!fabrics.length}
              >
                {fabrics.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} · {item.group}/{item.slug}
                  </option>
                ))}
              </select>
            </label>
            {selectedFabric && (
              <small className="sandbox-fabric-note">{selectedFabric.description}</small>
            )}
            <div className="sandbox-fabric-color">
              <span>颜色</span>
              <div className="sandbox-swatches">
                {FABRIC_COLORS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    aria-label={color}
                    className={fabricColor === color ? 'selected' : ''}
                    style={{ background: color }}
                    onClick={() => setFabricColor(color)}
                  />
                ))}
                <label className="sandbox-swatch-custom" title="自定义颜色">
                  <input type="color" value={fabricColor} onChange={(e) => setFabricColor(e.target.value)} />
                  <span>＋</span>
                </label>
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend>Components · {family}</legend>
            {groupOrder[family].map((group) => (
              <label key={group}>{groupLabels[group] || group}
                <select value={selections[group] || ''} onChange={(e) => setSelection(group, e.target.value || null)}>
                  <option value="">(base / none)</option>
                  {options.filter((item) => item.group === group).map((item) => (
                    <option key={item.id} value={item.id}>{item.label_zh} · {item.slug}</option>
                  ))}
                </select>
              </label>
            ))}
          </fieldset>

          <div className="sandbox-actions">
            <label className="sandbox-auto"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> 自动 compose</label>
            <button className="primary" disabled={!caseId || loading} onClick={() => void runCompose(undefined, 'compose')}>
              {loading ? '生成中…' : 'Compose'}
            </button>
            <button type="button" onClick={() => setShowJson((v) => !v)}>{showJson ? '隐藏 JSON' : '看 JSON'}</button>
          </div>

          {error && <p className="sandbox-error">{error}</p>}

          {result && (
            <div className="sandbox-stats">
              <div><b>hash</b> {result.recipe_hash}</div>
              <div><b>pieces</b> {result.pieces?.length || 0}</div>
              <div><b>trial</b> {String(result.validation?.trial_ready)}</div>
              <div><b>mode</b> {result.execution_mode || '—'}</div>
              <div><b>fabric</b> {selectedFabric?.label || materialId || '—'} · {fabricColor}</div>
              {result.sizing_profile?.material_shrink_rate != null && (
                <div><b>shrink</b> {Number(result.sizing_profile.material_shrink_rate) * 100}%</div>
              )}
              {result.paper_info && <div><b>paper</b> {result.paper_info.width_mm}×{result.paper_info.height_mm} mm</div>}
              {(result.validation?.errors || []).map((msg) => <em key={msg}>{msg}</em>)}
              {(result.validation?.warnings || []).slice(0, 4).map((msg) => <small key={msg}>{msg}</small>)}
            </div>
          )}

          <CompositionReviewPanel
            componentResults={result?.component_results}
            reviewLedger={result?.review_ledger}
            warnings={result?.validation?.warnings}
          />
        </aside>

        <section className="sandbox-stage">
          <div className="sandbox-tools">
            <button onClick={() => setZoom((z) => Math.max(0.3, z - 0.15))}>－</button>
            <span>{Math.round(zoom * 100)}%</span>
            <button onClick={() => setZoom((z) => Math.min(4, z + 0.15))}>＋</button>
            <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>重置视图</button>
          </div>
          <div
            className="sandbox-canvas pannable"
            onPointerDown={(e) => { panDrag.current = { x: e.clientX, y: e.clientY, ox: pan.x, oy: pan.y }; e.currentTarget.setPointerCapture(e.pointerId); }}
            onPointerMove={(e) => { if (!panDrag.current) return; setPan({ x: panDrag.current.ox + e.clientX - panDrag.current.x, y: panDrag.current.oy + e.clientY - panDrag.current.y }); }}
            onPointerUp={() => { panDrag.current = null; }}
            onWheel={(e) => { e.preventDefault(); setZoom((z) => Math.max(0.3, Math.min(4, z * (e.deltaY < 0 ? 1.1 : 0.9)))); }}
          >
            {result?.svg
              ? <div className="dxf-svg complete-dxf" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }} dangerouslySetInnerHTML={{ __html: result.svg }} />
              : <div className="sandbox-empty">{loading ? '正在加载 base…' : '选择 catalog 后将自动展示 base'}</div>}
          </div>
          {showJson && result && (
            <pre className="sandbox-json">{JSON.stringify({
              status: result.status,
              recipe_hash: result.recipe_hash,
              validation: result.validation,
              pieces: result.pieces,
              sources: result.sources,
              component_results: result.component_results,
              batch_plan: result.batch_plan,
              sizing_profile: result.sizing_profile,
              recipe,
            }, null, 2)}</pre>
          )}
        </section>
      </div>
    </div>
  );
}
