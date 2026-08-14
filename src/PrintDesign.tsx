import React from 'react';
import { useLanguage } from './Language';
import { aiBase } from './apiBase';
import './PrintDesign.css';

export type PrintAsset = { id: string; name: string; src: string };
export type FaceSettings = { density: number; size: number; gap: number; x: number; y: number; rotation: number };
export type PrintFaceMode = 'none' | 'density' | 'manual';
export type PrintPlacement = { id: string; view: 'front' | 'back'; assetId: string; x: number; y: number; size: number; rotation: number; cropLeft: number; cropRight: number; cropTop: number; cropBottom: number };
export type ProductionPrintAsset = { url: string; mode: 'motif' | 'seamless'; format: 'PNG'; width_px: number; height_px: number; dpi: number; color_space: string; transparent: boolean };
export type AdoptedPrintConcept = { preview_url: string; production_asset: ProductionPrintAsset; input: Record<string, any> };
type GeneratedDraft = { id: string; src: string; productionAsset: ProductionPrintAsset; input: Record<string, any> };
type PrintConversationTurn = { id: string; role: 'user' | 'assistant'; text: string; drafts?: GeneratedDraft[] };

export function PrintDesignPanel(props: any) {
  const { t } = useLanguage();
  const { basePreview, currentPreview, onPreviewChange, onAdopt, designContext, onExport } = props;
  const [aiPrompt, setAiPrompt] = React.useState('');
  const [aiStatus, setAiStatus] = React.useState('');
  const [aiError, setAiError] = React.useState('');
  const [aiHistory, setAiHistory] = React.useState<PrintConversationTurn[]>([]);
  const [uploadImage, setUploadImage] = React.useState<{ name: string; src: string } | null>(null);
  const [adopted, setAdopted] = React.useState('');
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file) return;
    const src = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
    setUploadImage({ name: file.name, src });
    event.target.value = '';
  };
  const generatePrint = async () => {
    if (!aiPrompt.trim() || aiStatus) return;
    const base = aiBase();
    const prompt = aiPrompt.trim();
    if (!basePreview?.url) { setAiError(t('请先在编辑搭配中完成2D设计预览。', 'Complete the 2D design preview first.')); return; }
    const priorRequests = aiHistory.filter((turn) => turn.role === 'user').map((turn) => turn.text).slice(-8);
    setAiHistory((current) => [...current, { id: `user-${Date.now()}`, role: 'user', text: prompt }]);
    setAiPrompt('');
    setAiError(''); setAiStatus(t('正在准备印花方案', 'Preparing the print concept'));
    try {
      const input = { prompt, history: priorRequests, source_preview_url: currentPreview || basePreview.url, inspiration_image_data_url: uploadImage?.src || '', design_context: { ...designContext, base_preview_input: basePreview.input } };
      const response = await fetch(`${base}/garment-print/jobs`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(input) });
      if (!response.ok) throw new Error(t('印花方案生成暂时不可用，请稍后重试。', 'Print concept generation is temporarily unavailable.'));
      const created = await response.json();
      let completed: any = created;
      while (!['succeeded', 'failed', 'cancelled'].includes(completed.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 1800));
        const statusResponse = await fetch(`${base}/garment-print/jobs/${created.job_id}`);
        if (!statusResponse.ok) throw new Error(t('无法读取生成进度', 'Unable to read generation progress'));
        completed = await statusResponse.json();
        setAiStatus(`${t('正在生成穿着效果', 'Generating the garment result')} · ${completed.progress}%`);
      }
      if (completed.status !== 'succeeded' || !completed.preview_url || !completed.production_asset?.url) throw new Error(t('印花方案或生产图案生成失败，请调整描述后重试。', 'The concept or production artwork could not be generated. Adjust the request and try again.'));
      const draft = { id: `concept-${created.job_id}`, src: `${base}${completed.preview_url}`, productionAsset: { ...completed.production_asset, url: `${base}${completed.production_asset.url}` }, input };
      onPreviewChange(draft.src); setUploadImage(null);
      setAiHistory((current) => [...current, { id: `assistant-${created.job_id}`, role: 'assistant', text: t('已生成新的上衣印花效果，可继续对话修改或采用当前方案。', 'A new garment print result is ready. Continue refining it or adopt this version.'), drafts: [draft] }]);
    } catch (error) { setAiError(error instanceof Error ? error.message : t('图案生成失败', 'Pattern generation failed')); }
    finally { setAiStatus(''); }
  };
  return <div className="panel-content print-panel-redesign">
    <div className="print-panel-title"><strong>{t('印花设计', 'Print Design')}</strong></div>
    <section className="print-ai-card">
      <div className="print-ai-heading"><button type="button" className="print-history-reset" disabled={!aiHistory.length || Boolean(aiStatus)} onClick={() => { setAiHistory([]); setAiError(''); }}>{t('新建创作', 'New session')}</button></div>
      <div className="print-ai-history" aria-label={t('印花创作记录', 'Print creation history')} aria-live="polite">
        {!aiHistory.length && <div className="print-ai-turn assistant"><small>{t('印花创作助手', 'Print design assistant')}</small><p>{t('描述你希望呈现在上衣上的图案，也可以上传参考图片。我会保留当前服装与人物，只调整印花。', 'Describe the artwork you want on the garment, or upload a reference image. I will preserve the garment and wearer while refining the print.')}</p></div>}
        {aiHistory.map((turn) => <div key={turn.id} className={`print-ai-turn ${turn.role}`}>{turn.role === 'assistant' && <small>{t('印花创作助手', 'Print design assistant')}</small>}<p>{turn.text}</p>{turn.drafts && <div className="print-draft-grid">{turn.drafts.map((draft) => <article key={draft.id}><img src={draft.src} alt={t('上衣印花效果方案', 'Garment print concept')} /><div className="print-draft-meta"><span>{draft.productionAsset.mode === 'motif' ? t('透明底定位图案', 'Transparent placement artwork') : t('连续纹样', 'Seamless repeat')}</span><span>{draft.productionAsset.width_px} × {draft.productionAsset.height_px}px</span></div><button type="button" className={adopted === draft.id ? 'adopted' : ''} onClick={() => { setAdopted(draft.id); onPreviewChange(draft.src); onAdopt({ preview_url: draft.src, production_asset: draft.productionAsset, input: draft.input }); }}>{adopted === draft.id ? t('已采用', 'Adopted') : t('采用当前方案', 'Adopt concept')}</button></article>)}</div>}</div>)}
        {aiStatus && <div className="print-ai-turn assistant thinking"><small>{t('印花创作助手', 'Print design assistant')}</small><span className="print-thinking"><i /><i /><i /></span><em>{aiStatus}</em></div>}
      </div>
      {uploadImage && <div className="print-upload-chip"><img src={uploadImage.src} alt="" /><span>{uploadImage.name}</span><button type="button" onClick={() => setUploadImage(null)}>×</button></div>}
      <div className="print-ai-compose"><textarea value={aiPrompt} onChange={(event) => setAiPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void generatePrint(); } }} placeholder={aiHistory.length ? t('继续描述印花修改…', 'Continue describing print revisions…') : t('告诉我你想要的印花…', 'Tell me what print you want…')} /><div><label className="print-chat-upload" title={t('上传参考图', 'Add image')}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} /><span aria-hidden="true">＋</span>{t('图片', 'Image')}</label><button type="button" disabled={!aiPrompt.trim() || Boolean(aiStatus)} onClick={generatePrint}>{aiHistory.length ? t('继续修改', 'Refine') : t('发送', 'Send')}</button></div></div>
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
