import React from 'react';
import { useLanguage } from './Language';
import { aiBase, textBase } from './apiBase';
import { taobaoPrintFabrics, TaobaoPrintFabric } from './catalogs';
import './PrintDesign.css';

export type PrintAsset = { id: string; name: string; src: string };
export type FaceSettings = { density: number; size: number; gap: number; x: number; y: number; rotation: number };
export type PrintFaceMode = 'none' | 'density' | 'manual';
export type PrintPlacement = { id: string; view: 'front' | 'back'; assetId: string; x: number; y: number; size: number; rotation: number; cropLeft: number; cropRight: number; cropTop: number; cropBottom: number };
export type ProductionPrintAsset = { url: string; mode: 'motif' | 'seamless'; format: 'PNG'; width_px: number; height_px: number; dpi: number; color_space: string; transparent: boolean };
export type AdoptedPrintConcept = { preview_url: string; production_asset: ProductionPrintAsset; input: Record<string, any> };
export type PrintZone = { x: number; y: number; w: number; h: number };
export type PrintBrief = { type?: 'small' | 'chest' | 'allover' | null; motif?: string; style_prompt?: string; placement?: 'left' | 'center' | 'right' | null; density?: 'sparse' | 'medium' | 'dense' | null; zone?: PrintZone; ready_to_generate?: boolean };
export type PrintOverlay = { src: string; type: 'small' | 'chest' | 'allover'; x: number; y: number; w: number; h: number; tile: number; gap: number };
type GeneratedDraft = { id: string; src: string; productionAsset: ProductionPrintAsset; input: Record<string, any> };
type PrintOption = { value: string; label_zh: string; label_en: string };
type PrintConversationTurn = { id: string; role: 'user' | 'assistant'; text: string; options?: PrintOption[]; drafts?: GeneratedDraft[] };
const MOCK_GARMENT = '/home-gallery/garment-01.png';
const MOSAIC = ['#ee93a0', '#a9ad39', '#8abedf', '#e8ae16', '#ef7b12', '#f29aac', '#c5c879', '#87bcdc'];
const TYPE_CHIPS: PrintOption[] = [
  { value: 'small', label_zh: '小印花', label_en: 'Small motif' },
  { value: 'chest', label_zh: '胸前印花', label_en: 'Chest print' },
  { value: 'allover', label_zh: '全身印花', label_en: 'All-over print' },
];
const TYPE_LABEL: Record<string, [string, string]> = { small: ['小印花', 'small motif'], chest: ['胸前印花', 'chest print'], allover: ['全身印花', 'all-over print'] };
const CHIP_LABELS: PrintOption[] = [
  ...TYPE_CHIPS,
  { value: 'left', label_zh: '偏左', label_en: 'Left' },
  { value: 'center', label_zh: '居中', label_en: 'Center' },
  { value: 'right', label_zh: '偏右', label_en: 'Right' },
  { value: 'sparse', label_zh: '疏一些', label_en: 'Sparse' },
  { value: 'medium', label_zh: '适中', label_en: 'Medium' },
  { value: 'dense', label_zh: '密一些', label_en: 'Dense' },
  { value: 'generate', label_zh: '生成印花图', label_en: 'Generate prints' },
];

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function defaultOverlay(type: PrintOverlay['type'], src: string, density?: PrintBrief['density']): PrintOverlay {
  if (type === 'chest') return { src, type, x: 0.3, y: 0.16, w: 0.34, h: 0.28, tile: 0.18, gap: 0.04 };
  if (type === 'allover') return { src, type, x: 0, y: 0, w: 1, h: 1, tile: density === 'dense' ? 0.14 : density === 'sparse' ? 0.28 : 0.2, gap: density === 'dense' ? 0.01 : density === 'sparse' ? 0.08 : 0.04 };
  return { src, type, x: 0.41, y: 0.2, w: 0.16, h: 0.16, tile: 0.18, gap: 0.04 };
}

function loadImage(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    if (!src.startsWith('data:')) image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('image'));
    image.src = src;
  });
}

export async function flattenMotif(garmentUrl: string, overlay: PrintOverlay) {
  const [garment, motif] = await Promise.all([loadImage(garmentUrl), loadImage(overlay.src)]);
  const canvas = document.createElement('canvas');
  canvas.width = garment.naturalWidth;
  canvas.height = garment.naturalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas');
  ctx.drawImage(garment, 0, 0);
  ctx.drawImage(motif, overlay.x * canvas.width, overlay.y * canvas.height, overlay.w * canvas.width, overlay.h * canvas.height);
  return canvas.toDataURL('image/png');
}

