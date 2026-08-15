import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLanguage } from './Language';
import { aiBase, geometryBase, tryonBase } from './apiBase';
import { Research3D } from './Research3D';
import { garmentPath, necklinePath, specialDesignLines } from './garmentGeometry';

export type CompositionRecipe = {
  family: 'tshirt' | 'shirt';
  sex: 'female' | 'male_general';
  base_case_id: string;
  measurements_cm: Record<string, string>;
  fit: string;
  ease_cm: number;
  material_id: string;
  material_label?: string;
  material_description?: string;
  process_id?: string;
  process_label?: string;
  fabric_color?: string;
  selections: Record<string, string | null>;
  base_option_ids?: Record<string, string | null>;
  intent_constraints?: Record<string, any>;
  execution_mode?: 'simple_piece_swap' | 'shirt_strategy' | 'batch_preview';
};

type Piece = { piece_id: string; role: string; source_case_id?: string; entity_count: number; width_mm?: number; height_mm?: number };
type Validation = {
  valid: boolean;
  trial_ready: boolean;
  errors: string[];
  warnings: string[];
  standard: string;
};
type TryonEdge = { edge_id: string; line_role: string; points_2d_mm: number[][]; length_mm: number; ordered_vertex_ids?: number[]; ordered_vertex_id_sets?: number[][] };
type TryonPanel = { panel_id: string; role: string; vertices_2d_mm: number[][]; triangles: number[][]; mesh_vertices_2d_mm?: number[][]; mesh_triangles?: number[][]; boundary_vertex_ids?: number[]; instances?: string[]; edges: TryonEdge[]; boundary_closed: boolean; area_mm2: number };
export type TryonDescriptor = {
  version: string;
  unit: 'mm';
  recipe_hash: string;
  family: string;
  panels: TryonPanel[];
  seam_pairs: { seam_id: string; kind: string }[];
  validation: { tryon_ready: boolean; errors: string[]; closed_panel_count: number; triangulated_panel_count: number };
};
type ComposeResult = {
  status: 'valid' | 'invalid';
  recipe_hash: string;
  svg: string;
  pieces: Piece[];
  sources: Record<string, string | { case_id: string; option_id: string; confidence: number }>;
  validation: Validation;
  replacement_candidates: Record<string, { option_id: string; label: string }[]>;
  sizing_profile: Record<string, string | number>;
  paper_info: { unit: string; width_mm: number; height_mm: number; recommended_sheet: string };
  tryon_descriptor: TryonDescriptor;
};

const layerLabels: Record<string, string> = {
  front: '前片', back: '后片', sleeve: '袖片', neck: '领片', placket: '门襟', cuff: '袖口', other: '其他辅料',
};
const layerLabelsEn: Record<string, string> = {
  front: 'Front', back: 'Back', sleeve: 'Sleeve', neck: 'Collar / neck', placket: 'Placket', cuff: 'Cuff', other: 'Other pieces',
};

const roleGroups: Record<string, string[]> = {
  front: ['front_body', 'front_left', 'front_right'],
  back: ['back_body', 'back_yoke'],
  sleeve: ['sleeve', 'sleeve_left', 'sleeve_right'],
  neck: ['neck_binding', 'neck_rib', 'collar', 'collar_stand', 'collar_interlining'],
  placket: ['front_placket'],
  cuff: ['cuff', 'rib_cuff', 'sleeve_placket', 'sleeve_placket_extension'],
};

const roleColors: Record<string, string> = {
  front_body: '#3f8f83', front_left: '#3f8f83', front_right: '#3f8f83',
  back_body: '#bd8d79', back_yoke: '#bd8d79', sleeve: '#6f9f91', sleeve_left: '#6f9f91', sleeve_right: '#6f9f91',
  neck_binding: '#9b86d9', neck_rib: '#9b86d9', collar: '#9b86d9', collar_stand: '#9b86d9', collar_interlining: '#9b86d9',
  front_placket: '#d29a45', cuff: '#4d86b4', rib_cuff: '#4d86b4', sleeve_placket: '#d29a45', sleeve_placket_extension: '#d29a45',
};
const layerColors: Record<string, string> = { front: '#3f8f83', back: '#bd8d79', sleeve: '#6f9f91', neck: '#9b86d9', placket: '#d29a45', cuff: '#4d86b4', other: '#777286' };

