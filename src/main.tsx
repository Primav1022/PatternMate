import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import './overrides.css';
import { AdoptedPrintConcept, FaceSettings, PrintAsset, PrintDesignPanel, PrintDesignPreview, PrintFaceMode, PrintPlacement } from './PrintDesign';
import { CompositionRecipe, PatternPreview } from './PatternPreview';
import { ComposeSandbox } from './ComposeSandbox';
import { ShirtSandbox } from './ShirtSandbox';
import { SleeveVlmSandbox } from './SleeveVlmSandbox';
import { RelabelQueue } from './RelabelQueue';
import { LanguageProvider, useLanguage } from './Language';
import { asset } from './asset';
import { geometryBase, textBase } from './apiBase';
import {
  defaultSelections,
  fabricGroupInfo,
  fabricOptions,
  GarmentFamily,
  groupLabels,
  groupOrder,
  composeSelections,
  executionModeFor,
  corpusOptionIds,
  optionsForFamily,
  optionsForGroup,
  PatternOption,
  processOptions,
} from './catalogs';

type Step = 'measure' | 'design' | 'styling' | 'print';
type Sex = 'female' | 'male_general';
type Measurements = Record<'height' | 'weight' | 'chest' | 'waist' | 'shoulder' | 'neck' | 'sleeveLength' | 'upperArm', string>;
type DesignIntent = { family?: GarmentFamily | null; category?: string | null; sleeve?: 'sleeveless' | 'short' | 'long' | null; target_length_cm?: number | null; fit?: string | null; neckline?: string | null; activity?: 'low' | 'medium' | 'high' | null; styles?: string[]; labels?: string[]; source_text?: string };
type SemanticFacet = { key: string; label: string; values: { value: string; score: number }[] };
type GeneratedUICard = { id: string; field: string; type: 'single_select' | 'multi_select' | 'number_input' | 'text_input'; required: boolean; title_zh: string; title_en: string; options: { value: string; label_zh: string; label_en: string }[]; allow_custom_text: boolean };
type PrintSnapshot = {
  assets: PrintAsset[];
  selectedAssetIds: Record<'front' | 'back', string>;
  modes: Record<'front' | 'back', PrintFaceMode>;
  settings: Record<'front' | 'back', FaceSettings>;
  placements: PrintPlacement[];
};
type ReferenceItem = {
  id: string;
  label: string;
  family: GarmentFamily;
  category: string;
  supported: boolean;
  coverUrl: string;
  semantics: Record<string, any>;
  baseOptionIds: Record<string, string>;
};

const stepIds: Step[] = ['measure', 'design', 'styling', 'print'];

const fixedTheme = { primary: '#f39a3d', soft: '#fff2e5', border: '#efc39f' } as const;

const defaultMeasurements: Measurements = { height: '160', weight: '50', chest: '85', waist: '60', shoulder: '38', neck: '32', sleeveLength: '50.5', upperArm: '25' };
const fabricColors = ['#ffffff', '#f4f1e8', '#22242a', '#aeb6c2', '#b8d3e8', '#6f8fae', '#d9c3ad', '#b86f62', '#d8a6b7', '#8ea88c', '#5f705e', '#d6b85f'];
function parseHex(hex: string): [number, number, number] {
  const raw = hex.replace('#', '');
  const n = raw.length === 3 ? raw.split('').map((c) => c + c).join('') : raw.padEnd(6, '0');
  return [parseInt(n.slice(0, 2), 16) || 0, parseInt(n.slice(2, 4), 16) || 0, parseInt(n.slice(4, 6), 16) || 0];
}
function toHex(r: number, g: number, b: number) {
  return `#${[r, g, b].map((n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')).join('')}`;
}
function toCmyk(r: number, g: number, b: number): [number, number, number, number] {
  const red = r / 255, green = g / 255, blue = b / 255;
  const k = 1 - Math.max(red, green, blue);
  if (k >= 0.999) return [0, 0, 0, 100];
  return [Math.round(((1 - red - k) / (1 - k)) * 100), Math.round(((1 - green - k) / (1 - k)) * 100), Math.round(((1 - blue - k) / (1 - k)) * 100), Math.round(k * 100)];
}
function fromCmyk(c: number, m: number, y: number, k: number) {
  return toHex(255 * (1 - c / 100) * (1 - k / 100), 255 * (1 - m / 100) * (1 - k / 100), 255 * (1 - y / 100) * (1 - k / 100));
}
function rgbToHsv(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return [h, max ? d / max : 0, max];
}
function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  const c = v * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = v - c;
  const [rp, gp, bp] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [Math.round((rp + m) * 255), Math.round((gp + m) * 255), Math.round((bp + m) * 255)];
}
function DyePicker({ value, onChange, onClose, anchor }: { value: string; onChange: (hex: string) => void; onClose: () => void; anchor: HTMLElement }) {
  const rgb = parseHex(value);
  const [h0, s, v] = rgbToHsv(...rgb);
  const [hue, setHue] = useState(h0 || 0);
  const [mode, setMode] = useState<'rgb' | 'cmyk'>('cmyk');
  const box = useRef<HTMLDivElement>(null);
  const cmyk = toCmyk(...rgb);
  const rect = anchor.getBoundingClientRect();
  useEffect(() => { if (s > 0.02) setHue(h0); }, [h0, s]);
  useEffect(() => {
    const close = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) onClose(); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [anchor, onClose]);
  const setSv = (event: React.PointerEvent<HTMLDivElement>) => {
    const area = event.currentTarget.getBoundingClientRect();
    const nextS = Math.min(1, Math.max(0, (event.clientX - area.left) / area.width));
    const nextV = Math.min(1, Math.max(0, 1 - (event.clientY - area.top) / area.height));
    onChange(toHex(...hsvToRgb(hue, nextS, nextV)));
  };
  const pickSv = (event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    setSv(event);
  };
  const drop = async () => {
    const Eye = (window as any).EyeDropper;
    if (!Eye) return;
    try { onChange((await new Eye().open()).sRGBHex); } catch { /* cancelled */ }
  };
  return <div className="dye-picker" ref={box} style={{ top: rect.bottom + 8, left: Math.max(12, Math.min(rect.left, window.innerWidth - 248)) }}>
    <div className="dye-sv" style={{ background: `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,hsl(${hue},100%,50%))` }} onPointerDown={pickSv} onPointerMove={(event) => event.buttons && setSv(event)}>
      <i style={{ left: `${s * 100}%`, top: `${(1 - v) * 100}%` }} />
    </div>
    <div className="dye-row">
      {'EyeDropper' in window && <button type="button" className="dye-drop" aria-label="eyedropper" onClick={() => void drop()}>⌖</button>}
      <i className="dye-preview" style={{ background: value }} />
      <input className="dye-hue" type="range" min={0} max={360} value={Math.round(hue)} onChange={(event) => { const next = Number(event.target.value); setHue(next); onChange(toHex(...hsvToRgb(next, s, v))); }} />
    </div>
    <div className="dye-nums">
      {(mode === 'rgb' ? rgb : cmyk).map((n, index) => <input key={`${mode}-${index}`} type="number" min={0} max={mode === 'rgb' ? 255 : 100} value={n} onChange={(event) => {
        const raw = Number(event.target.value);
        if (mode === 'rgb') { const next = [...rgb] as [number, number, number]; next[index] = raw; onChange(toHex(...next)); }
        else { const next = [...cmyk] as [number, number, number, number]; next[index] = raw; onChange(fromCmyk(...next)); }
      }} />)}
      <button type="button" className="dye-mode" onClick={() => setMode(mode === 'rgb' ? 'cmyk' : 'rgb')} aria-label="RGB / CMYK">↕</button>
    </div>
    <div className="dye-keys">{(mode === 'rgb' ? ['R', 'G', 'B'] : ['C', 'M', 'Y', 'K']).map((key) => <span key={key}>{key}</span>)}</div>
  </div>;
}
const digitalPrintSupport: Record<string, 'supported' | 'caution' | 'unsupported'> = {
  'cotton-jersey': 'supported', 'tencel-cotton': 'supported', 'heavy-cotton': 'supported', 'canvas-cotton': 'supported', 'waffle-knit': 'supported', 'slub-cotton': 'supported', 'terry-cloth': 'caution', 'rib-knit': 'supported', 'stretch-jersey': 'caution', 'performance-polyester': 'supported', 'mesh': 'supported', 'reflective-fabric': 'unsupported', 'cooling-fiber': 'supported', 'velvet-knit': 'unsupported', 'metallic': 'unsupported', 'sequin': 'unsupported', 'sheer-mesh': 'supported',
  poplin: 'supported', oxford: 'supported', 'mercerized-cotton': 'supported', linen: 'supported', chambray: 'supported', denim: 'supported', corduroy: 'unsupported', twill: 'supported', 'waxed-cotton': 'unsupported', 'silk-satin': 'unsupported', rayon: 'supported', velvet: 'unsupported', chiffon: 'supported', organza: 'supported', lace: 'supported',
};
const processSupport: Record<string, { brushed: boolean; tieDye: boolean }> = {
  'waffle-knit': { brushed: false, tieDye: true }, 'terry-cloth': { brushed: false, tieDye: true }, 'rib-knit': { brushed: false, tieDye: true }, 'stretch-jersey': { brushed: false, tieDye: false }, 'performance-polyester': { brushed: false, tieDye: false }, mesh: { brushed: false, tieDye: false }, 'reflective-fabric': { brushed: false, tieDye: false }, 'cooling-fiber': { brushed: false, tieDye: false }, 'velvet-knit': { brushed: false, tieDye: false }, metallic: { brushed: false, tieDye: false }, sequin: { brushed: false, tieDye: false }, 'sheer-mesh': { brushed: false, tieDye: false },
  'mercerized-cotton': { brushed: false, tieDye: true }, corduroy: { brushed: false, tieDye: true }, 'waxed-cotton': { brushed: false, tieDye: false }, 'silk-satin': { brushed: false, tieDye: false }, rayon: { brushed: false, tieDye: true }, velvet: { brushed: false, tieDye: false }, chiffon: { brushed: false, tieDye: false }, organza: { brushed: false, tieDye: false }, lace: { brushed: false, tieDye: false },
};