function jpegDataUrl(image: HTMLImageElement, max = 768) {
  const scale = Math.min(1, max / Math.max(image.naturalWidth, image.naturalHeight, 1));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', 0.88);
}

async function toJpegDataUrl(src: string, max = 768) {
  return jpegDataUrl(await loadImage(src), max);
}

async function cropFabricFace(src: string) {
  const image = await loadImage(src);
  const w = image.naturalWidth;
  const h = image.naturalHeight;
  if (h / w < 1.15) return jpegDataUrl(image);
  const sy = Math.round(h * 0.08);
  const sh = Math.round(h * 0.41);
  const size = Math.min(w, sh);
  const sx = Math.round((w - size) / 2);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 512;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas');
  ctx.drawImage(image, sx, sy, size, size, 0, 0, 512, 512);
  return canvas.toDataURL('image/jpeg', 0.88);
}

export async function renderAlloverFabric(overlay: PrintOverlay, size = 1024) {
  const motif = await loadImage(overlay.src);
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas');
  ctx.fillStyle = '#f4efe8';
  ctx.fillRect(0, 0, size, size);
  const tile = Math.max(36, Math.round(overlay.tile * size));
  const step = tile + Math.round(overlay.gap * size);
  for (let y = 0; y < size; y += step) for (let x = 0; x < size; x += step) ctx.drawImage(motif, x, y, tile, tile);
  return canvas.toDataURL('image/png');
}

function zoneRect(brief: PrintBrief): PrintZone {
  if (brief.zone) return brief.zone;
  const base = brief.type === 'allover' ? { x: 0.08, y: 0.08, w: 0.84, h: 0.84 } : brief.type === 'small' ? { x: 0.38, y: 0.2, w: 0.24, h: 0.18 } : { x: 0.28, y: 0.16, w: 0.44, h: 0.28 };
  if (brief.placement === 'left') base.x -= 0.12;
  if (brief.placement === 'right') base.x += 0.12;
  return base;
}

const CHIP_TEXT = new Set(CHIP_LABELS.flatMap((item) => [item.value, item.label_zh, item.label_en]));

function styleNotes(history: PrintConversationTurn[]) {
  return history.filter((turn) => turn.role === 'user' && !CHIP_TEXT.has(turn.text)).map((turn) => turn.text);
}

function briefPrompt(brief: PrintBrief, extra = '', notes: string[] = []) {
  const type = brief.type ? TYPE_LABEL[brief.type]?.[0] : '';
  const style = brief.style_prompt || Array.from(new Set([brief.motif, ...notes].filter(Boolean))).join('，');
  const zone = zoneRect(brief);
  const zoneText = brief.type === 'allover'
    ? '全身印花：前片、袖子、克夫、可见侧片和后片的布面都要铺上同一块布的连续花纹，不要只印前片，皮肤和背景保持不变。'
    : `前片放置框：距左${Math.round(zone.x * 100)}%，距上${Math.round(zone.y * 100)}%，宽${Math.round(zone.w * 100)}%，高${Math.round(zone.h * 100)}%。把印花放进这个框，不要移到框外。`;
  return [type, style, brief.type !== 'allover' && brief.placement && `位置${brief.placement === 'left' ? '偏左' : brief.placement === 'right' ? '偏右' : '居中'}`, brief.density && `排列${brief.density === 'sparse' ? '稀疏' : brief.density === 'dense' ? '紧密' : '适中'}`, zoneText, extra].filter(Boolean).join('，');
}

function PrintPlacementMap({ svg, brief, onZoneChange }: { svg?: string; brief: PrintBrief; onZoneChange?: (zone: PrintZone) => void }) {
  const { t } = useLanguage();
  const bodyRef = React.useRef<HTMLDivElement>(null);
  const drag = React.useRef<{ x: number; y: number; zx: number; zy: number } | null>(null);
  const zone = zoneRect(brief);
  const startDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, zx: zone.x, zy: zone.y };
  };
  const moveDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!drag.current || !bodyRef.current) return;
    const box = bodyRef.current.getBoundingClientRect();
    const next = {
      ...zone,
      x: Math.min(1 - zone.w, Math.max(0, drag.current.zx + (event.clientX - drag.current.x) / box.width)),
      y: Math.min(1 - zone.h, Math.max(0, drag.current.zy + (event.clientY - drag.current.y) / box.height)),
    };
    onZoneChange?.(next);
  };
  const endDrag = () => { drag.current = null; };
  if (!brief.type) return null;
  const title = brief.type === 'allover' ? t('前片满幅排列区', 'Front all-over zone') : t('前片图案放置区', 'Front placement zone');
  return <div className="print-dxf-map" aria-label={title}>
    <small>{title} · {t('拖抓手调整', 'Drag to place')}{brief.density ? ` · ${brief.density === 'sparse' ? t('疏', 'Sparse') : brief.density === 'dense' ? t('密', 'Dense') : t('中', 'Medium')}` : ''}</small>
    <div ref={bodyRef} className="print-dxf-body">
      {svg ? <div className="print-dxf-svg" dangerouslySetInnerHTML={{ __html: svg }} /> : <svg viewBox="0 0 100 120" aria-hidden="true"><path d="M22 14 L50 8 L78 14 L90 112 H10 Z" fill="#f7f1ea" stroke="#b9a89a" /></svg>}
      <div className={`print-zone${brief.type === 'allover' ? ' allover' : ''}`} style={{ left: `${zone.x * 100}%`, top: `${zone.y * 100}%`, width: `${zone.w * 100}%`, height: `${zone.h * 100}%` }} onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
        <i className="print-zone-handle" aria-hidden="true" />
      </div>
    </div>
  </div>;
}