function panelPoints(panel: TryonPanel): number[][] {
  if (panel.vertices_2d_mm.length > 2) return panel.vertices_2d_mm;
  return panel.edges.flatMap((edge) => edge.points_2d_mm);
}

function PanelPreview({ panel, color }: { panel: TryonPanel; color: string }) {
  const points = panelPoints(panel);
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const minX = Math.min(...xs); const minY = Math.min(...ys);
  const width = Math.max(Math.max(...xs) - minX, 1); const height = Math.max(Math.max(...ys) - minY, 1);
  const boundary = panel.vertices_2d_mm.map((point, index) => `${index ? 'L' : 'M'}${point[0]} ${point[1]}`).join(' ') + (panel.boundary_closed ? ' Z' : '');
  return <svg viewBox={`${minX - width * .05} ${minY - height * .05} ${width * 1.1} ${height * 1.1}`} preserveAspectRatio="xMidYMid meet">
    {boundary && <path d={boundary} fill={panel.boundary_closed ? color : 'none'} fillOpacity=".82" stroke="#5b4d49" strokeWidth={Math.max(width, height) * .006} />}
    {panel.edges.filter((edge) => edge.line_role !== 'cut_line' && edge.line_role !== 'unknown').map((edge) => <polyline key={edge.edge_id} points={edge.points_2d_mm.map((point) => point.join(',')).join(' ')} fill="none" stroke={edge.line_role.includes('notch') ? '#e58a35' : '#786a65'} strokeWidth={Math.max(width, height) * .0035} strokeDasharray={edge.line_role.includes('construction') ? '8 6' : undefined} />)}
  </svg>;
}

