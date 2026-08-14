import patternCatalogJson from './catalog-data/pattern-options.v1.json';
import fabricCatalogJson from './catalog-data/fabrics.v1.json';
import processCatalogJson from './catalog-data/processes.v1.json';
import { asset } from './asset';

export type GarmentFamily = 'tshirt' | 'shirt';

export type PatternOption = {
  id: string;
  family: GarmentFamily;
  compatible_families?: GarmentFamily[];
  group: string;
  slug: string;
  label_zh: string;
  thumbnail: string;
};

export type FabricOption = {
  id: string;
  family: GarmentFamily;
  group: string;
  slug: string;
  label: string;
  description: string;
  swatch: string;
};

export type ProcessOption = {
  id: string;
  slug: string;
  label: string;
  thumbnail: string;
};

export const patternOptions: PatternOption[] = (patternCatalogJson.options as PatternOption[]).map((item) => ({
  ...item,
  thumbnail: asset(item.thumbnail.replace(/\.svg$/i, '.png')),
}));

export const fabricGroupInfo: Record<string, { label: string; visual: string; suitable: string }> = {
  'soft-basic': { label: '柔软基础', visual: '柔软、亲肤，表面自然', suitable: '基础T恤、极简风、日系风' },
  'heavy-structured': { label: '厚重结构', visual: '硬挺、有重量，廓形支撑明显', suitable: 'Oversized、潮牌、工装街头' },
  'textured-surface': { label: '纹理肌理', visual: '表面凹凸，触感和纹路明显', suitable: '复古、日系、高级休闲' },
  'stretch-fit': { label: '弹性贴身', visual: '高弹、修身，能够贴合身体', suitable: '女性款、打底与运动休闲' },
  'performance-tech': { label: '功能科技', visual: '轻薄、光滑，带科技与功能感', suitable: '运动、户外、科技风' },
  'statement-fashion': { label: '个性时装', visual: '强视觉效果和高辨识度', suitable: '潮流、舞台、设计师款' },
  'crisp-formal': { label: '商务挺括', visual: '平整、干净，结构清晰', suitable: '商务、学院与正式衬衫' },
  'natural-casual': { label: '自然休闲', visual: '天然纹理、松弛、带自然褶皱', suitable: '度假、日系、轻商务' },
  'structured-workwear': { label: '厚重工装', visual: '硬挺耐用，重量与工装感明显', suitable: '复古、户外、工装衬衫' },
  'luxury-draping': { label: '柔软垂坠', visual: '流动、柔软并带光泽', suitable: '高级女装、晚装、轻奢' },
  'sheer-lightweight': { label: '轻薄透明', visual: '空气感、透视、轻盈', suitable: '潮流、仙女风与叠穿' },
  'fashion-statement': { label: '艺术特殊', visual: '图案或特殊光泽突出', suitable: '个性、潮流与视觉重点款' },
};

const fabricDescriptions: Record<string, string> = {
  'cotton-jersey': '表面平整、自然起褶，是最经典的T恤质感。',
  'tencel-cotton': '比棉更柔滑、垂坠，带轻微自然光泽。',
  'heavy-cotton': '厚度明显，肩部支撑强，成衣廓形稳定。',
  'canvas-cotton': '粗糙硬挺，适合强调工装与结构感。',
  'waffle-knit': '方格凸起形成清晰的立体纹理。',
  'slub-cotton': '纱线粗细不均，呈现自然做旧颗粒感。',
  'terry-cloth': '表面小毛圈，触感柔软蓬松。',
  'rib-knit': '竖向条纹明显，弹性高、贴身性好。',
  'stretch-jersey': '表面平滑，伸缩性适合修身版型。',
  'performance-polyester': '轻薄平滑，带微光泽和运动感。',
  mesh: '孔洞结构透气，具有轻微透视效果。',
  'reflective-fabric': '光线下产生高反射，强调科技感。',
  'cooling-fiber': '触感清凉，适合夏季与功能服装。',
  'velvet-knit': '柔软绒面吸光，视觉沉稳而高级。',
  metallic: '金属反光明显，具有未来与舞台感。',
  sequin: '闪耀颗粒形成强烈的装饰效果。',
  'sheer-mesh': '轻薄半透明，适合叠穿和局部设计。',
  poplin: '组织细密平滑，适合干净正式的衬衫。',
  oxford: '表面颗粒更明显，比府绸更休闲。',
  'mercerized-cotton': '棉质平整，带克制的高级光泽。',
  linen: '天然纤维纹理明显，会产生自然褶皱。',
  chambray: '外观类似轻薄牛仔，休闲但不厚重。',
  denim: '斜纹厚重，耐磨且结构感强。',
  corduroy: '竖条绒面明显，适合复古与秋冬款。',
  twill: '斜向纹理清晰，兼顾挺括与耐用。',
  'waxed-cotton': '表面油蜡光泽，带防护与户外感。',
  'silk-satin': '高光泽、流动垂坠，适合轻奢设计。',
  rayon: '触感柔软，垂坠自然且易形成流动褶皱。',
  velvet: '绒面厚实高级，吸光效果明显。',
  chiffon: '飘逸透明，适合轻盈层次。',
  organza: '半透明但更挺括，能够保持造型。',
  lace: '镂空花纹明显，装饰性和透视感并存。',
};

const fabricRows: FabricOption[] = [];
for (const [family, groups] of Object.entries(fabricCatalogJson.groups)) {
  for (const [group, values] of Object.entries(groups)) {
    for (const [slug, label] of values as [string, string][]) {
      fabricRows.push({
        id: `${family}.${group}.${slug}`,
        family: family as GarmentFamily,
        group,
        slug,
        label,
        description: fabricDescriptions[slug] || fabricGroupInfo[group]?.visual || '服装面料选项',
        swatch: asset(`/ui-assets/v1/fabric-options/${family}/${group}/${slug}/swatch.png`),
      });
    }
  }
}
export const fabricOptions = fabricRows;