function loadRememberedMeasurements(): Measurements {
  try {
    const saved = localStorage.getItem('smart-pattern-measurements');
    return saved ? { ...defaultMeasurements, ...JSON.parse(saved) } : defaultMeasurements;
  } catch { return defaultMeasurements; }
}

const semanticValueLabels: Record<string, string> = {
  basic: '基础', casual: '休闲', unisex: '中性', streetwear: '街头', vintage_washed: '复古水洗', artistic: '艺术', athleisure: '运动休闲', commuter: '通勤', preppy: '学院', elegant: '优雅', sweet: '甜美', avant_garde: '先锋', niche: '小众', retro: '复古', minimal: '简约', conservative: '保守', quiet_luxury: '静奢', romantic: '浪漫', sporty: '运动', hot_girl: '辣妹', business: '商务', outdoor: '户外', workwear: '工装', oriental: '东方', punk: '朋克', y2k: 'Y2K',
  oversized: '超宽松', relaxed: '宽松', fitted: '修身', regular: '合体', tight: '紧身', workplace: '职场', fashion: '时装', sports: '运动', high: '高', medium: '中', low: '低', upper_body: '上身', neckline: '领口', chest: '胸部', shoulder: '肩部', waist: '腰部', none: '无', female: '女性', male: '男性', healthy: '匀称', full: '丰满', slender: '纤细', oversize: 'Oversize',
  tshirt: 'T恤', shirt: '衬衫', sleeveless: '无袖', short: '短袖', long: '长袖', 'v-neck': 'V领', crew: '圆领', polo: 'Polo领',
};
const semanticLabel = (value: string) => semanticValueLabels[value] || value;
const englishSlug = (value: string) => value.split('-').map((part) => part ? part[0].toUpperCase() + part.slice(1) : part).join(' ');

const MOCK_REFERENCE_IDS = [
  'C2390077', 'C2390270', 'C2390279', 'C2390303', 'C2390726',
  'C2430065', 'C2430079', 'C2430144', 'C2430196', 'C2430367',
  'C2431027', 'C2431055', 'C2490092', 'C2490188', 'C2490194',
  'C2490252', 'C2490257', 'C2490260', 'C2490278', 'C2490320',
  'C2490335', 'C2490383', 'C2490411', 'C2490437',
] as const;

const fallbackReferences: ReferenceItem[] = MOCK_REFERENCE_IDS.map((id, index) => {
  const shirt = index % 4 === 0 || id.startsWith('C243');
  return {
    id,
    label: id,
    family: shirt ? 'shirt' : 'tshirt',
    category: shirt ? 'shirt' : 'tshirt',
    supported: true,
    coverUrl: asset(`/reference-images/v1/${id}/cover.jpg`),
    semantics: {
      fit: shirt ? 'regular' : index % 2 ? 'relaxed' : 'oversized',
      style_tags: shirt ? ['通勤', '衬衫', '基础'] : ['休闲', '基础', '日常'],
    },
    baseOptionIds: shirt
      ? { silhouette: 'shirt.silhouette.regular-fit', placket: 'shirt.placket.full', cuff: 'shirt.cuff.regular', sleeve: 'shirt.sleeve.regular' }
      : { neckline: 'tshirt.neckline.crew', sleeve: 'tshirt.sleeve.set-in' },
  };
});

const USE_MOCK_CATALOG = false;