export function PrintDesignPanel(props: any) {
  const { t, language } = useLanguage();
  const { basePreview, currentPreview, onPreviewChange, onAdopt, onPlace, overlay, onBriefChange, placementZone, variant = 'print', libraryPick } = props;
  const batik = variant === 'tie-dye';
  const [aiPrompt, setAiPrompt] = React.useState('');
  const [aiStatus, setAiStatus] = React.useState('');
  const [aiError, setAiError] = React.useState('');
  const [aiHistory, setAiHistory] = React.useState<PrintConversationTurn[]>([]);
  const [brief, setBrief] = React.useState<PrintBrief>({});
  const [uploadImage, setUploadImage] = React.useState<{ name: string; src: string } | null>(null);
  const [adopted, setAdopted] = React.useState(libraryPick?.id || '');
  const briefRef = React.useRef(brief);
  briefRef.current = brief;
  React.useEffect(() => { onBriefChange?.(brief); }, [brief]);
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const src = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
    setUploadImage({ name: file.name, src });
    event.target.value = '';
  };
  const resultSrc = (base: string, path: string) => `${base}${path.startsWith('/') ? path : `/${path}`}`;
  const pollJob = async (base: string, route: string, jobId: string, label: string) => {
    let completed: any = { job_id: jobId, status: 'queued', progress: 0 };
    while (!['succeeded', 'failed', 'cancelled'].includes(completed.status)) {
      await new Promise((resolve) => window.setTimeout(resolve, 1600));
      const statusResponse = await fetch(`${base}${route}/${jobId}`);
      if (!statusResponse.ok) throw new Error(t('无法读取生成进度', 'Unable to read generation progress'));
      completed = await statusResponse.json();
      setAiStatus(`${label}${completed.progress ? ` · ${completed.progress}%` : ''}`);
    }
    return completed;
  };
  const generateMotifs = async (nextBrief: PrintBrief, extra = '') => {
    const base = aiBase();
    const prompt = briefPrompt(nextBrief, extra, styleNotes(aiHistory));
    const mode = nextBrief.type === 'allover' || batik ? 'seamless' : 'motif';
    setAiError(''); setAiStatus(t('正在生成4张印花图', 'Generating 4 print artworks'));
    try {
      const input = { prompt, history: aiHistory.filter((turn) => turn.role === 'user').map((turn) => turn.text).slice(-6), process: batik ? 'tie-dye' : 'print', mode, width: 512, height: 512, candidate_count: 4, inspiration_image_data_url: uploadImage?.src || '' };
      const response = await fetch(`${base}/print/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('印花图生成暂时不可用，请稍后重试。', 'Print artwork generation is temporarily unavailable.'));
      const created = await response.json();
      const completed = await pollJob(base, '/print/jobs', created.job_id, t('正在生成印花图', 'Generating print artworks'));
      if (completed.status !== 'succeeded' || !completed.result_urls?.length) throw new Error(t('印花图生成失败，请调整描述后重试。', 'Print artwork generation failed. Adjust the request and try again.'));
      const drafts = completed.result_urls.slice(0, 4).map((path: string, index: number) => ({ id: `${created.job_id}-${index}`, src: resultSrc(base, path), productionAsset: { url: resultSrc(base, path), mode, format: 'PNG' as const, width_px: 512, height_px: 512, dpi: 150, color_space: 'sRGB', transparent: mode === 'motif' }, input: { ...input, selected_print_url: path } }));
      setUploadImage(null);
      const placeHint = nextBrief.type === 'allover' || batik
        ? t('点一张先在布料上排列，排好再出效果图。', 'Pick one, arrange it on the fabric, then generate the look.')
        : t('点一张放到衣服上，拖动缩放后保存即可。', 'Pick one, place it on the garment, then save.');
      setAiHistory((current) => [...current, { id: `assistant-${created.job_id}`, role: 'assistant', text: `${t('按这个风格出图', 'Generating with this style')}：${prompt}。${placeHint}`, drafts }]);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('图案生成失败', 'Pattern generation failed')); }
    finally { setAiStatus(''); }
  };
  const applyMotif = (draft: GeneratedDraft) => {
    if (!basePreview?.url) { setAiError(t('请先在编辑搭配中完成2D设计预览。', 'Complete the 2D design preview first.')); return; }
    const type = batik || briefRef.current.type === 'allover' ? 'allover' : (briefRef.current.type || 'small');
    setAdopted(draft.id);
    onAdopt({ preview_url: draft.src, production_asset: draft.productionAsset, input: draft.input });
    onPlace?.(defaultOverlay(type, draft.src, briefRef.current.density));
    setAiError('');
    setAiHistory((current) => [...current, { id: `assistant-place-${draft.id}`, role: 'assistant', text: type === 'allover' ? t('已放到布料上。调好疏密后点「出效果图」。', 'Placed on the fabric. Adjust the repeat, then generate the look.') : t('已放到衣服上。拖动、缩放，点「保存这一版」即可。', 'Placed on the garment. Drag and scale, then save this version.') }]);
  };
  const generateLook = async (printSrc?: string, arranged = true) => {
    const src = printSrc || (overlay?.type === 'allover' ? overlay.src : '');
    if (!basePreview?.url || !src) {
      if (printSrc) setAiError(t('请先在编辑搭配中完成2D设计预览。', 'Complete the 2D design preview first.'));
      return;
    }
    const base = aiBase();
    setAiError(''); setAiStatus(t('正在把布料印到衣服上', 'Printing the fabric onto the garment'));
    try {
      const live = { ...briefRef.current, zone: briefRef.current.zone || placementZone };
      const garmentUrl = await toJpegDataUrl(basePreview.url, 768).catch(() => basePreview.url);
      const fabricSrc = arranged ? await cropFabricFace(src) : await toJpegDataUrl(src, 512);
      const prompt = (arranged
        ? `${briefPrompt(live, '', styleNotes(aiHistory))}。第一张是已通过的带人脸试穿图：必须保留同一张脸、同一人、同一姿势和机位，禁止换人、禁止重画脸、禁止生成没有人脸的新图。第二张是已排好的布料，只把这块布的花纹铺满衣服所有可见面料，包括袖子。`
        : `${briefPrompt(live, '', styleNotes(aiHistory))}。第一张是已通过的带人脸试穿图：必须保留同一张脸、同一人、同一姿势和机位，禁止换人、禁止重画脸、禁止生成没有人脸的新图。第二张是选中的印花布料，只把这块布的花纹铺满衣服所有可见面料，包括袖子和侧片。`).slice(0, 1200);
      const input = { prompt, history: aiHistory.filter((turn) => turn.role === 'user').map((turn) => turn.text).slice(-4), process: batik ? 'tie-dye' : 'print', source_preview_url: garmentUrl, selected_print_url: fabricSrc, selected_print_mode: 'seamless', inspiration_image_data_url: fabricSrc, design_context: { allover: true, cover: 'entire_garment', keep_identity: true, ...(arranged ? { arranged_fabric: true, tile: overlay?.tile, gap: overlay?.gap } : { fabric_reference: true }) } };
      const response = await fetch(`${base}/garment-print/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('穿着效果生成暂时不可用，请稍后重试。', 'Garment look generation is temporarily unavailable.'));
      const created = await response.json();
      const completed = await pollJob(base, '/garment-print/jobs', created.job_id, t('正在生成效果图', 'Generating the look'));
      if (completed.status !== 'succeeded' || !completed.preview_url) throw new Error(String(completed.error || '').slice(0, 180) || t('效果图生成失败，请换一块布再试。', 'The garment look failed. Try another fabric.'));
      onPreviewChange(resultSrc(base, completed.preview_url));
      setAiHistory((current) => [...current, { id: `assistant-look-${created.job_id}`, role: 'assistant', text: t('效果图已出。不满意可以换一块布，或继续改描述。', 'The look is ready. Pick another fabric or keep editing.') }]);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('效果图生成失败', 'Look generation failed')); }
    finally { setAiStatus(''); }
  };
  const talk = async (raw: string, asChip = false) => {
    const text = raw.trim();
    if (!text || aiStatus) return;
    if (batik) {
      setAiHistory((current) => [...current, { id: `user-${Date.now()}`, role: 'user', text }]);
      setAiPrompt('');
      await generateMotifs({ motif: text }, text);
      return;
    }
    const chipMap: Record<string, Partial<PrintBrief>> = { small: { type: 'small' }, chest: { type: 'chest' }, allover: { type: 'allover' }, left: { placement: 'left' }, center: { placement: 'center' }, right: { placement: 'right' }, sparse: { density: 'sparse' }, medium: { density: 'medium' }, dense: { density: 'dense' } };
    const seed = { ...briefRef.current, ...(asChip && chipMap[text] ? chipMap[text] : {}) };
    const label = CHIP_LABELS.find((item) => item.value === text);
    const shown = label ? (language === 'zh' ? label.label_zh : label.label_en) : text;
    setAiHistory((current) => [...current, { id: `user-${Date.now()}`, role: 'user', text: shown }]);
    setAiPrompt('');
    setAiError('');
    if (text === 'generate' || /生成印花图|生成效果图|出图|看看效果/.test(text)) {
      if (libraryPick && !batik) { await generateLook(libraryPick.swatch, false); return; }
      await generateMotifs(seed, shown);
      return;
    }
    setAiStatus(t('印花创作师正在想', 'The print designer is thinking'));
    try {
      const response = await fetch(`${textBase()}/print/conversation`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ language, brief: seed, messages: [...aiHistory.map((turn) => ({ role: turn.role, content: turn.text })), { role: 'user', content: shown }], image_data_urls: uploadImage?.src ? [uploadImage.src] : [] }) });
      if (!response.ok) throw new Error(t('印花创作师暂时不可用。', 'The print designer is temporarily unavailable.'));
      const data = await response.json();
      const nextBrief = { ...seed, ...(data.brief || {}) };
      setBrief(nextBrief);
      setAiHistory((current) => [...current, { id: `assistant-${Date.now()}`, role: 'assistant', text: data.assistant_message || t('继续说说你想要的印花。', 'Tell me more about the print.'), options: data.options || [] }]);
      if (data.brief?.ready_to_generate && /生成印花|生成效果|出图/.test(text)) await generateMotifs(nextBrief, shown);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('对话失败', 'Conversation failed')); }
    finally { setAiStatus(''); }
  };
  const pickFabric = (fabric: TaobaoPrintFabric) => {
    const next = { ...briefRef.current, type: 'allover' as const, motif: fabric.name, style_prompt: fabric.style, density: briefRef.current.density || 'medium' };
    setBrief(next);
    setAdopted(fabric.id);
    onAdopt({ preview_url: fabric.swatch, production_asset: { url: fabric.swatch, mode: 'seamless', format: 'PNG', width_px: 512, height_px: 512, dpi: 150, color_space: 'sRGB', transparent: false }, input: { source: 'taobao', ...fabric } });
    onPlace?.(null);
    setAiHistory((current) => [...current, { id: `user-fabric-${fabric.id}`, role: 'user', text: fabric.name }, { id: `assistant-fabric-${fabric.id}`, role: 'assistant', text: t(`已选「${fabric.name}」。点生成后会把它作为布料参考图印到衣服上。`, `Selected "${fabric.name}". Click Generate to print it onto the garment.`) }]);
  };
  React.useEffect(() => {
    if (!libraryPick || batik || adopted === libraryPick.id) return;
    pickFabric(libraryPick);
  }, [libraryPick]);
  const assistantName = batik ? t('扎染创作助手', 'Tie-dye assistant') : t('印花创作师', 'Print designer');
  const opening = batik
    ? t('描述你想要的扎染或蜡染纹理，也可以上传参考图。我会保留当前服装与人物，在面料上生成蜡染纹理参考。', 'Describe the tie-dye or batik texture you want, or upload a reference. I will keep the garment and wearer, and generate a batik texture on the cloth.')
    : t('我是印花创作师。先选一种印花方式，或直接描述你想要的图案，我会一步步帮你确定细节和位置。', 'I am a print designer. Pick a print type, or describe the artwork — I will guide the details and placement.');
  return <div className="panel-content print-panel-redesign">
      <div className="design-conversation" aria-label={batik ? t('扎染创作记录', 'Tie-dye creation history') : t('印花创作记录', 'Print creation history')} aria-live="polite">
        {!aiHistory.length && <div className="design-message assistant"><small>{assistantName}</small><span>{opening}</span>{!batik && <div className="design-quick-replies">{TYPE_CHIPS.map((item) => <button key={item.value} type="button" disabled={Boolean(aiStatus)} onClick={() => void talk(item.value, true)}>{language === 'zh' ? item.label_zh : item.label_en}</button>)}</div>}</div>}
        {aiHistory.map((turn) => <React.Fragment key={turn.id}><div className={`design-message ${turn.role}`}>{turn.role === 'assistant' && <small>{assistantName}</small>}<span>{turn.text}</span>{turn.role === 'assistant' && turn.options && turn.id === aiHistory.at(-1)?.id && <div className="design-quick-replies">{turn.options.map((item) => <button key={item.value} type="button" disabled={Boolean(aiStatus)} onClick={() => void talk(item.value, true)}>{language === 'zh' ? item.label_zh : item.label_en}</button>)}</div>}</div>{turn.drafts && <div className="print-draft-grid">{turn.drafts.map((draft) => <article key={draft.id}><img src={draft.src} alt={t('印花图', 'Print artwork')} /><button type="button" className={adopted === draft.id ? 'adopted' : ''} disabled={Boolean(aiStatus)} onClick={() => void applyMotif(draft)}>{t('选这个', 'Select this')}</button></article>)}</div>}</React.Fragment>)}
        {aiStatus && <div className="design-message assistant thinking"><small>{assistantName}</small><span className="design-thinking" aria-label={aiStatus}><i /><i /><i /></span><em>{aiStatus}</em></div>}
      </div>
      {uploadImage && <div className="print-upload-chip"><img src={uploadImage.src} alt="" /><span>{uploadImage.name}</span><button type="button" onClick={() => setUploadImage(null)}>×</button></div>}
      <div className="chat-input"><input value={aiPrompt} onChange={(event) => setAiPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void talk(aiPrompt); } }} placeholder={aiHistory.length ? (batik ? t('继续描述蜡染纹理修改…', 'Continue describing batik revisions…') : t('继续描述印花…', 'Continue describing the print…')) : (batik ? t('告诉我你想要的扎染或蜡染纹理…', 'Tell me the tie-dye or batik texture you want…') : t('告诉我你想要的印花…', 'Tell me what print you want…'))} /><label className="print-chat-upload" title={t('上传参考图', 'Add image')}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} /><span aria-hidden="true">＋</span></label><button type="button" disabled={!aiPrompt.trim() || Boolean(aiStatus)} onClick={() => void talk(aiPrompt)}>{t('发送', 'Send')}</button></div>
      {libraryPick && <div className="print-upload-chip"><img src={libraryPick.swatch} alt="" /><span>{libraryPick.name}</span><button type="button" onClick={() => { setAdopted(''); onAdopt?.(null); props.onLibraryPick?.(null); }}>×</button></div>}
      {aiError && <p className="form-error">{aiError}</p>}
    {(libraryPick || overlay?.type === 'allover') && <button className="primary full" disabled={Boolean(aiStatus)} onClick={() => void (libraryPick ? generateLook(libraryPick.swatch, false) : generateLook())}>{t('生成', 'Generate')}</button>}
  </div>;
}

