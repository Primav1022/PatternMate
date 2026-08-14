import React from 'react';
import { useLanguage } from './Language';
import './PrintDesign.css';

export type PrintAsset = { id: string; name: string; src: string };
export type FaceSettings = { density: number; size: number; gap: number; x: number; y: number; rotation: number };
export type PrintFaceMode = 'none' | 'density' | 'manual';
export type PrintPlacement = { id: string; view: 'front' | 'back'; assetId: string; x: number; y: number; size: number; rotation: number; cropLeft: number; cropRight: number; cropTop: number; cropBottom: number };
export type ProductionPrintAsset = { url: string; mode: 'motif' | 'seamless'; format: 'PNG'; width_px: number; height_px: number; dpi: number; color_space: string; transparent: boolean };
export type AdoptedPrintConcept = { preview_url: string; production_asset: ProductionPrintAsset; input: Record<string, any> };
type GeneratedPattern = { id: string; src: string; mode: 'motif' | 'seamless'; input: Record<string, any> };
type GeneratedDraft = { id: string; src: string; productionAsset: ProductionPrintAsset; input: Record<string, any> };
type PrintConversationTurn = { id: string; role: 'user' | 'assistant'; text: string; patterns?: GeneratedPattern[]; drafts?: GeneratedDraft[] };

const resolvePrintMode = (text: string): 'motif' | 'seamless' => /满印|满版|连续|无缝|平铺|重复|循环|四方连续|二方连续|all[- ]?over|seamless|repeat|tileable|tiled/i.test(text) ? 'seamless' : 'motif';
const requestsTypography = (text: string) => /文字|字母|字体|标语|口号|文案|英文|中文|typograph|lettering|slogan|wordmark|\btext\b|\bwords?\b/i.test(text);