function createUntitledProjectName(): string {
  const current = sessionStorage.getItem('smart-pattern-current-project-name');
  if (current) return current;
  const now = new Date();
  const date = `${String(now.getFullYear()).slice(-2)}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
  const counterKey = `smart-pattern-project-counter-${date}`;
  const next = Math.max(0, Number(localStorage.getItem(counterKey) || 0)) + 1;
  localStorage.setItem(counterKey, String(next));
  const name = `未命名设计_${date}_${String(next).padStart(2, '0')}`;
  sessionStorage.setItem('smart-pattern-current-project-name', name);
  return name;
}

function isUntitledProjectName(name: string) {
  return /^未命名设计_\d{6}_\d+$/.test(name) || /^Untitled Design_\d{6}_\d+$/.test(name);
}

function HomePage({ language, setLanguage, onStart }: { language: 'zh' | 'en'; setLanguage: (value: 'zh' | 'en') => void; onStart: () => void }) {
  const { t } = useLanguage();
  const [launching, setLaunching] = useState(false);
  const gallery = [1, 4, 3, 7, 6, 9, 5, 8].map((index) => asset(`/home-gallery/garment-${String(index).padStart(2, '0')}.png`));
  const scrollingGallery = [...gallery, ...gallery];
  const mosaic = ['#C4C17C', '#F3C13E', '#9DC9E8', '#F2821C', '#F497A2', '#9A95ED', '#60332D', '#C4C17C', '#F3C13E', '#9DC9E8', '#F2821C', '#F497A2'];
  const start = () => {
    if (launching) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { onStart(); return; }
    setLaunching(true);
    window.setTimeout(onStart, 560);
  };
  return <div className="home-page"><main className="home-main"><section className="home-copy"><div className="home-brand"><img src={asset('/brand/logo.png')} alt="" className="home-logo" /><img src={asset('/brand/patternmate-wordmark.svg')} alt="PatternMate" className="home-wordmark" /></div><p><strong>{t('让服装创作更简单', 'Make garment design simpler')}</strong>{t('输入尺寸与需求，选择品类与参考样式，\nAI为您生成风格预览与可生产纸样，从灵感到成衣，高效实现每一个创意。', 'Enter measurements and ideas, choose a category and reference style.\nAI turns inspiration into production-ready patterns.')}</p><button className={launching ? 'home-start launching' : 'home-start'} onClick={start}><b className="home-start-label">{t('开始探索', 'Start exploring')}</b><span>→</span><i className="home-start-mosaic" aria-hidden="true">{mosaic.map((color, index) => <i key={index} style={{ background: color, ['--i' as string]: index }} />)}</i></button></section><section className="home-gallery" aria-label={t('灵感图集', 'Inspiration gallery')}><div className="home-gallery-track">{scrollingGallery.map((src, index) => <figure key={`${src}-${index}`} className={`home-garment home-garment-${index % gallery.length + 1}`}><img src={src} alt="" /></figure>)}</div></section><label className="home-language" aria-label={t('语言', 'Language')}><select value={language} onChange={(event) => setLanguage(event.target.value as 'zh' | 'en')}><option value="zh">中文</option><option value="en">English</option></select></label></main></div>;
}

function App() {
  const { language, setLanguage, t } = useLanguage();
  const steps = stepIds.map((id) => ({ id, label: ({ measure: t('身体个性化', 'Body Personalization'), design: t('服装偏好', 'Preferences'), styling: t('编辑搭配', 'Pattern Mix'), print: t('印花创作', 'Print Design') } as Record<Step, string>)[id] }));
  const [step, setStep] = useState<Step>('measure');
  const [showHome, setShowHome] = useState(true);
  const [projectName, setProjectName] = useState(createUntitledProjectName);
  const [projectNameEditing, setProjectNameEditing] = useState(false);
  const [projectNameDraft, setProjectNameDraft] = useState('');
  const [referenceItems, setReferenceItems] = useState<ReferenceItem[]>([]);
  const [catalogStatus, setCatalogStatus] = useState<'loading' | 'ready' | 'offline'>('loading');
  const [selectedReference, setSelectedReference] = useState('');
  const selectedReferenceInfo = referenceItems.find((item) => item.id === selectedReference) || fallbackReferences[0];
  const family = selectedReferenceInfo.family;
  const [message, setMessage] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [intentMessages, setIntentMessages] = useState<string[]>([]);
  const [assistantMessages, setAssistantMessages] = useState<string[]>([]);
  const [semanticFacets, setSemanticFacets] = useState<SemanticFacet[]>([]);
  const [facetSelections, setFacetSelections] = useState<Record<string, string>>({});
  const [facetEditor, setFacetEditor] = useState<string | null>(null);
  const [designIntent, setDesignIntent] = useState<DesignIntent>({});
  const [intentUnresolved, setIntentUnresolved] = useState<string[]>([]);
  const [intentVersion, setIntentVersion] = useState(0);
  const [confirmedIntent, setConfirmedIntent] = useState<Record<string, any>>({});
  const [generatedCard, setGeneratedCard] = useState<GeneratedUICard | null>(null);
  const [analysisMode, setAnalysisMode] = useState<'model' | 'rules'>('rules');
  const [referenceScores, setReferenceScores] = useState<Record<string, number>>({});
  const [referenceOrder, setReferenceOrder] = useState<string[]>([]);
  const [lastIntentText, setLastIntentText] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [sex, setSex] = useState<Sex>('female');
  const [measurements, setMeasurements] = useState<Measurements>(loadRememberedMeasurements);
  const [rememberMeasurements, setRememberMeasurements] = useState(() => Boolean(localStorage.getItem('smart-pattern-measurements')));
  const [measurementsSaved, setMeasurementsSaved] = useState(false);
  const [referenceConfirmed, setReferenceConfirmed] = useState(false);
  const [compositionReady, setCompositionReady] = useState(false);
  const [stylingConfirmed, setStylingConfirmed] = useState(false);
  const [patternReview, setPatternReview] = useState<{ passed: boolean; notes: string[] }>({ passed: true, notes: [] });
  const [printCompatibilityWarning, setPrintCompatibilityWarning] = useState(false);
  const [compositionSummary, setCompositionSummary] = useState<any>(null);
  const [measureError, setMeasureError] = useState('');
  const [selections, setSelections] = useState<Record<string, string | null>>(defaultSelections('tshirt'));
  const [submittedSelections, setSubmittedSelections] = useState<Record<string, string | null>>(defaultSelections('tshirt'));
  const [patternUndo, setPatternUndo] = useState<Array<{ selections: Record<string, string | null>; submitted: Record<string, string | null> }>>([]);
  const [materialId, setMaterialId] = useState(fabricOptions.find((item) => item.family === 'tshirt')?.id || '');
  const [fabricColor, setFabricColor] = useState('#ffffff');
  const [processId, setProcessId] = useState(processOptions[0].id);
  const [submittedMaterialId, setSubmittedMaterialId] = useState(fabricOptions.find((item) => item.family === 'tshirt')?.id || '');
  const [submittedFabricColor, setSubmittedFabricColor] = useState('#ffffff');
  const [submittedProcessId, setSubmittedProcessId] = useState(processOptions[0].id);
  const [designPreviewRevision, setDesignPreviewRevision] = useState(0);
  const [finalDesignPreview, setFinalDesignPreview] = useState<{ url: string; input: Record<string, any>; revision: number } | null>(null);
  const [styleVersions, setStyleVersions] = useState<Array<{ id: string; label: string; revision: number; selections: Record<string, string | null>; materialId: string; fabricColor: string; processId: string; recipeHash: string; designUrl: string; designInput: Record<string, any>; pieceCount: number }>>([]);
  const [printPreviewUrl, setPrintPreviewUrl] = useState('');
  const [adoptedPrintConcept, setAdoptedPrintConcept] = useState<AdoptedPrintConcept | null>(null);
  const [exportError, setExportError] = useState('');
  const [analysisError, setAnalysisError] = useState('');
  const [exporting, setExporting] = useState(false);
  const [printModes, setPrintModes] = useState<Record<'front' | 'back', PrintFaceMode>>({ front: 'density', back: 'none' });
  const [printView, setPrintView] = useState<'front' | 'back'>('front');
  const [printAssets, setPrintAssets] = useState<PrintAsset[]>([
    { id: 'builtin-01', name: '内置图案 01', src: asset('/print-library/print-test-1/source.png') },
    { id: 'builtin-02', name: '内置图案 02', src: asset('/print-library/print-test-2/source.png') },
    { id: 'builtin-03', name: '内置图案 03', src: asset('/print-library/print-test-3/source.png') },
  ]);
  const [selectedPrintAssetIds, setSelectedPrintAssetIds] = useState<Record<'front' | 'back', string>>({ front: 'builtin-01', back: 'builtin-01' });
  const [printSettings, setPrintSettings] = useState<Record<'front' | 'back', FaceSettings>>({ front: { density: 70, size: 80, gap: 0, x: 50, y: 50, rotation: 0 }, back: { density: 70, size: 80, gap: 0, x: 50, y: 50, rotation: 0 } });
  const [printPlacements, setPrintPlacements] = useState<PrintPlacement[]>([]);
  const [activePlacementId, setActivePlacementId] = useState<string | null>(null);
  const [printUndoHistory, setPrintUndoHistory] = useState<PrintSnapshot[]>([]);
  const [printRedoHistory, setPrintRedoHistory] = useState<PrintSnapshot[]>([]);
  const [printGuidesEnabled, setPrintGuidesEnabled] = useState(true);
  const printHistoryBurst = useRef<number | null>(null);
  const sidebarWidth = step === 'measure' ? 440 : (typeof window !== 'undefined' && window.innerWidth < 960 ? 264 : 299);
  const current = useMemo(() => steps.find((item) => item.id === step)!, [step, language]);
  const selectedStructure = optionsForFamily(family).find((item) => item.id === (selections.neckline || selections.collar));
  const selectedFabric = fabricOptions.find((item) => item.id === materialId);
  const submittedFabric = fabricOptions.find((item) => item.id === submittedMaterialId);
  const selectedFabricPrintSupport = digitalPrintSupport[selectedFabric?.slug || ''] || 'supported';
  const selectedProcessSlug = processId.split('.').pop() || '';
  const selectedProcessSupport = selectedProcessSlug === 'brushed-distressed' ? (processSupport[selectedFabric?.slug || '']?.brushed === false ? 'unsupported' : 'supported') : selectedProcessSlug === 'tie-dye' ? (processSupport[selectedFabric?.slug || '']?.tieDye === false ? 'unsupported' : 'supported') : 'supported';

  useEffect(() => {
    // MOCK UI: seed local reference covers so design page is usable without /catalog
    if (USE_MOCK_CATALOG) {
      setReferenceItems(fallbackReferences);
      setCatalogStatus('ready');
      setAnalysisError('');
      return;
    }
    const base = textBase();
    let disposed = false;
    let retryTimer: number | undefined;
    let controller: AbortController | undefined;
    const loadCatalog = async () => {
      controller?.abort();
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 4000);
      try {
        const response = await fetch(`${base}/catalog`, { signal: controller.signal });
        if (!response.ok) throw new Error('catalog unavailable');
        const data = await response.json();
        if (!data?.items?.length) throw new Error('empty catalog');
        if (disposed) return;
        setReferenceItems(data.items.map((item: any) => ({
          id: item.case_id,
          label: item.case_id,
          family: item.category === 'shirt' ? 'shirt' : 'tshirt',
          category: item.original_category || item.category,
          supported: true,
          coverUrl: asset(item.cover_url),
          semantics: item.semantics || {},
          baseOptionIds: item.base_option_ids || {},
        })));
        setCatalogStatus('ready');
        setAnalysisError('');
      } catch {
        if (disposed) return;
        setReferenceItems((current) => current.length ? current : fallbackReferences);
        setCatalogStatus('ready');
        retryTimer = window.setTimeout(loadCatalog, 8000);
      } finally {
        window.clearTimeout(timeout);
      }
    };
    loadCatalog();
    return () => { disposed = true; controller?.abort(); if (retryTimer) window.clearTimeout(retryTimer); };
  }, []);

  useEffect(() => {
    setProjectName((currentName) => {
      const englishMatch = currentName.match(/^未命名设计_(\d{6})_(\d+)$/);
      const chineseMatch = currentName.match(/^Untitled Design_(\d{6})_(\d+)$/);
      if (language === 'en' && englishMatch) return `Untitled Design_${englishMatch[1]}_${englishMatch[2]}`;
      if (language === 'zh' && chineseMatch) return `未命名设计_${chineseMatch[1]}_${chineseMatch[2]}`;
      return currentName;
    });
  }, [language]);

  useEffect(() => {
    sessionStorage.setItem('smart-pattern-current-project-name', projectName);
  }, [projectName]);

  useEffect(() => { document.documentElement.style.setProperty('--fabric-color', fabricColor); }, [fabricColor]);

  const recipe: CompositionRecipe = useMemo(() => ({
    family,
    sex,
    base_case_id: selectedReference,
    measurements_cm: measurements,
    fit: facetSelections.fit || 'regular',
    ease_cm: facetSelections.fit === 'relaxed' || facetSelections.fit === 'oversized' ? 12 : 8,
    material_id: submittedMaterialId,
    material_label: submittedFabric?.label,
    material_description: submittedFabric?.description,
    process_id: submittedProcessId,
    process_label: processOptions.find((item) => item.id === submittedProcessId)?.label,
    fabric_color: submittedFabricColor,
    selections: composeSelections(family, submittedSelections),
    base_option_ids: selectedReferenceInfo.baseOptionIds,
    intent_constraints: Object.fromEntries(Object.entries({ ...designIntent, conversation: intentMessages, assistant_summary: assistantMessages.filter(Boolean).slice(-3) }).filter(([, value]) => value !== null && value !== undefined && value !== '' && (!Array.isArray(value) || value.length > 0))),
    execution_mode: executionModeFor(family),
  }), [family, sex, selectedReference, selectedReferenceInfo.baseOptionIds, measurements, facetSelections, submittedMaterialId, submittedFabric, submittedFabricColor, submittedProcessId, submittedSelections, designIntent, intentMessages, assistantMessages]);

  const activePrintPlacements = printPlacements.filter((placement) => printModes[placement.view] === 'manual');
  const usedPrintAssetIds = new Set(activePrintPlacements.map((placement) => placement.assetId));
  (['front', 'back'] as const).forEach((face) => {
    if (printModes[face] === 'density') usedPrintAssetIds.add(selectedPrintAssetIds[face]);
  });
  const designState = {
    reference: selectedReference,
    family,
    tags,
    material_id: materialId,
    fabric_color: fabricColor,
    process_id: processId,
    printSkipped: printModes.front === 'none' && printModes.back === 'none',
    pattern_review: patternReview,
    print: {
      face_modes: printModes,
      face_settings: printSettings,
      density_asset_ids: {
        front: printModes.front === 'density' ? selectedPrintAssetIds.front : null,
        back: printModes.back === 'density' ? selectedPrintAssetIds.back : null,
      },
      placements: activePrintPlacements,
      assets: printAssets.filter((asset) => usedPrintAssetIds.has(asset.id)),
    },
  };

  const updateMeasurement = (key: keyof Measurements, value: string) => {
    setMeasurements((old) => ({ ...old, [key]: value }));
    setMeasurementsSaved(false); setReferenceConfirmed(false); setStylingConfirmed(false);
  };
  const validateMeasurements = () => {
    const required: (keyof Measurements)[] = ['height', 'chest', 'waist', 'shoulder', 'neck', 'sleeveLength', 'upperArm'];
    const invalid = required.find((key) => !Number.isFinite(Number(measurements[key])) || Number(measurements[key]) <= 0);
    if (invalid) { setMeasureError('请完整填写身高、胸围、腰围、肩宽、领围、袖长和上臂围。'); return false; }
    setMeasureError(''); return true;
  };
  const saveMeasurements = () => {
    if (!validateMeasurements()) return;
    if (rememberMeasurements) localStorage.setItem('smart-pattern-measurements', JSON.stringify(measurements));
    else localStorage.removeItem('smart-pattern-measurements');
    setMeasureError('');
    setMeasurementsSaved(true);
    setStep('design');
  };
  const translateAssistant = async (text: string) => {
    if (language !== 'en' || !text) return text;
    try {
      const base = textBase();
      const response = await fetch(`${base}/translate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text, target_language: 'en' }) });
      if (response.ok) return (await response.json()).text || text;
    } catch { /* keep the original assistant reply if translation is unavailable */ }
    return text;
  };
  const analyze = async (preset?: string) => {
    const value = (typeof preset === 'string' ? preset : message).trim(); if (!value || analyzing) return;
    if (catalogStatus !== 'ready') { setAnalysisError('需求分析服务尚未连接，正在自动重试。'); return; }
    setAnalyzing(true); setAnalysisError(''); setIntentMessages((old) => [...old, value]); setMessage('');
    try {
      const started = Date.now();
      const base = textBase();
      const history: { role: string; content: string }[] = [];
      intentMessages.forEach((content, index) => {
        history.push({ role: 'user', content });
        if (assistantMessages[index]) history.push({ role: 'assistant', content: assistantMessages[index] });
      });
      history.push({ role: 'user', content: value });
      const response = await fetch(`${base}/design/conversation`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ messages: history, language, intent_version: intentVersion, current_intent: designIntent, confirmed: confirmedIntent }) });
      if (!response.ok) throw new Error('需求分析服务暂时不可用，请稍后重试。');
      const data = await response.json();
      const wait = 1100 - (Date.now() - started);
      if (wait > 0) await new Promise((resolve) => window.setTimeout(resolve, wait));
      setAssistantMessages((old) => [...old, data.assistant_message || '已记录这次补充，并更新了可选参考款。']); setDesignIntent(data.intent || {}); setIntentUnresolved(data.unresolved || []); setIntentVersion(data.intent_version || intentVersion + 1); setGeneratedCard(data.ui_cards?.[0] || null); setAnalysisMode(data.analysis_mode === 'model' ? 'model' : 'rules'); setLastIntentText((data.intent?.labels || []).join(' · '));
      if (data.confirmed) setConfirmedIntent(data.confirmed);
      if (language === 'en') {
        void translateAssistant(data.assistant_message || '已记录这次补充，并更新了可选参考款。').then((translated) => setAssistantMessages((old) => old.map((item, index) => index === old.length - 1 ? translated : item)));
      }
      setSemanticFacets(data.facets || []);
      const inferred: Record<string, string> = { ...facetSelections };
      if (data.intent?.styles?.[0]) inferred.style_tags = data.intent.styles[0];
      if (data.intent?.fit) inferred.fit = data.intent.fit;
      setFacetSelections(inferred); setTags(Object.values(inferred));
      setReferenceScores(Object.fromEntries((data.items || []).map((item: any) => [item.case_id, item.score])));
      setReferenceOrder((data.items || []).map((item: any) => item.case_id));
      setSelectedReference(''); setReferenceConfirmed(false); setCompositionReady(false); setStylingConfirmed(false);
    } catch {
      setIntentMessages((old) => old.slice(0, -1)); setMessage(value);
      setAnalysisError('需求分析服务未连接，请确认本地前后端均已启动。'); setCatalogStatus('offline');
    }
    finally { setAnalyzing(false); }
  };
  const chooseFacet = async (key: string, value: string) => {
    const next = { ...facetSelections, [key]: value };
    setFacetSelections(next); setTags(Object.values(next)); setFacetEditor(null);
    if (!intentMessages.length) return;
    try {
      const base = textBase();
      const response = await fetch(`${base}/analyze`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: intentMessages.join('；'), tags: Object.values(next) }) });
      if (!response.ok) return;
      const data = await response.json();
      setSemanticFacets(data.facets || []);
      setReferenceScores(Object.fromEntries((data.items || []).map((item: any) => [item.case_id, item.score])));
      setReferenceOrder((data.items || []).map((item: any) => item.case_id));
    } catch { /* keep the user's correction locally when rescoring is temporarily unavailable */ }
  };
  const chooseReference = (id: string) => {
    const next = referenceItems.find((item) => item.id === id);
    if (!next) return;
    setSelectedReference(id);
    setReferenceConfirmed(false); setCompositionReady(false); setStylingConfirmed(false);
    const baseSelections = { ...defaultSelections(next.family), ...next.baseOptionIds };
    setSelections(baseSelections);
    setSubmittedSelections(baseSelections);
    setPatternUndo([]);
    const nextMaterial = fabricOptions.find((item) => item.family === next.family)?.id || '';
    setMaterialId(nextMaterial); setSubmittedMaterialId(nextMaterial);
    setSubmittedFabricColor(fabricColor); setSubmittedProcessId(processId);
    setFinalDesignPreview(null); setDesignPreviewRevision(0); setStyleVersions([]);
  };
  const setSelection = (group: string, optionId: string | null) => {
    setPatternUndo((old) => [...old.slice(-9), { selections, submitted: submittedSelections }]);
    const next = { ...selections, [group]: optionId };
    setSelections(next);
    setCompositionReady(false);
    setStylingConfirmed(false);
  };
  const undoPattern = () => {
    const previous = patternUndo[patternUndo.length - 1];
    if (!previous) return;
    setPatternUndo((old) => old.slice(0, -1));
    setSelections(previous.selections);
    setSubmittedSelections(previous.submitted);
    setCompositionReady(false);
    setStylingConfirmed(false);
  };
  const hasDraftPatternChanges = JSON.stringify(selections) !== JSON.stringify(submittedSelections) || materialId !== submittedMaterialId || fabricColor !== submittedFabricColor || processId !== submittedProcessId;
  const designPreviewReady = Boolean(finalDesignPreview?.url);
  const stylingReady = !hasDraftPatternChanges && (compositionReady || designPreviewReady);
  const submitPatternDraft = () => {
    setSubmittedSelections({ ...selections });
    setSubmittedMaterialId(materialId); setSubmittedFabricColor(fabricColor); setSubmittedProcessId(processId);
    setFinalDesignPreview(null);
    setDesignPreviewRevision((value) => value + 1);
    setCompositionReady(false);
    setStylingConfirmed(false);
  };
  const restoreStyleVersion = (version: typeof styleVersions[number]) => {
    setSelections({ ...version.selections });
    setSubmittedSelections({ ...version.selections });
    setMaterialId(version.materialId); setSubmittedMaterialId(version.materialId);
    setFabricColor(version.fabricColor); setSubmittedFabricColor(version.fabricColor);
    setProcessId(version.processId); setSubmittedProcessId(version.processId);
    setDesignPreviewRevision(version.revision);
    setFinalDesignPreview({ url: version.designUrl, input: version.designInput, revision: version.revision });
    setCompositionReady(false);
    setStylingConfirmed(false);
  };
  useEffect(() => {
    if (!stylingReady || !compositionSummary?.recipe_hash || !finalDesignPreview?.url) return;
    setStyleVersions((old) => {
      if (old.some((row) => row.revision === designPreviewRevision && row.designUrl === finalDesignPreview.url && row.recipeHash === compositionSummary.recipe_hash)) return old;
      const label = `V${old.length + 1}`;
      return [...old, {
        id: `${designPreviewRevision}-${compositionSummary.recipe_hash}-${finalDesignPreview.url}`,
        label,
        revision: designPreviewRevision,
        selections: { ...submittedSelections },
        materialId: submittedMaterialId,
        fabricColor: submittedFabricColor,
        processId: submittedProcessId,
        recipeHash: String(compositionSummary.recipe_hash),
        designUrl: finalDesignPreview.url,
        designInput: finalDesignPreview.input,
        pieceCount: Array.isArray(compositionSummary.pieces) ? compositionSummary.pieces.length : 0,
      }].slice(-8);
    });
  }, [stylingReady, compositionSummary, finalDesignPreview, designPreviewRevision, submittedSelections, submittedMaterialId, submittedFabricColor, submittedProcessId]);
  const capturePrintSnapshot = (): PrintSnapshot => ({
    assets: printAssets.map((asset) => ({ ...asset })),
    selectedAssetIds: { ...selectedPrintAssetIds },
    modes: { ...printModes },
    settings: { front: { ...printSettings.front }, back: { ...printSettings.back } },
    placements: printPlacements.map((placement) => ({ ...placement })),
  });
  const recordPrintHistory = () => {
    if (printHistoryBurst.current == null) {
      setPrintUndoHistory((old) => [...old.slice(-49), capturePrintSnapshot()]);
      setPrintRedoHistory([]);
    } else {
      window.clearTimeout(printHistoryBurst.current);
    }
    printHistoryBurst.current = window.setTimeout(() => { printHistoryBurst.current = null; }, 300);
  };
  const restorePrintSnapshot = (snapshot: PrintSnapshot) => {
    setPrintAssets(snapshot.assets.map((asset) => ({ ...asset })));
    setSelectedPrintAssetIds({ ...snapshot.selectedAssetIds });
    setPrintModes({ ...snapshot.modes });
    setPrintSettings({ front: { ...snapshot.settings.front }, back: { ...snapshot.settings.back } });
    setPrintPlacements(snapshot.placements.map((placement) => ({ ...placement })));
    setActivePlacementId(null);
  };
  const undoPrint = () => {
    const snapshot = printUndoHistory[printUndoHistory.length - 1]; if (!snapshot) return;
    if (printHistoryBurst.current != null) window.clearTimeout(printHistoryBurst.current);
    printHistoryBurst.current = null;
    setPrintRedoHistory((old) => [...old.slice(-49), capturePrintSnapshot()]);
    setPrintUndoHistory((old) => old.slice(0, -1));
    restorePrintSnapshot(snapshot);
  };
  const redoPrint = () => {
    const snapshot = printRedoHistory[printRedoHistory.length - 1]; if (!snapshot) return;
    setPrintUndoHistory((old) => [...old.slice(-49), capturePrintSnapshot()]);
    setPrintRedoHistory((old) => old.slice(0, -1));
    restorePrintSnapshot(snapshot);
  };
  useEffect(() => {
    if (step !== 'print') return;
    const handleHistoryShortcut = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return;
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === 'z') {
        event.preventDefault();
        if (event.shiftKey) redoPrint(); else undoPrint();
      } else if ((event.ctrlKey || event.metaKey) && key === 'y') {
        event.preventDefault();
        redoPrint();
      } else if ((key === 'delete' || key === 'backspace') && printModes[printView] === 'manual' && activePlacementId) {
        event.preventDefault();
        deletePlacement(activePlacementId);
      }
    };
    window.addEventListener('keydown', handleHistoryShortcut);
    return () => window.removeEventListener('keydown', handleHistoryShortcut);
  }, [step, printUndoHistory, printRedoHistory, printAssets, selectedPrintAssetIds, printModes, printSettings, printPlacements, printView, activePlacementId]);
  const selectPrintAsset = (assetId: string) => { if (assetId !== selectedPrintAssetIds[printView]) recordPrintHistory(); setSelectedPrintAssetIds((old) => ({ ...old, [printView]: assetId })); };
  const setPrintMode = (mode: PrintFaceMode) => { if (mode !== printModes[printView]) recordPrintHistory(); setPrintModes((old) => ({ ...old, [printView]: mode })); };
  const updateFaceSettings = (view: 'front' | 'back', patch: Partial<FaceSettings>) => { recordPrintHistory(); setPrintSettings((old) => ({ ...old, [view]: { ...old[view], ...patch } })); };
  const addPrintAsset = (asset: PrintAsset) => { recordPrintHistory(); setPrintAssets((old) => [...old, asset]); setSelectedPrintAssetIds((old) => ({ ...old, [printView]: asset.id })); };
  const updatePlacement = (id: string, patch: Partial<PrintPlacement>) => { recordPrintHistory(); setPrintPlacements((old) => old.map((item) => item.id === id ? { ...item, ...patch } : item)); };
  const addPlacement = (view: 'front' | 'back', x = 50, y = 50) => { recordPrintHistory(); const id = `print-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`; setPrintPlacements((old) => [...old, { id, view, assetId: selectedPrintAssetIds[view], x, y, size: 32, rotation: 0, cropLeft: 0, cropRight: 0, cropTop: 0, cropBottom: 0 }]); setActivePlacementId(id); };
  const deletePlacement = (id: string) => { recordPrintHistory(); setPrintPlacements((old) => old.filter((item) => item.id !== id)); setActivePlacementId((active) => active === id ? null : active); };

  const exportAll = async (designOverride: any = designState) => {
    // React passes a click event when this function is used directly as an onClick handler.
    // Never serialize that event; only production design state belongs in the export payload.
    if (designOverride?.currentTarget || designOverride?.nativeEvent) designOverride = designState;
    setExportError(''); setExporting(true);
    try {
      const base = geometryBase();
      const response = await fetch(`${base}/export`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ project_name: projectName, recipe, design: designOverride }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(typeof data?.detail === 'string' ? data.detail : data?.detail?.message || '当前组合未通过导出校验');
      }
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      const filename = disposition.match(/filename=([^;]+)/i)?.[1]?.replace(/"/g, '') || `smart-pattern-${selectedReference}-trial.zip`;
      const url = URL.createObjectURL(blob); const link = document.createElement('a');
      link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : '几何服务不可用，未生成任何伪生产文件。');
    } finally { setExporting(false); }
  };

  const canAccess = (target: Step) => target === 'measure' || target === 'design' && measurementsSaved || target === 'styling' && referenceConfirmed || target === 'print' && stylingConfirmed;
  if (showHome) return <HomePage language={language} setLanguage={setLanguage} onStart={() => setShowHome(false)} />;
  const globalNotice = exportError ? { kind: 'error', text: t(`导出失败：${exportError}`, `Export failed: ${exportError}`) } : analysisError ? { kind: 'error', text: analysisError } : catalogStatus !== 'ready' && step === 'design' ? { kind: 'status', text: t('正在连接设计服务…', 'Connecting to the design service…') } : null;
  return <div className="app-shell" style={{ '--theme-primary': fixedTheme.primary, '--theme-soft': fixedTheme.soft, '--theme-border': fixedTheme.border } as React.CSSProperties}><header className="topbar"><button type="button" className="brand-lockup" onClick={() => setShowHome(true)} aria-label="PatternMate"><img className="brand-mark" src={asset('/brand/logo.png')} alt="" /><img className="brand-wordmark" src={asset('/brand/patternmate-wordmark.svg')} alt="PatternMate" /></button><div className="project-title-view"><span title={projectName}>{isUntitledProjectName(projectName) ? t('新建文件', 'New file') : projectName}</span><button aria-label={t('编辑项目名称', 'Edit project name')} title={t('编辑项目名称', 'Edit project name')} onClick={() => { setProjectNameDraft(projectName); setProjectNameEditing(true); }}>✎</button></div><nav className="steps">{steps.map((item, index) => <button key={item.id} disabled={!canAccess(item.id)} className={item.id === step ? 'step active' : 'step'} onClick={() => canAccess(item.id) && setStep(item.id)}><span aria-hidden="true">{index + 1}</span>{item.label}</button>)}</nav><label className="language-picker" aria-label={t('语言', 'Language')}><select aria-label={t('语言', 'Language')} value={language} onChange={(event) => setLanguage(event.target.value as 'zh' | 'en')}><option value="zh">中文</option><option value="en">English</option></select></label>{stylingConfirmed && <button className="topbar-export" disabled={exporting} onClick={exportAll}>{exporting ? t('生成中…', 'Generating…') : t('导出', 'Export')}</button>}</header>
    {globalNotice && <div className={`global-notice ${globalNotice.kind}`} role={globalNotice.kind === 'error' ? 'alert' : 'status'}>{globalNotice.text}</div>}
    <main className="workspace resizable-workspace" style={{ gridTemplateColumns: `${sidebarWidth}px minmax(0,1fr)` }}><aside className="sidebar"><div className="side-heading"><img className="side-heading-mark" src={asset(`/icon/Group ${{ measure: 278, design: 279, styling: 280, print: 282 }[step]}.svg`)} alt="" aria-hidden="true" />{current.label}</div>
      {step === 'measure' && <MeasurePanel sex={sex} setSex={(value: Sex) => { setSex(value); setMeasurementsSaved(false); setReferenceConfirmed(false); setStylingConfirmed(false); }} measurements={measurements} updateMeasurement={updateMeasurement} rememberMeasurements={rememberMeasurements} setRememberMeasurements={setRememberMeasurements} error={measureError} onNext={saveMeasurements} />}
      {step === 'design' && <DesignPanel message={message} setMessage={setMessage} analyze={analyze} analyzing={analyzing} serviceReady={catalogStatus === 'ready'} intentMessages={intentMessages} assistantMessages={assistantMessages} generatedCard={generatedCard} analysisMode={analysisMode} />}
      {step === 'styling' && <StylingPanel family={family} baseItem={selectedReferenceInfo} referenceItems={referenceItems} intent={designIntent} unresolved={intentUnresolved} selections={selections} setSelection={setSelection} materialId={materialId} setMaterialId={setMaterialId} fabricColor={fabricColor} setFabricColor={setFabricColor} processId={processId} setProcessId={setProcessId} ready={stylingReady} hasDraftPatternChanges={hasDraftPatternChanges} onGenerate={submitPatternDraft} printSupport={selectedFabricPrintSupport} processSupport={selectedProcessSupport} onNext={() => { if (hasDraftPatternChanges) { setSubmittedSelections({ ...selections }); setSubmittedMaterialId(materialId); setSubmittedFabricColor(fabricColor); setSubmittedProcessId(processId); } const notes = [...(!compositionReady ? ['pattern_compose_unreviewed'] : []), ...(!finalDesignPreview?.url ? ['design_preview_missing'] : [])]; setPatternReview({ passed: notes.length === 0, notes }); const isPrint = processId.endsWith('.print'); const unsupported = isPrint ? selectedFabricPrintSupport === 'unsupported' : selectedProcessSupport === 'unsupported'; if (unsupported) { setPrintCompatibilityWarning(true); return; } setStylingConfirmed(true); if (isPrint) setStep('print'); else { const noPrintDesign = { ...designState, printSkipped: true, print: { ...designState.print, face_modes: { front: 'none', back: 'none' }, density_asset_ids: { front: null, back: null }, placements: [], assets: [] } }; void exportAll(noPrintDesign); } }} />}
      {step === 'print' && <PrintDesignPanel basePreview={finalDesignPreview} currentPreview={printPreviewUrl || finalDesignPreview?.url || ''} onPreviewChange={setPrintPreviewUrl} onAdopt={setAdoptedPrintConcept} designContext={{ recipe, composition: compositionSummary }} onExport={() => adoptedPrintConcept && exportAll({ ...designState, print_concept: { ...adoptedPrintConcept, adopted: true } })} />}
    </aside><section className="canvas-area">
      {step === 'measure' && <MeasureCanvas saved={measurementsSaved} measurements={measurements} sex={sex} />}
      {step === 'design' && <ReferenceGrid items={referenceItems} scores={referenceScores} order={referenceOrder} selected={selectedReference} onSelect={(item) => item.id === selectedReference ? setSelectedReference('') : chooseReference(item.id)} onConfirm={(item) => { chooseReference(item.id); setReferenceConfirmed(true); setStep('styling'); }} status={catalogStatus} />}
      {step === 'styling' && <PatternPreview recipe={recipe} baseCoverUrl={selectedReferenceInfo.coverUrl} generationRevision={designPreviewRevision} seedPreviewUrl={finalDesignPreview?.revision === designPreviewRevision ? finalDesignPreview.url : undefined} styleVersions={styleVersions} activeVersionId={styleVersions.find((row) => row.revision === designPreviewRevision && row.designUrl === finalDesignPreview?.url)?.id} onRestoreVersion={restoreStyleVersion} onGeneratedPreview={(url, input, revision) => setFinalDesignPreview({ url, input, revision })} onReplaceSelection={setSelection} onUndo={undoPattern} canUndo={patternUndo.length > 0} onExport={exportAll} onValidationChange={(ready) => { setCompositionReady(ready); }} onCompositionChange={setCompositionSummary} />}
      {step === 'print' && <PrintDesignPreview src={printPreviewUrl || finalDesignPreview?.url || ''} />}
    </section></main>{facetEditor && <TagCorrectionModal facets={semanticFacets} activeKey={facetEditor} selected={facetSelections} setActiveKey={setFacetEditor} onChoose={chooseFacet} onClose={() => setFacetEditor(null)} />}{printCompatibilityWarning && <DigitalPrintWarning isPrint={processId.endsWith('.print')} onBack={() => setPrintCompatibilityWarning(false)} onSkip={() => { const noPrintDesign = { ...designState, printSkipped: true, print: { ...designState.print, face_modes: { front: 'none', back: 'none' }, density_asset_ids: { front: null, back: null }, placements: [], assets: [] } }; setPrintCompatibilityWarning(false); setPrintModes({ front: 'none', back: 'none' }); setStylingConfirmed(true); void exportAll(noPrintDesign); }} />}{projectNameEditing && <div className="reference-modal-backdrop" onMouseDown={() => setProjectNameEditing(false)}><div className="project-name-modal" onMouseDown={(event) => event.stopPropagation()}><h2>{t('编辑项目名称', 'Edit project name')}</h2><input autoFocus aria-label={t('完整项目名称', 'Full project name')} value={projectNameDraft} onChange={(event) => setProjectNameDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && projectNameDraft.trim()) { setProjectName(projectNameDraft.trim()); setProjectNameEditing(false); } if (event.key === 'Escape') setProjectNameEditing(false); }} /><div><button onClick={() => setProjectNameEditing(false)}>{t('取消', 'Cancel')}</button><button className="primary" disabled={!projectNameDraft.trim()} onClick={() => { setProjectName(projectNameDraft.trim()); setProjectNameEditing(false); }}>{t('保存名称', 'Save name')}</button></div></div></div>}</div>;
}