function MotifLayer({ overlay, onChange }: { overlay: PrintOverlay; onChange: (next: PrintOverlay) => void }) {
  const drag = React.useRef<{ kind: 'move' | 'resize'; x: number; y: number; ox: number; oy: number; ow: number } | null>(null);
  const boxRef = React.useRef<HTMLDivElement>(null);
  const start = (kind: 'move' | 'resize', event: React.PointerEvent) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { kind, x: event.clientX, y: event.clientY, ox: overlay.x, oy: overlay.y, ow: overlay.w };
  };
  const move = (event: React.PointerEvent) => {
    if (!drag.current || !boxRef.current) return;
    const box = boxRef.current.getBoundingClientRect();
    const dx = (event.clientX - drag.current.x) / box.width;
    const dy = (event.clientY - drag.current.y) / box.height;
    if (drag.current.kind === 'move') onChange({ ...overlay, x: clamp(drag.current.ox + dx, 0, 1 - overlay.w), y: clamp(drag.current.oy + dy, 0, 1 - overlay.h) });
    else {
      const size = clamp(drag.current.ow + dx, 0.06, 0.8);
      onChange({ ...overlay, w: size, h: size, x: clamp(overlay.x, 0, 1 - size), y: clamp(overlay.y, 0, 1 - size) });
    }
  };
  return <div ref={boxRef} className="print-motif-layer" onPointerMove={move} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
    <div className="print-motif" style={{ left: `${overlay.x * 100}%`, top: `${overlay.y * 100}%`, width: `${overlay.w * 100}%`, height: `${overlay.h * 100}%` }} onPointerDown={(event) => start('move', event)} onWheel={(event) => { event.preventDefault(); const size = clamp(overlay.w * (event.deltaY < 0 ? 1.08 : 0.92), 0.06, 0.8); const cx = overlay.x + overlay.w / 2; const cy = overlay.y + overlay.h / 2; onChange({ ...overlay, w: size, h: size, x: clamp(cx - size / 2, 0, 1 - size), y: clamp(cy - size / 2, 0, 1 - size) }); }}>
      <img src={overlay.src} alt="" draggable={false} />
      <i className="print-motif-resize" onPointerDown={(event) => start('resize', event)} />
    </div>
  </div>;
}