export const processOptions: ProcessOption[] = processCatalogJson.processes.map((item) => ({
  id: item.id,
  slug: item.slug,
  label: item.label_zh,
  thumbnail: asset(item.thumbnail),
}));

export const groupOrder: Record<GarmentFamily, string[]> = {
  tshirt: ['neckline', 'sleeve', 'garment_length'],
  shirt: ['silhouette', 'collar', 'placket', 'sleeve', 'cuff', 'garment_length'],
};

export const groupLabels: Record<string, string> = {
  neckline: '领口',
  sleeve: '袖型',
  garment_length: '衣长',
  special: '特殊设计',
  silhouette: '廓形',
  collar: '领型',
  placket: '前门襟',
  cuff: '袖口',
};

export function optionsForFamily(family: GarmentFamily): PatternOption[] {
  return patternOptions.filter((item) => item.family === family || item.compatible_families?.includes(family));
}

const DONOR_OPTION_GROUPS = new Set(['collar', 'sleeve', 'cuff', 'neckline', 'placket', 'silhouette']);

/** Shirt remix UI follows the annotation taxonomy, not the leftover catalog extras (娃娃领 / 蝙蝠袖). */
const SHIRT_ANNOTATED_OPTIONS: Record<string, Set<string>> = {
  silhouette: new Set(['shirt.silhouette.a-line', 'shirt.silhouette.oversized', 'shirt.silhouette.fitted-x', 'shirt.silhouette.regular-fit', 'shirt.silhouette.relaxed-h']),
  collar: new Set(['shirt.collar.open-v-pointed', 'shirt.collar.casual-wide-lapel', 'shirt.collar.pointed', 'shirt.collar.bow-tie']),
  placket: new Set(['shirt.placket.full', 'shirt.placket.half', 'shirt.placket.diagonal', 'shirt.placket.ruffled', 'shirt.placket.concealed']),
  sleeve: new Set(['shirt.sleeve.regular', 'shirt.sleeve.puff', 'shirt.sleeve.bell', 'shirt.sleeve.flutter']),
  cuff: new Set(['shirt.cuff.regular', 'shirt.cuff.gathered', 'shirt.cuff.ruffled']),
};

export function corpusOptionIds(
  items: Array<{ family?: string; baseOptionIds?: Record<string, string> }>,
  family: GarmentFamily,
  group: string,
): string[] {
  const ids = new Set<string>();
  for (const item of items) {
    if (item.family && item.family !== family) continue;
    const id = item.baseOptionIds?.[group];
    if (id) ids.add(id);
  }
  return [...ids];
}

export function optionsForGroup(family: GarmentFamily, group: string, availableIds?: Iterable<string>): PatternOption[] {
  let rows = optionsForFamily(family).filter((item) => item.group === group);
  const annotated = family === 'shirt' ? SHIRT_ANNOTATED_OPTIONS[group] : undefined;
  if (annotated) rows = rows.filter((item) => annotated.has(item.id));
  if (!availableIds || !DONOR_OPTION_GROUPS.has(group)) return rows;
  const allow = new Set(availableIds);
  if (!allow.size) return rows;
  const filtered = rows.filter((item) => allow.has(item.id));
  return filtered.length ? filtered : rows;
}

export function composeSelections(family: GarmentFamily, selections: Record<string, string | null>): Record<string, string | null> {
  return Object.fromEntries(groupOrder[family].map((group) => [group, selections[group] ?? null]));
}

export function executionModeFor(family: GarmentFamily): 'simple_piece_swap' | 'shirt_strategy' {
  return family === 'shirt' ? 'shirt_strategy' : 'simple_piece_swap';
}

export function defaultSelections(family: GarmentFamily): Record<string, string | null> {
  return Object.fromEntries(groupOrder[family].map((group) => [group, null]));
}

const CUFFLESS_SLEEVES: Record<string, { zh: string; en: string }> = {
  flutter: { zh: '飞袖并入衣身，没有独立袖口', en: 'Flutter sleeves join the body and have no separate cuff' },
  bell: { zh: '喇叭袖以下摆为袖口，不能再选独立袖克夫', en: 'Bell sleeves finish at the hem — no separate cuff' },
};

function optionSlug(optionId: string | null | undefined): string {
  return String(optionId || '').split('.').pop() || '';
}

export type SelectionConflict = {
  block: boolean;
  clear?: Record<string, string | null>;
  shake: string[];
  shakeOption?: string;
  message: { zh: string; en: string };
};

/** Shirt-only: flutter/bell have no independent cuff piece. */
export function selectionConflict(
  family: GarmentFamily,
  group: string,
  optionId: string,
  selections: Record<string, string | null>,
): SelectionConflict | null {
  if (family !== 'shirt') return null;
  const slug = optionSlug(optionId);
  if (group === 'sleeve' && CUFFLESS_SLEEVES[slug] && selections.cuff) {
    const copy = CUFFLESS_SLEEVES[slug];
    return {
      block: false,
      clear: { cuff: null },
      shake: ['cuff'],
      message: { zh: `${copy.zh}，已取消袖口选择`, en: `${copy.en}. Cuff selection cleared.` },
    };
  }
  if (group === 'cuff' && CUFFLESS_SLEEVES[optionSlug(selections.sleeve)]) {
    const copy = CUFFLESS_SLEEVES[optionSlug(selections.sleeve)];
    return {
      block: true,
      shake: ['sleeve'],
      shakeOption: optionId,
      message: copy,
    };
  }
  return null;
}