function panelExtent(panel: TryonPanel): { width: number; height: number } {
  const points = panelPoints(panel);
  if (!points.length) return { width: 0, height: 0 };
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  return { width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}

function GarmentFlat({ recipe, descriptor, view }: { recipe: CompositionRecipe; descriptor: TryonDescriptor; view: 'front' | 'back' }) {
  const sleeve = String(recipe.intent_constraints?.sleeve || recipe.selections.sleeve || '').split('.').pop() || 'regular';
  const neckline = String(recipe.selections.neckline || recipe.selections.collar || '').toLowerCase();
  const special = String(recipe.selections.special || '').split('.').pop() || '';
  const bodyPanels = descriptor.panels.filter((panel) => panel.role.startsWith(view));
  const sleevePanels = descriptor.panels.filter((panel) => panel.role.startsWith('sleeve'));
  const bodyLength = Math.max(...bodyPanels.map((panel) => panelExtent(panel).height), 1);
  const sleeveLength = Math.max(...sleevePanels.map((panel) => Math.max(panelExtent(panel).width, panelExtent(panel).height)), 0);
  const longSleeve = recipe.family === 'shirt' || sleeveLength > bodyLength * .72;
  const outline = garmentPath(recipe.family, sleeve, longSleeve);
  const neck = necklinePath(neckline, view);
  const details = view === 'front' ? specialDesignLines(special) : [];
  const color = recipe.fabric_color || '#f3eee7';
  return <figure className="garment-flat"><svg viewBox="0 0 600 760" role="img" aria-label={`${view} garment flat`}>
    <defs><linearGradient id={`flat-${view}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor={color} /><stop offset="1" stopColor={color} stopOpacity=".78" /></linearGradient></defs>
    <path d={outline} fill={`url(#flat-${view})`} stroke="#655b59" strokeWidth="3" strokeLinejoin="round" />
    <path d={neck} fill="#fff" stroke="#655b59" strokeWidth="3" />
    <path d="M164 244 Q300 270 436 244" fill="none" stroke="#a59a97" strokeWidth="2" strokeDasharray="8 7" />
    <path d="M176 690 Q300 706 424 690" fill="none" stroke="#a59a97" strokeWidth="2" strokeDasharray="8 7" />
    {view === 'front' && recipe.family === 'shirt' && <><line x1="300" y1="116" x2="300" y2="705" stroke="#8d8582" strokeWidth="2" strokeDasharray="8 6" />{[230, 330, 430, 530].map((y) => <circle key={y} cx="300" cy={y} r="4" fill="#655b59" />)}</>}
    {details.map((line, index) => <path key={index} d={line.d} fill="none" stroke="#9b75b8" strokeWidth="3" strokeDasharray={line.dashed ? '7 5' : undefined} />)}
  </svg></figure>;
}

function DesignOverview({ recipe, patternContext, ready, generationRevision = 0, seedPreviewUrl, onGenerated }: { recipe: CompositionRecipe; patternContext: Record<string, any>; ready: boolean; generationRevision?: number; seedPreviewUrl?: string; onGenerated?: (url: string, input: Record<string, any>, revision: number) => void }) {
  const { t } = useLanguage();
  const [job, setJob] = useState<any>(null);
  const [imageUrl, setImageUrl] = useState(seedPreviewUrl || '');
  const [history, setHistory] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [promptOpen, setPromptOpen] = useState(false);
  const [promptDraft, setPromptDraft] = useState('');
  const [promptLoading, setPromptLoading] = useState(false);
  const autoGenerated = useRef(Boolean(seedPreviewUrl));
  const requestRevision = useRef(0);
  const imageUrlRef = useRef(seedPreviewUrl || '');
  const lastInputRef = useRef<Record<string, any>>({});
  const [imageReady, setImageReady] = useState(Boolean(seedPreviewUrl));
  const mosaic = ['#ee93a0', '#a9ad39', '#8abedf', '#e8ae16', '#ef7b12', '#f29aac', '#c5c879', '#87bcdc'];
  const buildInput = (prompt?: string) => ({
    case_id: recipe.base_case_id,
    family: recipe.family,
    sex: recipe.sex,
    intent: recipe.intent_constraints || {},
    measurements_cm: recipe.measurements_cm,
    selections: recipe.selections,
    fabric_color: recipe.fabric_color || '#ffffff',
    material_id: recipe.material_id,
    material_label: recipe.material_label || '',
    material_description: recipe.material_description || '',
    process_id: recipe.process_id || '',
    process_label: recipe.process_label || '',
    pattern_context: patternContext,
    ...(prompt?.trim() ? { prompt: prompt.trim() } : {}),
  });
  const loadPrompt = async () => {
    setPromptLoading(true);
    try {
      const base = aiBase();
      const response = await fetch(`${base}/design-preview/prompt`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(buildInput(promptDraft)) });
      if (!response.ok) throw new Error(t('无法读取提示词。', 'Unable to load the prompt.'));
      const data = await response.json();
      setPromptDraft(String(data.prompt || ''));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('无法读取提示词。', 'Unable to load the prompt.'));
    } finally {
      setPromptLoading(false);
    }
  };
  const togglePrompt = async () => {
    const next = !promptOpen;
    setPromptOpen(next);
    if (next && !promptDraft.trim()) await loadPrompt();
  };
  const generate = async (withPrompt = false) => {
    if (!ready) return;
    const revisionAtStart = generationRevision;
    const currentRequest = ++requestRevision.current;
    const input = buildInput(withPrompt ? promptDraft : undefined);
    lastInputRef.current = input;
    setError(''); setJob({ status: 'queued', progress: 0 }); setPromptOpen(false); setImageReady(false);
    try {
      const base = aiBase();
      const response = await fetch(`${base}/design-preview/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('2D设计生成服务暂时不可用。', 'The 2D design service is temporarily unavailable.'));
      const created = await response.json(); let current = created;
      while (!['succeeded', 'failed', 'cancelled'].includes(current.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 1800));
        const status = await fetch(`${base}/design-preview/jobs/${created.job_id}`);
        if (!status.ok) throw new Error(t('无法读取生成进度。', 'Unable to read generation progress.'));
        current = await status.json(); if (currentRequest !== requestRevision.current) return; setJob(current);
      }
      if (current.status !== 'succeeded' || !current.result_urls?.[0]) throw new Error(t('2D设计生成失败，请重试。', '2D design generation failed. Please retry.'));
      const finalUrl = `${base}${current.result_urls[0]}`;
      if (imageUrlRef.current) setHistory((old) => [...old.slice(-7), imageUrlRef.current]);
      imageUrlRef.current = finalUrl;
      setImageReady(false);
      setImageUrl(finalUrl);
      if (current.prompt) setPromptDraft(String(current.prompt));
      setJob(null); onGenerated?.(finalUrl, input, revisionAtStart);
    } catch (reason) { if (currentRequest === requestRevision.current) { setJob(null); setError(reason instanceof Error ? reason.message : t('生成失败，请重试。', 'Generation failed. Please retry.')); } }
  };
  const selectPrevious = () => {
    if (!history.length) return;
    const previous = history[history.length - 1];
    setHistory(history.slice(0, -1));
    imageUrlRef.current = previous;
    setImageReady(false);
    setImageUrl(previous);
    onGenerated?.(previous, lastInputRef.current, generationRevision);
  };
  useEffect(() => {
    requestRevision.current += 1;
    setHistory([]);
    setJob(null);
    setError('');
    setPromptOpen(false);
    if (seedPreviewUrl) {
      imageUrlRef.current = seedPreviewUrl;
      setImageReady(false);
      setImageUrl(seedPreviewUrl);
      autoGenerated.current = true;
    } else {
      imageUrlRef.current = '';
      setImageReady(false);
      setImageUrl('');
      autoGenerated.current = false;
    }
  }, [generationRevision]);
  useEffect(() => {
    if (!ready || !recipe.base_case_id || autoGenerated.current) return;
    autoGenerated.current = true; void generate(false);
  }, [recipe.base_case_id, ready, generationRevision]);
  const waiting = Boolean(job) || Boolean(imageUrl && !imageReady) || (!imageUrl && !error);
  return <div className="design-ai-preview">
    {imageUrl && <img src={imageUrl} alt={t('2D设计预览', '2D design preview')} className={imageReady ? 'ready' : ''} onLoad={() => setImageReady(true)} />}
    {waiting && <div className="design-ai-loading" role="status">
      <div className="design-ai-mosaic" aria-hidden="true">{Array.from({ length: 24 }, (_, index) => <i key={index} style={{ background: mosaic[index % mosaic.length], ['--i' as string]: index }} />)}</div>
      <strong>{job ? t('正在生成2D设计预览', 'Generating the 2D design preview') : imageUrl ? t('正在载入预览图', 'Loading the preview') : t('正在准备当前版型与设计参数。', 'Preparing the current pattern and design parameters.')}</strong>
      {job && <progress max="100" value={job.progress || 0} />}
    </div>}
    {error && <p>{error}</p>}
    <div className="design-ai-actions">
      <button className="secondary" disabled={!history.length || Boolean(job)} onClick={selectPrevious}>{t('选上一个', 'Previous')}</button>
      <button className="secondary" disabled={!ready || Boolean(job)} onClick={() => { void generate(false); }}>{t('重新生成', 'Regenerate')}</button>
      <button className={`secondary ${promptOpen ? 'active' : ''}`} disabled={Boolean(job)} onClick={() => { void togglePrompt(); }}>{t('提示词', 'Prompt')}</button>
    </div>
    {promptOpen && <div className="design-ai-prompt">
      <textarea value={promptDraft} onChange={(event) => setPromptDraft(event.target.value)} rows={8} placeholder={promptLoading ? t('正在加载提示词…', 'Loading prompt…') : t('生成提示词', 'Generation prompt')} />
      <div className="design-ai-prompt-actions">
        <button className="secondary" disabled={promptLoading} onClick={() => { void loadPrompt(); }}>{t('重置默认', 'Reset default')}</button>
        <button className="primary" disabled={!ready || !promptDraft.trim()} onClick={() => { void generate(true); }}>{t('用此提示词生成', 'Generate with prompt')}</button>
      </div>
    </div>}
  </div>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '组合服务暂时不可用';
}

function svgViewBox(svg: string): number[] | null {
  const match = svg.match(/viewBox="([^"]+)"/);
  if (!match) return null;
  const parts = match[1].trim().split(/[\s,]+/).map(Number);
  return parts.length === 4 && parts.every(Number.isFinite) ? parts : null;
}

function unionViewBox(a: string, b: string): string {
  const first = svgViewBox(a);
  const second = svgViewBox(b);
  if (!first || !second) return '';
  const minX = Math.min(first[0], second[0]);
  const minY = Math.min(first[1], second[1]);
  const maxX = Math.max(first[0] + first[2], second[0] + second[2]);
  const maxY = Math.max(first[1] + first[3], second[1] + second[3]);
  return `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;
}

function withViewBox(svg: string, viewBox: string): string {
  return viewBox ? svg.replace(/viewBox="[^"]+"/, `viewBox="${viewBox}"`) : svg;
}

export function PatternPreview({ recipe, baseCoverUrl, generationRevision = 0, seedPreviewUrl, styleVersions = [], activeVersionId = '', onRestoreVersion, onGeneratedPreview, onReplaceSelection, onExport, onValidationChange, onCompositionChange }: {
  recipe: CompositionRecipe;
  baseCoverUrl?: string;
  generationRevision?: number;
  seedPreviewUrl?: string;
  styleVersions?: Array<{ id: string; label: string; designUrl: string; pieceCount?: number; recipeHash?: string }>;
  activeVersionId?: string;
  onRestoreVersion?: (version: any) => void;
  onGeneratedPreview?: (url: string, input: Record<string, any>, revision: number) => void;
  onReplaceSelection: (group: string, optionId: string) => void;
  onExport: () => void;
  onValidationChange?: (ready: boolean) => void;
  onCompositionChange?: (summary: { recipe_hash: string; pieces: Piece[]; sizing_profile: Record<string, string | number> }) => void;
}) {
  const { t } = useLanguage();
  const [result, setResult] = useState<ComposeResult | null>(null);
  const [lastValid, setLastValid] = useState<ComposeResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [layers] = useState<Record<string, boolean>>({ front: true, back: true, sleeve: true, neck: true, placket: true, cuff: true, other: true });
  const [informationLayers] = useState<Record<string, boolean>>({ seam: true, fold: true, notch: true, grainline: true });
  const [viewMode, setViewMode] = useState<'design' | 'dxf' | 'tryon'>('design');
  const [panelPreviewAvailable, setPanelPreviewAvailable] = useState(false);
  const [attemptedReplacement, setAttemptedReplacement] = useState('');
  const [showUngraded, setShowUngraded] = useState(false);
  const [ungradedSvg, setUngradedSvg] = useState('');
  const [sharedViewBox, setSharedViewBox] = useState('');
  const [busyLabel, setBusyLabel] = useState('');
  const [composedRecipeKey, setComposedRecipeKey] = useState('');
  const revision = useRef(0);
  const ungradedCase = useRef('');
  const panDrag = useRef<{ x: number; y: number; originX: number; originY: number } | null>(null);
  const validationCallback = useRef(onValidationChange);
  const compositionCallback = useRef(onCompositionChange);
  const serializedRecipe = useMemo(() => JSON.stringify({ ...recipe, compact_layout: true }), [recipe]);

  useEffect(() => { validationCallback.current = onValidationChange; }, [onValidationChange]);
  useEffect(() => { compositionCallback.current = onCompositionChange; }, [onCompositionChange]);
  useEffect(() => { setShowUngraded(false); setUngradedSvg(''); setSharedViewBox(''); ungradedCase.current = ''; }, [serializedRecipe]);
  useEffect(() => {
    const controller = new AbortController();
    const base = tryonBase();
    fetch(`${base}/research/health`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((health) => setPanelPreviewAvailable(Boolean(health?.enabled && health?.cloth_solver_available)))
      .catch(() => setPanelPreviewAvailable(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const requestRevision = ++revision.current;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      if (!recipe.base_case_id) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setError('');
      try {
        const base = geometryBase();
        const response = await fetch(`${base}/compose`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: serializedRecipe,
          signal: controller.signal,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '纸样组合失败');
        if (requestRevision !== revision.current) return;
        setResult(data);
        if (data.status === 'valid') {
          setLastValid(data);
          setComposedRecipeKey(serializedRecipe);
          setAttemptedReplacement('');
          compositionCallback.current?.({ recipe_hash: data.recipe_hash, pieces: data.pieces, sizing_profile: data.sizing_profile });
        }
      } catch (requestError) {
        if (!controller.signal.aborted && requestRevision === revision.current) setError(errorMessage(requestError));
      } finally {
        if (requestRevision === revision.current) setLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [serializedRecipe]);

  const visibleResult = result?.status === 'valid' ? result : lastValid || result;
  const currentReady = Boolean(!loading && result?.status === 'valid' && composedRecipeKey === serializedRecipe);
  const currentTryonDescriptor = result?.status === 'valid' ? result.tryon_descriptor : undefined;
  const currentTryonReady = Boolean(
    currentReady
    && currentTryonDescriptor?.version === 'patternmate.tryon.v2'
    && currentTryonDescriptor.recipe_hash === result?.recipe_hash
    && currentTryonDescriptor.validation?.tryon_ready,
  );
  useEffect(() => {
    validationCallback.current?.(currentReady);
  }, [currentReady]);
  const hiddenRoles = Object.entries(layers).flatMap(([group, visible]) => visible ? [] : (roleGroups[group] || []));
  const hiddenInformation = Object.entries(informationLayers).flatMap(([kind, visible]) => visible ? [] : [kind]);
  const rawSvg = (showUngraded && ungradedSvg) || visibleResult?.svg || '';
  const svg = sharedViewBox ? withViewBox(rawSvg, sharedViewBox) : rawSvg;
  const designPatternContext = { recipe_hash: visibleResult?.recipe_hash || '', pieces: (visibleResult?.pieces || []).map((piece) => ({ role: piece.role, width_mm: piece.width_mm, height_mm: piece.height_mm })), sizing_profile: visibleResult?.sizing_profile || {} };
  const designReady = Boolean(!loading && result?.status === 'valid' && composedRecipeKey === serializedRecipe);
  const grade = visibleResult?.sizing_profile;
  const gradeHint = viewMode === 'dxf' && grade
    ? showUngraded
      ? t(' · 原始尺寸', ' · source size')
      : t(` · 放码 宽×${Number(grade.width).toFixed(2)} 长×${Number(grade.length).toFixed(2)} 袖×${Number(grade.sleeve_length).toFixed(2)}`, ` · graded W×${Number(grade.width).toFixed(2)} L×${Number(grade.length).toFixed(2)} sleeve×${Number(grade.sleeve_length).toFixed(2)}`)
    : '';
  const hasComponentAdjustments = Object.entries(recipe.selections).some(([group, optionId]) => Boolean(optionId) && optionId !== recipe.base_option_ids?.[group]);
  const replacement = result?.status === 'invalid'
    ? Object.entries(result.replacement_candidates || {}).find(([group, values]) => values.length > 0 && `${group}:${values[0].option_id}` !== attemptedReplacement)
    : undefined;
  const startPan = (event: React.PointerEvent<HTMLDivElement>) => {
    panDrag.current = { x: event.clientX, y: event.clientY, originX: pan.x, originY: pan.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const movePan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!panDrag.current) return;
    setPan({ x: panDrag.current.originX + event.clientX - panDrag.current.x, y: panDrag.current.originY + event.clientY - panDrag.current.y });
  };
  const wheelZoom = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setZoom((value) => Math.max(.35, Math.min(3.5, value * (event.deltaY < 0 ? 1.12 : .89))));
  };
  const toggleUngraded = async () => {
    if (showUngraded) {
      setShowUngraded(false);
      return;
    }
    setViewMode('dxf');
    if (ungradedSvg && ungradedCase.current === serializedRecipe) {
      setShowUngraded(true);
      return;
    }
    setBusyLabel(t('正在加载放码前纸样', 'Loading ungraded pattern'));
    try {
      const base = geometryBase();
      const response = await fetch(`${base}/compose`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ...JSON.parse(serializedRecipe), skip_grading: true }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data?.svg) throw new Error(typeof data?.detail === 'string' ? data.detail : '无法加载放码前 DXF');
      ungradedCase.current = serializedRecipe;
      setSharedViewBox(unionViewBox(visibleResult?.svg || '', data.svg));
      setUngradedSvg(data.svg);
      setShowUngraded(true);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusyLabel('');
    }
  };

  return <div className="pattern-workbench" style={{ '--fabric-color': recipe.fabric_color || '#ffffff' } as React.CSSProperties}>
    <div className={`pattern-stage ${viewMode === 'design' ? 'design-mode' : ''}`}>
      <div className="pattern-title">{viewMode === 'design' ? t('设计预览', 'Design preview') : viewMode === 'tryon' ? t('3D 试穿', '3D try-on') : showUngraded ? t('纸样预览（放码前）', 'Pattern preview (before grading)') : t('纸样预览', 'Pattern preview')} · {recipe.family === 'tshirt' ? t('T恤', 'T-shirt') : t('衬衫', 'Shirt')}{recipe.base_case_id ? ` · ${recipe.base_case_id}` : ''}{gradeHint}</div>
      <div className="pattern-view-switch"><button className={viewMode === 'dxf' ? 'active' : ''} onClick={() => setViewMode('dxf')}>{t('专业DXF', 'Professional DXF')}</button><button className={viewMode === 'design' ? 'active' : ''} onClick={() => setViewMode('design')}>{t('2D设计预览', '2D Preview')}</button>{recipe.family === 'tshirt' && <button disabled={!panelPreviewAvailable || !currentTryonReady} title={!panelPreviewAvailable ? t('请启用 3D 服务以查看试穿效果', 'Enable the 3D service to inspect the try-on preview') : !currentTryonReady ? t(`当前 DXF 尚未具备完整试穿接口：${currentTryonDescriptor?.validation?.errors?.join('、') || '请先生成并通过校验'}`, `The current DXF is not ready for try-on: ${currentTryonDescriptor?.validation?.errors?.join(', ') || 'generate and validate it first'}`) : t('使用当前 DXF、人体与面料进行布料仿真', 'Simulate the current DXF with the body and fabric')} className={viewMode === 'tryon' ? 'active' : ''} onClick={() => currentTryonReady && setViewMode('tryon')}>{t('3D试穿预览', '3D Try-on Preview')}</button>}</div>
      <div className="design-preview-stage" style={{ display: viewMode === 'design' ? undefined : 'none' }}>
        <DesignOverview recipe={recipe} patternContext={designPatternContext} ready={designReady} generationRevision={generationRevision} seedPreviewUrl={seedPreviewUrl} onGenerated={onGeneratedPreview} />
        {styleVersions.length > 0 && <div className="design-version-rail" aria-label={t('搭配版本', 'Style versions')}>
          {styleVersions.map((version) => (
            <button key={version.id} type="button" className={version.id === activeVersionId ? 'active' : ''} title={version.label} onClick={() => onRestoreVersion?.(version)}>
              <img src={version.designUrl} alt="" />
              <em>{version.label}</em>
            </button>
          ))}
        </div>}
      </div>
      {viewMode === 'tryon' && currentTryonDescriptor ? <div className="tryon-preview-stage"><Research3D mode="tryon" measurements={recipe.measurements_cm} sex={recipe.sex} recipe={recipe} composition={currentTryonDescriptor} /></div> : viewMode !== 'design' && <div className="pattern-scroll pannable" style={{ '--grid-size': `${32 * zoom}px` } as React.CSSProperties} onPointerDown={startPan} onPointerMove={movePan} onPointerUp={() => { panDrag.current = null; }} onPointerCancel={() => { panDrag.current = null; }} onWheel={wheelZoom}>
        {svg
          ? <div className="dxf-svg complete-dxf" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }} dangerouslySetInnerHTML={{ __html: svg }} />
          : <div className="dxf-empty">{t('正在准备完整纸样…', 'Preparing complete pattern…')}</div>}
      </div>}
      {hiddenRoles.length > 0 && <style>{hiddenRoles.map((role) => `.complete-dxf [data-piece-role="${role}"]{display:none}`).join('')}</style>}
      {hiddenInformation.length > 0 && <style>{hiddenInformation.map((kind) => `.complete-dxf [data-line-kind="${kind}"]{display:none}`).join('')}</style>}
      { (loading || busyLabel) && <div className="compose-status" role="status" aria-live="polite"><span className="compose-spinner" /><strong>{busyLabel || t('正在生成版片', 'Generating pattern')}</strong></div>}
      {error && <div className="compose-error"><strong>{t('无法生成当前组合', 'Unable to generate this combination')}</strong><span>{error}</span><button onClick={() => setError('')}>{t('保留上一个有效结果', 'Keep previous valid result')}</button></div>}
      {result?.status === 'invalid' && hasComponentAdjustments && <div className="compose-error">
        <strong>{t('当前组合未通过校验', 'Combination failed validation')}</strong>
        <span>{result.validation.errors.join('；')}</span>
        {!replacement && <span>{t('没有通过服务端预校验的自动替代项，请保留上一个结果并手动调整版片。', 'No server-validated replacement is available. Keep the previous result and adjust manually.')}</span>}
        <div><button onClick={() => setResult(lastValid)}>{t('保留上一个', 'Keep previous')}</button>{replacement && <button className="primary" onClick={() => { setAttemptedReplacement(`${replacement[0]}:${replacement[1][0].option_id}`); onReplaceSelection(replacement[0], replacement[1][0].option_id); }}>{t('使用已验证的', 'Use validated')} “{replacement[1][0].label}”</button>}</div>
      </div>}
      {viewMode === 'dxf' && <div className="pattern-tools"><button onClick={() => setZoom((value) => Math.max(.35, value - .15))}>－</button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => Math.min(3.5, value + .15))}>＋</button><button className="icon" data-tip={t('自动排版：重置缩放并居中纸样', 'Fit layout: reset zoom and center the pattern')} aria-label={t('自动排版：重置缩放并居中纸样', 'Fit layout: reset zoom and center the pattern')} onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="2" width="7" height="9" rx="1" /><rect x="11" y="2" width="7" height="5" rx="1" /><rect x="11" y="9" width="7" height="9" rx="1" /><rect x="2" y="13" width="7" height="5" rx="1" /></svg></button><button className={`icon ${showUngraded ? 'active' : ''}`} data-tip={showUngraded ? t('切回放码后：按当前人体尺寸缩放后的纸样', 'Back to graded DXF sized to the current body') : t('查看放码前：尚未按人体尺寸缩放的原始纸样', 'Original DXF before body grading')} aria-label={showUngraded ? t('切回放码后：按当前人体尺寸缩放后的纸样', 'Back to graded DXF sized to the current body') : t('查看放码前：尚未按人体尺寸缩放的原始纸样', 'Original DXF before body grading')} onClick={() => { void toggleUngraded(); }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3.5h8.5v13H4z" /><path d="M7.5 3.5V16.5H16V6.5H11.5V3.5z" /></svg></button><button className="icon pattern-download" disabled={!currentReady} data-tip={t('下载当前试样 DXF', 'Download current trial DXF')} aria-label={t('下载当前试样 DXF', 'Download current trial DXF')} onClick={onExport}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3.5v9" /><path d="M6.5 9.5 10 13l3.5-3.5" /><path d="M4 16.5h12" /></svg></button></div>}
    </div>
  </div>;
}