function MeasurePanel({ sex, setSex, measurements, updateMeasurement, rememberMeasurements, setRememberMeasurements, error, onNext }: any) {
  const { t } = useLanguage();
  const fields = [['height', '身高', 'cm'], ['weight', '体重（可选）', 'kg'], ['chest', '胸围', 'cm'], ['waist', '腰围', 'cm'], ['shoulder', '肩宽', 'cm'], ['neck', '领围', 'cm'], ['sleeveLength', '袖长', 'cm'], ['upperArm', '上臂围', 'cm']];
  return <div className="panel-content measure-panel">
    <h3>{t('号型', 'Sizing')}</h3>
    <div className="option-row"><button className={sex === 'female' ? 'option selected' : 'option'} onClick={() => setSex('female')}>{t('女装国标', 'Women standard')}</button><button className={sex === 'male_general' ? 'option selected' : 'option'} onClick={() => setSex('male_general')}>{t('男装通用', 'Men general')}</button></div>
    <h3>{t('尺寸', 'Measurements')}</h3>
    <div className="measure-fields">{fields.map(([key, label, unit]) => <label className="field" key={key}><span>{languageField(label, t)}</span><div><input inputMode="decimal" value={measurements[key]} onChange={(event) => updateMeasurement(key, event.target.value)} placeholder="—" /><em>{unit}</em></div></label>)}</div>
    <label className="remember-measurements"><input type="checkbox" checked={rememberMeasurements} onChange={(event) => setRememberMeasurements(event.target.checked)} /><span>{t('记住本机尺寸', 'Remember on this device')}</span></label>
    {error && <p className="form-error">{error}</p>}
    <div className="measurement-preview-actions"><button className="primary full" onClick={onNext}>{t('确认并继续', 'Confirm and continue')}</button></div>
  </div>;
}