export function PrintDesignPanel(props: any) {
  const { t } = useLanguage();
  const { basePreview, onPreviewChange, onAdopt, designContext, onExport } = props;
  const [aiPrompt, setAiPrompt] = React.useState('');
  const [aiStatus, setAiStatus] = React.useState('');
  const [aiError, setAiError] = React.useState('');
  const [aiHistory, setAiHistory] = React.useState<PrintConversationTurn[]>([]);
  const [uploadImage, setUploadImage] = React.useState<{ name: string; src: string } | null>(null);
  const [adopted, setAdopted] = React.useState('');
  const promptRef = React.useRef<HTMLTextAreaElement>(null);
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const src = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
    setUploadImage({ name: file.name, src });
    event.target.value = '';
  };
  const pollJob = async (url: string, jobId: string, label: string) => {
    let completed: any = { status: 'queued' };
    while (!['succeeded', 'failed', 'cancelled'].includes(completed.status)) {
      await new Promise((resolve) => window.setTimeout(resolve, 1800));
      const statusResponse = await fetch(`${url}/${jobId}`);
      if (!statusResponse.ok) throw new Error(t('无法读取生成进度', 'Unable to read generation progress'));
      completed = await statusResponse.json();
      setAiStatus(`${label} · ${completed.progress}%`);
    }
    return completed;
  };
  const applyPattern = async (pattern: GeneratedPattern) => {
    if (aiStatus || !basePreview?.url) return;
    const base = import.meta.env.VITE_AI_BASE_URL || '/ai';
    setAiError(''); setAiStatus(t('正在将选中印花应用到服饰', 'Applying the selected print to the garment'));
    try {
      const input = { prompt: pattern.input.prompt, history: pattern.input.history, source_preview_url: basePreview.url, selected_print_url: pattern.src, selected_print_mode: pattern.mode, design_context: { ...designContext, base_preview_input: basePreview.input } };
      const response = await fetch(`${base}/garment-print/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('印花穿着效果生成暂时不可用，请稍后重试。', 'Garment print rendering is temporarily unavailable.'));
      const created = await response.json();
      const completed = await pollJob(`${base}/garment-print/jobs`, created.job_id, t('正在生成最终穿着效果', 'Generating the final garment result'));
      if (completed.status !== 'succeeded' || !completed.preview_url || !completed.production_asset?.url) throw new Error(t('选中印花未能应用到服饰，请稍后重试。', 'The selected print could not be applied to the garment.'));
      const draft = { id: `concept-${created.job_id}`, src: `${base}${completed.preview_url}`, productionAsset: { ...completed.production_asset, url: `${base}${completed.production_asset.url}` }, input };
      setAdopted(''); onAdopt(null); onPreviewChange(draft.src);
      setAiHistory((current) => [...current, { id: `assistant-${created.job_id}`, role: 'assistant', text: t('印花服饰预览稿已生成。你可以继续输入修改要求，或点击“采用当前方案”。', 'The printed garment preview is ready. Continue with another request, or click “Adopt concept”.'), drafts: [draft] }]);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('穿着效果生成失败', 'Garment rendering failed')); }
    finally { setAiStatus(''); }
  };
  const generatePrint = async () => {
    if (!aiPrompt.trim() || aiStatus) return;
    const base = import.meta.env.VITE_AI_BASE_URL || '/ai';
    const prompt = aiPrompt.trim();
    if (!basePreview?.url) { setAiError(t('请先在编辑搭配中完成2D设计预览。', 'Complete the 2D design preview first.')); return; }
    const priorRequests = aiHistory.filter((turn) => turn.role === 'user').map((turn) => turn.text).slice(-8);
    const direction = [...priorRequests, prompt].join('; ');
    const mode = resolvePrintMode(direction);
    const input = { prompt, history: priorRequests, mode, width: 1536, height: 1536, candidate_count: 3, inspiration_image_data_url: uploadImage?.src || '', negative_prompt: `watermark, logo, brand mark, caption, label, mockup, garment, person, frame${requestsTypography(direction) ? '' : ', text, letters, words, typography'}` };
    setAiHistory((current) => [...current, { id: `user-${Date.now()}`, role: 'user', text: prompt }]);
    setAiPrompt(''); setAiError(''); setAiStatus(t('正在生成多种印花图样', 'Generating multiple print options'));
    setAdopted(''); onAdopt(null); onPreviewChange(basePreview.url);
    try {
      const response = await fetch(`${base}/print/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('印花图样生成暂时不可用，请稍后重试。', 'Print option generation is temporarily unavailable.'));
      const created = await response.json();
      const completed = await pollJob(`${base}/print/jobs`, created.job_id, t('正在生成多种印花图样', 'Generating multiple print options'));
      if (completed.status !== 'succeeded' || !Array.isArray(completed.result_urls) || completed.result_urls.length < 2) throw new Error(t('未能生成足够的印花图样，请调整描述后重试。', 'Not enough print options were generated. Adjust the request and try again.'));
      const patterns = completed.result_urls.map((url: string, index: number) => ({ id: `pattern-${created.job_id}-${index}`, src: `${base}${url}`, mode, input }));
      setUploadImage(null);
      setAiHistory((current) => [...current, { id: `assistant-${created.job_id}`, role: 'assistant', text: t('已生成 3 种印花图样。请选择一种，我会把它叠加到前一步的服饰图上。', 'Three print options are ready. Choose one to apply it to the previous garment image.'), patterns }]);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('图案生成失败', 'Pattern generation failed')); }
    finally { setAiStatus(''); }
  };
  return <div className="panel-content print-panel-redesign">
    <div className="print-panel-title"><strong>{t('印花设计', 'Print Design')}</strong></div>
    <section className="print-ai-card">
      <div className="print-ai-heading"><button type="button" className="print-history-reset" disabled={!aiHistory.length || Boolean(aiStatus)} onClick={() => { setAiHistory([]); setAiError(''); setAdopted(''); onAdopt(null); onPreviewChange(basePreview?.url || ''); }}>{t('新建创作', 'New session')}</button></div>
      <div className="print-ai-history" aria-label={t('印花创作记录', 'Print creation history')} aria-live="polite">
        {!aiHistory.length && <div className="print-ai-turn assistant"><small>{t('印花创作助手', 'Print design assistant')}</small><p>{t('先描述你想要的印花，也可以上传参考图片。我会生成 3 种独立图样供你选择，再把选中的图样应用到前一步服饰图上。', 'Describe the print you want, or upload a reference image. I will create three standalone options, then apply your selection to the previous garment image.')}</p></div>}
        {aiHistory.map((turn) => <div key={turn.id} className={`print-ai-turn ${turn.role}`}>{turn.role === 'assistant' && <small>{t('印花创作助手', 'Print design assistant')}</small>}<p>{turn.text}</p>{turn.patterns && <div className="print-pattern-grid">{turn.patterns.map((pattern, index) => <article key={pattern.id}><img src={pattern.src} alt={`${t('印花图样方案', 'Print option')} ${index + 1}`} /><div className="print-draft-meta"><span>{t(`方案 ${index + 1}`, `Option ${index + 1}`)}</span><span>{pattern.mode === 'motif' ? t('定位图案', 'Placement motif') : t('连续纹样', 'Seamless repeat')}</span></div><button type="button" disabled={Boolean(aiStatus)} onClick={() => void applyPattern(pattern)}>{t('选择并生成成衣', 'Select and apply')}</button></article>)}</div>}{turn.drafts && <div className="print-draft-grid">{turn.drafts.map((draft) => <article key={draft.id}><img src={draft.src} alt={t('印花服饰预览稿', 'Printed garment preview draft')} /><div className="print-draft-meta"><span>{draft.productionAsset.mode === 'motif' ? t('透明底定位图案', 'Transparent placement artwork') : t('连续纹样', 'Seamless repeat')}</span><span>{draft.productionAsset.width_px} × {draft.productionAsset.height_px}px</span></div><div className="print-draft-actions"><button type="button" onClick={() => { setAdopted(''); onAdopt(null); onPreviewChange(draft.src); setAiPrompt(''); window.requestAnimationFrame(() => promptRef.current?.focus()); }}>{t('继续修改', 'Continue editing')}</button><button type="button" className={adopted === draft.id ? 'adopted' : ''} onClick={() => { setAdopted(draft.id); onPreviewChange(draft.src); onAdopt({ preview_url: draft.src, production_asset: draft.productionAsset, input: draft.input }); }}>{adopted === draft.id ? t('已采用', 'Adopted') : t('采用当前方案', 'Adopt concept')}</button></div></article>)}</div>}</div>)}
        {aiStatus && <div className="print-ai-turn assistant thinking"><small>{t('印花创作助手', 'Print design assistant')}</small><span className="print-thinking"><i /><i /><i /></span><em>{aiStatus}</em></div>}
      </div>
      {uploadImage && <div className="print-upload-chip"><img src={uploadImage.src} alt="" /><span>{uploadImage.name}</span><button type="button" onClick={() => setUploadImage(null)}>×</button></div>}
      <div className="print-ai-compose"><textarea ref={promptRef} value={aiPrompt} onChange={(event) => setAiPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void generatePrint(); } }} placeholder={aiHistory.length ? t('描述下一组印花图样…', 'Describe the next set of print options…') : t('告诉我你想要的印花…', 'Tell me what print you want…')} /><div><label className="print-chat-upload" title={t('上传参考图', 'Add image')}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} /><span aria-hidden="true">＋</span>{t('图片', 'Image')}</label><button type="button" disabled={!aiPrompt.trim() || Boolean(aiStatus)} onClick={generatePrint}>{aiHistory.length ? t('生成新方案', 'New options') : t('生成图样', 'Generate options')}</button></div></div>
      {aiError && <p className="form-error">{aiError}</p>}
    </section>
    <button className="primary full" disabled={!adopted} onClick={onExport}>{t('下载生产文件包', 'Download production package')}</button>
  </div>;
}

export function PrintDesignPreview({ src }: { src: string }) {
  const { t } = useLanguage();
  const [fullscreen, setFullscreen] = React.useState(false);
  return <div className={`print-concept-preview${fullscreen ? ' fullscreen' : ''}`}>{src ? <img src={src} alt={t('印花穿着效果预览', 'Garment print preview')} /> : <p>{t('请先在编辑搭配中生成2D设计预览。', 'Generate the 2D design preview first.')}</p>}{src && <button type="button" onClick={() => setFullscreen((value) => !value)}>{fullscreen ? t('退出全屏', 'Exit fullscreen') : t('全屏查看', 'Fullscreen')}</button>}</div>;
}