function FabricBoard({ overlay, onChange }: { overlay: PrintOverlay; onChange: (next: PrintOverlay) => void }) {
  const { t } = useLanguage();
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const image = new Image();
    image.onload = () => {
      ctx.fillStyle = '#f4efe8';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const tile = Math.max(24, overlay.tile * canvas.width);
      const step = tile + overlay.gap * canvas.width;
      for (let y = 0; y < canvas.height; y += step) for (let x = 0; x < canvas.width; x += step) ctx.drawImage(image, x, y, tile, tile);
    };
    image.src = overlay.src;
  }, [overlay.src, overlay.tile, overlay.gap]);
  return <div className="print-fabric-board">
    <small>{t('布料排列', 'Fabric layout')} · {t('先排好再出效果图', 'Arrange first, then generate')}</small>
    <canvas ref={canvasRef} width={512} height={512} />
    <label>{t('大小', 'Size')}<input type="range" min="0.1" max="0.4" step="0.01" value={overlay.tile} onChange={(event) => onChange({ ...overlay, tile: Number(event.target.value) })} /></label>
    <label>{t('间距', 'Gap')}<input type="range" min="0" max="0.16" step="0.01" value={overlay.gap} onChange={(event) => onChange({ ...overlay, gap: Number(event.target.value) })} /></label>
  </div>;
}