function languageField(label: string, t: (zh: string, en: string) => string) { const labels: Record<string, string> = { 身高: 'Height', '体重（可选）': 'Weight (optional)', 胸围: 'Chest', 腰围: 'Waist', 肩宽: 'Shoulder width', 领围: 'Neck', 袖长: 'Sleeve length', 上臂围: 'Upper arm' }; return t(label, labels[label] || label); }

function TypewriterText({ text }: { text: string }) {
  const [shown, setShown] = useState('');
  useEffect(() => {
    setShown('');
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { setShown(text); return; }
    let i = 0;
    let timer = 0;
    const step = () => {
      i += 1;
      setShown(text.slice(0, i));
      if (i < text.length) timer = window.setTimeout(step, 28);
    };
    timer = window.setTimeout(step, 28);
    return () => window.clearTimeout(timer);
  }, [text]);
  return <span>{shown}</span>;
}

function AssistantBubble({ lines, options, idle, children }: { lines: string[]; options?: boolean; idle: boolean; children?: React.ReactNode }) {
  const { t } = useLanguage();
  const [line, setLine] = useState(0);
  const [nudge, setNudge] = useState(false);
  useEffect(() => { setLine(0); }, [lines[0]]);
  useEffect(() => {
    setNudge(false);
    if (!idle) return;
    let cool = 0;
    const onMove = () => {
      const now = Date.now();
      if (now - cool < 4000) return;
      cool = now;
      if (lines.length > 1) setLine((n) => (n + 1) % lines.length);
      if (!options) return;
      setNudge(false);
      requestAnimationFrame(() => setNudge(true));
    };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, [idle, options, lines.length]);
  return <div className={`design-message assistant${nudge ? ' nudge' : ''}`}><small>{t('设计助手', 'Design assistant')}</small><TypewriterText text={lines[line % lines.length] || ''} />{children}</div>;
}

function DesignPanel({ message, setMessage, analyze, analyzing, serviceReady, intentMessages, assistantMessages, generatedCard, analysisMode }: any) {
  const { language, t } = useLanguage();
  const pickOption = (option: { value: string; label_zh: string; label_en: string }) => analyze(option.value === '_skip' ? '先跳过' : (language === 'zh' ? option.label_zh : option.label_en));
  const openingOptions = [
    { value: 'tshirt', label_zh: 'T恤', label_en: 'T-shirt' },
    { value: 'polo', label_zh: 'Polo', label_en: 'Polo' },
    { value: 'shirt', label_zh: '衬衫', label_en: 'Shirt' },
    { value: '_skip', label_zh: '先跳过', label_en: 'Skip for now' },
  ];
  const openingLines = [
    t('直接说说你想做的衣服，右侧参考款会跟着对话更新。先选一个品类，或直接打字。', 'Describe the garment you want — pick a category, or just type. References on the right update as we talk.'),
    t('想做什么衣服？点 T恤、Polo 或衬衫，右边马上换参考款。', 'What are you making? Pick T-shirt, Polo, or Shirt and the references update.'),
    t('也可以先跳过品类，直接打字说风格、场合或袖型。', 'Or skip the category and type a style, occasion, or sleeve.'),
    t('不确定就点「先跳过」，我再问你穿去哪、要什么版型。', 'Not sure? Skip for now — I will ask where you wear it and what fit you want.'),
  ];
  const last = intentMessages.length - 1;
  const idle = !analyzing && !message.trim();
  const replyButtons = (options: { value: string; label_zh: string; label_en: string }[]) => <div className="design-quick-replies">{options.map((option) => <button key={option.value} className={option.value === '_skip' ? 'skip' : undefined} disabled={analyzing || !serviceReady} onClick={() => pickOption(option)}>{language === 'zh' ? option.label_zh : option.label_en}</button>)}</div>;
  const thinking = <div className="design-message assistant thinking"><small>{t('设计助手', 'Design assistant')}</small><span className="design-thinking" aria-label={t('正在思考', 'Thinking')}><i /><i /><i /></span></div>;
  return <div className="panel-content design-panel">
    <div className="design-conversation" aria-live="polite">
      {intentMessages.length === 0 && (serviceReady ? <AssistantBubble lines={openingLines} options idle={idle}>{replyButtons(openingOptions)}</AssistantBubble> : thinking)}
      {intentMessages.map((item: string, index: number) => {
        const live = index === last && !analyzing;
        const hasOptions = live && generatedCard?.options?.length > 0;
        const lines = live ? [assistantMessages[index]].filter(Boolean) : [assistantMessages[index]];
        return <React.Fragment key={`${index}-${item}`}><div className="design-message user"><span>{item}</span></div>{assistantMessages[index] && (live ? <AssistantBubble lines={lines} options={hasOptions} idle={false}>{hasOptions && replyButtons(generatedCard.options)}</AssistantBubble> : <div className="design-message assistant"><small>{t('设计助手', 'Design assistant')}</small><span>{assistantMessages[index]}</span></div>)}</React.Fragment>;
      })}
      {analyzing && thinking}
    </div>
    <div className="chat-input"><input value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && serviceReady && analyze()} placeholder={t('告诉ai您的需求', 'Tell AI what you need')} /><button disabled={analyzing || !serviceReady || !message.trim()} onClick={analyze}>{analyzing ? '…' : t('发送', 'Send')}</button></div>
    {analysisMode === 'rules' && intentMessages.length > 0 && <small className="analysis-mode">{t('当前使用基础分析模式', 'Basic analysis mode is active')}</small>}
  </div>;
}

function TagCorrectionModal({ facets, activeKey, selected, setActiveKey, onChoose, onClose }: any) { const { language, t } = useLanguage(); const active = facets.find((facet: SemanticFacet) => facet.key === activeKey) || facets[0]; return <div className="reference-modal-backdrop" onMouseDown={onClose}><div className="tag-correction-modal" onMouseDown={(event) => event.stopPropagation()}><button className="modal-close" onClick={onClose}>×</button><h2>{t('调整设计偏好', 'Edit design preferences')}</h2><p>{t('请选择更符合你想法的选项。', 'Choose the options that best match your idea.')}</p><div className="facet-tabs">{facets.map((facet: SemanticFacet) => <button className={facet.key === active?.key ? 'active' : ''} key={facet.key} onClick={() => setActiveKey(facet.key)}>{language === 'zh' ? facet.label : englishSlug(facet.key)}</button>)}</div><div className="facet-values">{active?.values.map((item: { value: string; score: number }) => <button className={selected[active.key] === item.value ? 'selected' : ''} key={item.value} onClick={() => onChoose(active.key, item.value)}><span>{language === 'zh' ? semanticLabel(item.value) : englishSlug(item.value)}</span></button>)}</div></div></div>; }

function StylingPanel({ family, baseItem, referenceItems = [], intent, unresolved, selections, setSelection, materialId, setMaterialId, fabricColor, setFabricColor, processId, setProcessId, ready, readyHint = '', hasDraftPatternChanges, onGenerate, printSupport, processSupport: selectedProcessSupport, onNext }: any) {
  const { language, t } = useLanguage();
  const [dyeOpen, setDyeOpen] = useState(false);
  const [openCard, setOpenCard] = useState<'pieces' | 'fabric' | 'process'>('pieces');
  const dyeBtn = useRef<HTMLButtonElement>(null);
  const options = optionsForFamily(family);
  const availableFabrics = fabricOptions.filter((item) => item.family === family);
  const fabricGroups = [...new Set(availableFabrics.map((item) => item.group))];
  const activeCompatibility = processId.endsWith('.print') ? printSupport : selectedProcessSupport;
  const optionName = (item?: PatternOption) => item ? (language === 'zh' ? item.label_zh : englishSlug(item.slug)) : t('未标注', 'Unlabelled');
  const groups = groupOrder[family as GarmentFamily];
  const swatches = ['#C4C17C', '#F3C13E', '#9DC9E8', '#F2821C', '#F497A2', '#9A95ED', '#60332D'];
  const mosaic = [...swatches, ...swatches.slice(0, 5)];
  const cards = [
    { id: 'pieces' as const, label: t('版片', 'Pattern pieces') },
    { id: 'fabric' as const, label: t('面料', 'Fabric') },
    { id: 'process' as const, label: t('工艺', 'Process') },
  ];
  const renderGroup = (group: string, index: number) => (
    <details className="choice-group" key={group} open>
      <summary><i className="choice-swatch" style={{ background: swatches[index % swatches.length] }} />{language === 'zh' ? groupLabels[group] : englishSlug(group)}</summary>
      <div className="asset-grid">
        {group === 'special' && <button className={!selections[group] ? 'asset-card selected none-card' : 'asset-card none-card'} onClick={() => setSelection(group, null)}>{t('不添加', 'None')}</button>}
        {optionsForGroup(family, group, corpusOptionIds(referenceItems, family, group)).map((item: PatternOption) => (
          <button aria-pressed={selections[group] === item.id} className={selections[group] === item.id ? 'asset-card selected' : 'asset-card'} key={item.id} onClick={() => setSelection(group, selections[group] === item.id ? null : item.id)}>
            {item.thumbnail && <img src={item.thumbnail} alt={optionName(item)} />}
            <span>{optionName(item)}</span>
          </button>
        ))}
      </div>
    </details>
  );
  const activeIndex = cards.findIndex((card) => card.id === openCard);
  return <div className="panel-content styling-panel">
    <nav className="styling-progress" aria-label={t('搭配步骤', 'Styling steps')}>
      <div className="styling-progress-mosaic" aria-hidden="true">
        {mosaic.map((color, index) => <i key={index} style={{ background: index < [4, 6, 12][activeIndex] ? color : '#efe8df' }} />)}
      </div>
      <div className="styling-progress-labels">
        {cards.map((card, index) => (
          <button type="button" key={card.id} className={openCard === card.id ? 'active' : activeIndex > index ? 'done' : ''} aria-current={openCard === card.id ? 'step' : undefined} onClick={() => setOpenCard(card.id)}>{card.label}</button>
        ))}
      </div>
    </nav>
    <div className="styling-body">
      {openCard === 'pieces' && <>
        {baseItem?.id && <div className="current-base"><img src={String(baseItem.coverUrl || '').replace(/\/cover\.(png|jpe?g|webp)$/i, '/thumb.jpg')} alt="" /><div><strong>{baseItem.id}</strong><span>{t('当前基础纸样', 'Current base pattern')}</span><small>{['silhouette', 'collar', 'placket', 'sleeve', 'cuff'].map((group) => optionName(options.find((item) => item.id === baseItem.baseOptionIds?.[group]))).filter((name) => name && name !== t('未标注', 'Unlabelled')).join(' · ')}</small></div></div>}
        {groups.map((group, index) => renderGroup(group, index))}
      </>}
      {openCard === 'fabric' && <>
        <div className="fabric-color-picker"><div><strong>{t('面料颜色', 'Fabric color')}</strong></div><div className="fabric-color-swatches">{fabricColors.map((color) => <button key={color} aria-label={`${t('选择面料颜色', 'Choose fabric color')} ${color}`} className={fabricColor === color ? 'selected' : ''} style={{ background: color }} onClick={() => { setFabricColor(color); setDyeOpen(false); }} />)}<button ref={dyeBtn} type="button" className="fabric-color-custom" title={t('自定义颜色', 'Custom color')} aria-label={t('自定义颜色', 'Custom color')} onClick={() => setDyeOpen((open) => !open)}><span>＋</span></button></div>{dyeOpen && dyeBtn.current && <DyePicker value={fabricColor} onChange={setFabricColor} onClose={() => setDyeOpen(false)} anchor={dyeBtn.current} />}</div>
        {fabricGroups.map((group, index) => {
          const info = fabricGroupInfo[group];
          return <details className="choice-group" key={group} open>
            <summary><i className="choice-swatch" style={{ background: swatches[index % swatches.length] }} />{language === 'zh' ? info?.label || group : englishSlug(group)}</summary>
            <div className="asset-grid">
              {availableFabrics.filter((item) => item.group === group).map((item) => (
                <div key={item.id}>
                  <button className={materialId === item.id ? 'asset-card selected' : 'asset-card'} onClick={() => setMaterialId(item.id)}>
                    <img src={item.swatch} alt={item.label} />
                    <span>{language === 'zh' ? item.label : englishSlug(item.id.split('.').pop() || item.id)}</span>
                  </button>
                  {materialId === item.id && activeCompatibility === 'unsupported' && <p className="fabric-choice-warning">{processId.endsWith('.print') ? t('不支持数码印花，请更换面料。', 'Digital print is unsupported; change fabric.') : t('当前工艺不适用该面料。', 'The selected process is not suitable for this fabric.')}</p>}
                </div>
              ))}
            </div>
          </details>;
        })}
      </>}
      {openCard === 'process' && <div className="asset-grid process-grid">{processOptions.map((item) => <div key={item.id}><button className={processId === item.id ? 'asset-card selected' : 'asset-card'} onClick={() => setProcessId(item.id)}><img src={item.thumbnail} alt={item.label} /><span>{language === 'zh' ? item.label : englishSlug(item.id.split('.').pop() || item.id)}</span></button>{processId === item.id && selectedProcessSupport === 'unsupported' && <p className="fabric-choice-warning">{t('当前面料不适用此工艺。', 'This fabric is not suitable for this process.')}</p>}</div>)}</div>}
    </div>
    {hasDraftPatternChanges && <button className="secondary full" onClick={onGenerate}>{t('生成组合预览', 'Generate preview')}</button>}
    <button className="primary full" onClick={onNext}>{processId.endsWith('.print') ? t('确认并进入印花', 'Confirm and continue') : t('确认并导出', 'Confirm and export')}</button>
  </div>;
}