export function PrintDesignPreview({ src, versions = [], activeId = '', compositionSvg = '', printBrief = {}, overlay, onOverlayChange, onCommit, onZoneChange, onSelectVersion, showLibrary = false, adoptedId = '', onPickFabric }: {
  src: string;
  versions?: Array<{ id: string; label: string; designUrl: string; tip?: string }>;
  activeId?: string;
  compositionSvg?: string;
  printBrief?: PrintBrief;
  overlay?: PrintOverlay | null;
  onOverlayChange?: (next: PrintOverlay) => void;
  onCommit?: (url: string) => void;
  onZoneChange?: (zone: PrintZone) => void;
  onSelectVersion?: (version: { id: string; label: string; designUrl: string; tip?: string }) => void;
  showLibrary?: boolean;
  adoptedId?: string;
  onPickFabric?: (fabric: TaobaoPrintFabric) => void;
}) {
  const { t } = useLanguage();
  const [view, setView] = React.useState<'preview' | 'library'>('preview');
  const [picked, setPicked] = React.useState(adoptedId || '');
  const [fullscreen, setFullscreen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [libraryReady, setLibraryReady] = React.useState(false);
  const libraryLoaded = React.useRef(new Set<string>());
  const imgRef = React.useRef<HTMLImageElement>(null);
  const [box, setBox] = React.useState({ left: 0, top: 0, w: 0, h: 0 });
  const preview = src || MOCK_GARMENT;
  const rail = versions.length ? versions : (preview ? [{ id: 'live', label: 'V1', designUrl: preview, tip: t('当前预览', 'Current preview') }] : []);
  const motif = overlay && overlay.type !== 'allover';
  const library = showLibrary && view === 'library';
  const noteLibrary = (id: string) => {
    if (libraryLoaded.current.size >= 8) return;
    libraryLoaded.current.add(id);
    if (libraryLoaded.current.size >= 8) setLibraryReady(true);
  };
  React.useEffect(() => {
    if (!library || libraryReady) return;
    const timer = window.setTimeout(() => setLibraryReady(true), 2500);
    return () => window.clearTimeout(timer);
  }, [library, libraryReady]);
  React.useEffect(() => {
    const img = imgRef.current;
    const stage = img?.parentElement;
    if (!img || !stage) return;
    const update = () => {
      if (!img.naturalWidth) return;
      const stageBox = stage.getBoundingClientRect();
      const imgBox = img.getBoundingClientRect();
      const scale = Math.min(imgBox.width / img.naturalWidth, imgBox.height / img.naturalHeight);
      const w = img.naturalWidth * scale;
      const h = img.naturalHeight * scale;
      setBox({ left: imgBox.left - stageBox.left + (imgBox.width - w) / 2, top: imgBox.top - stageBox.top + (imgBox.height - h) / 2, w, h });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(img);
    observer.observe(stage);
    img.addEventListener('load', update);
    return () => { observer.disconnect(); img.removeEventListener('load', update); };
  }, [preview, overlay, fullscreen, library]);
  const save = async () => {
    if (!overlay || !onCommit) return;
    setSaving(true);
    try { onCommit(await flattenMotif(preview, overlay)); }
    finally { setSaving(false); }
  };
  return <div className={`pattern-workbench${library ? ' print-library-mode' : ''}`}>
    <div className={`pattern-stage design-mode print-canvas${library ? ' print-library' : ''}`}>
      {showLibrary && <div className="pattern-view-switch">
        <button type="button" className={view === 'library' ? 'active' : ''} onClick={() => setView('library')}>{t('印花库', 'Print library')}</button>
        <button type="button" className={view === 'preview' ? 'active' : ''} onClick={() => setView('preview')}>{t('2D预览图', '2D preview')}</button>
      </div>}
      {library && !libraryReady && <div className="design-ai-loading print-library-wait" role="status">
        <div className="design-ai-mosaic" aria-hidden="true">{Array.from({ length: 24 }, (_, index) => <i key={index} style={{ background: MOSAIC[index % MOSAIC.length], ['--i' as string]: index }} />)}</div>
        <strong>{t('正在载入印花库', 'Loading print library')}</strong>
      </div>}
      {library ? <div className={`print-library-scroll${libraryReady ? '' : ' is-warming'}`}>
        <div className="reference-grid all-references print-fabric-library" aria-label={t('全身印花布', 'Allover print fabrics')}>
        {taobaoPrintFabrics.map((fabric, index) => {
          const selected = (picked || adoptedId) === fabric.id;
          return <article key={fabric.id} className={selected ? 'reference-card selected' : 'reference-card'} onClick={() => setPicked(fabric.id)}>
            <img className="reference-image" src={fabric.swatch} alt={fabric.name} loading={index < 8 ? 'eager' : 'lazy'} decoding="async" onLoad={() => noteLibrary(fabric.id)} onError={() => noteLibrary(fabric.id)} />
            <a className="print-share-icon" href={fabric.taobao} target="_blank" rel="noreferrer" title={t('分享到淘宝', 'Share on Taobao')} aria-label={t('分享到淘宝', 'Share on Taobao')} onClick={(event) => event.stopPropagation()}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.6 13.5 15.4 17.2M15.4 6.8 8.6 10.5" /></svg>
            </a>
            <strong className="print-fabric-name">{fabric.name}</strong>
            {selected && <button type="button" className="primary reference-confirm" onClick={(event) => { event.stopPropagation(); onPickFabric?.(fabric); }}>{t('确认印花', 'Use this print')}</button>}
          </article>;
        })}
        </div>
      </div> : <>
        <div className={`design-preview-stage${fullscreen ? ' fullscreen' : ''}`}>
          <img ref={imgRef} src={preview} alt={t('印花穿着效果预览', 'Garment print preview')} className="ready" />
          {motif && box.w > 0 && <div className="print-motif-frame" style={{ left: box.left, top: box.top, width: box.w, height: box.h }}><MotifLayer overlay={overlay} onChange={(next) => onOverlayChange?.(next)} /></div>}
          {overlay?.type === 'allover' && <FabricBoard overlay={overlay} onChange={(next) => onOverlayChange?.(next)} />}
          {!overlay && printBrief.type !== 'allover' && <PrintPlacementMap svg={compositionSvg} brief={printBrief} onZoneChange={onZoneChange} />}
          <div className="design-ai-actions">
            {motif && <button type="button" className="primary" disabled={saving} onClick={() => void save()}>{saving ? t('保存中…', 'Saving…') : t('保存这一版', 'Save this version')}</button>}
            <button type="button" onClick={() => setFullscreen((value) => !value)}>{fullscreen ? t('退出全屏', 'Exit fullscreen') : t('全屏查看', 'Fullscreen')}</button>
          </div>
        </div>
        {rail.length > 0 && <div className="design-version-rail" aria-label={t('搭配版本', 'Style versions')}>
          {rail.map((version) => (
            <button key={version.id} type="button" className={(activeId ? version.id === activeId : version.designUrl === preview) ? 'active' : ''} data-tip={version.tip || version.label} title={version.tip || version.label} onClick={() => onSelectVersion?.(version)}>
              <img src={version.designUrl} alt="" />
              <em>{version.label}</em>
            </button>
          ))}
        </div>}
      </>}
    </div>
  </div>;
}