function DigitalPrintWarning({ onBack, onSkip, isPrint }: { onBack: () => void; onSkip: () => void; isPrint: boolean }) {
  const { t } = useLanguage();
  return <div className="reference-modal-backdrop" role="dialog" aria-modal="true"><div className="digital-print-modal"><h2>{isPrint ? t('当前面料不支持数码印花', 'Digital printing is not supported for this fabric') : t('当前面料不适用所选工艺', 'The selected process is not suitable for this fabric')}</h2><p>{isPrint ? t('你可以返回“编辑搭配”更换面料，或继续导出不含印花的工业生产文件。', 'Return to Pattern Mix to change the fabric, or export industrial files without prints.') : t('请返回“编辑搭配”更换面料或工艺；系统不会导出未经材料适用性确认的工艺组合。', 'Return to Pattern Mix to change the fabric or process. The system will not export an unverified process combination.')}</p><div><button onClick={onBack}>{t('返回编辑搭配', 'Return to Pattern Mix')}</button>{isPrint && <button className="primary" onClick={onSkip}>{t('无需印花，直接导出', 'Export without prints')}</button>}</div></div></div>;
}

function MeasureCanvas({ saved }: { saved: boolean; measurements: Measurements; sex: Sex }) {
  const { t } = useLanguage();
  return <div className="measurement-workspace"><div className="measurement-guide-visual"><div className={saved ? 'measure-status saved' : 'measure-status'}>{saved ? t('✓ 尺寸已保存', '✓ Measurements saved') : t('按图示位置测量', 'Measure at the marked points')}</div><img src={asset('/measurement/measurement-diagram.png')} alt={t('人体尺寸测量位置示意', 'Body measurement diagram')} /><p>{t('软尺水平贴合身体并保持自然呼吸；建议测量两次。', 'Keep the tape level and breathe normally; measure twice.')}</p></div></div>;
}
function ReferenceGrid({ items, scores: _scores, order, selected, onSelect, onConfirm, status }: { items: ReferenceItem[]; scores: Record<string, number>; order: string[]; selected: string; onSelect: (item: ReferenceItem) => void; onConfirm: (item: ReferenceItem) => void; status: 'loading' | 'ready' | 'offline' }) {
  const { t } = useLanguage();
  const byId = new Map(items.map((item) => [item.id, item]));
  const sorted = order.length ? order.map((id) => byId.get(id)).filter((item): item is ReferenceItem => Boolean(item)) : items;
  if (!sorted.length) {
    return <div className="reference-grid-empty"><span className="reference-loading-mark">↻</span><strong>{status === 'offline' ? t('正在重新连接设计服务', 'Reconnecting to the design service') : t('正在载入参考库', 'Loading reference library')}</strong></div>;
  }
  return <div className="reference-grid all-references">{sorted.map((item, index) => {
    const swatch = asset(`/icon/swatch-${String((index % 12) + 1).padStart(2, '0')}.svg`);
    const isSelected = item.id === selected;
    return <article aria-label={`${t('选择参考图', 'Select reference')} ${item.id}`} aria-pressed={isSelected} className={isSelected ? 'reference-card selected' : 'reference-card'} key={item.id} onClick={() => onSelect(item)}><img className="reference-swatch" src={swatch} alt="" aria-hidden="true" /><img className="reference-image" src={item.coverUrl.replace(/\/cover\.(png|jpe?g|webp)$/i, '/thumb.jpg')} alt={t('服装参考图', 'Garment reference')} loading="lazy" />{isSelected && <button className="primary reference-confirm" type="button" onClick={(event) => { event.stopPropagation(); onConfirm(item); }}>{t('确认参考款', 'Confirm reference')}</button>}</article>;
  })}</div>;
}

function isComposeSandboxRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash === 'sandbox' || new URLSearchParams(window.location.search).has('sandbox');
}

function isSleeveVlmSandboxRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash === 'sleeve-vlm' || hash === 'sandbox/sleeve-vlm' || new URLSearchParams(window.location.search).has('sleeve-vlm');
}

function isShirtSandboxRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash === 'shirt-sandbox' || hash === 'sandbox/shirt' || new URLSearchParams(window.location.search).has('shirt-sandbox');
}

function isRelabelRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash === 'relabel' || hash === 'sandbox/relabel' || new URLSearchParams(window.location.search).has('relabel');
}

function Root() {
  const [route, setRoute] = useState<'app' | 'sandbox' | 'sleeve-vlm' | 'shirt-sandbox' | 'relabel'>(() => {
    if (isRelabelRoute()) return 'relabel';
    if (isShirtSandboxRoute()) return 'shirt-sandbox';
    if (isSleeveVlmSandboxRoute()) return 'sleeve-vlm';
    if (isComposeSandboxRoute()) return 'sandbox';
    return 'app';
  });
  useEffect(() => {
    const sync = () => {
      if (isRelabelRoute()) setRoute('relabel');
      else if (isShirtSandboxRoute()) setRoute('shirt-sandbox');
      else if (isSleeveVlmSandboxRoute()) setRoute('sleeve-vlm');
      else if (isComposeSandboxRoute()) setRoute('sandbox');
      else setRoute('app');
    };
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    return () => {
      window.removeEventListener('hashchange', sync);
      window.removeEventListener('popstate', sync);
    };
  }, []);
  if (route === 'relabel') return <RelabelQueue />;
  if (route === 'shirt-sandbox') return <ShirtSandbox />;
  if (route === 'sleeve-vlm') return <SleeveVlmSandbox />;
  if (route === 'sandbox') return <ComposeSandbox />;
  return <App />;
}

const rootElement = document.getElementById('root')! as HTMLElement & { __chi27Root?: ReturnType<typeof createRoot> };
const appRoot = rootElement.__chi27Root || (rootElement.__chi27Root = createRoot(rootElement));
appRoot.render(<LanguageProvider><Root /></LanguageProvider>);
