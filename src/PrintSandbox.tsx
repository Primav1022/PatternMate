import React from 'react';
import { PrintDesignPanel, PrintDesignPreview } from './PrintDesign';
import { asset } from './asset';
import { useLanguage } from './Language';

const MOCK_PREVIEW = '/home-gallery/garment-01.png';

export function PrintSandbox() {
  const { language, setLanguage, t } = useLanguage();
  const [variant, setVariant] = React.useState<'print' | 'tie-dye'>('print');
  const [previewUrl, setPreviewUrl] = React.useState(MOCK_PREVIEW);
  const [adopted, setAdopted] = React.useState<any>(null);
  const [sidebarWidth, setSidebarWidth] = React.useState(298);
  const drag = React.useRef<{ x: number; w: number } | null>(null);
  const basePreview = React.useMemo(() => ({ url: MOCK_PREVIEW, input: { sandbox: true }, revision: 1 }), []);
  return (
    <div className="app-shell" style={{ '--theme-primary': '#e07a3d', '--theme-soft': '#fff1e8', '--theme-border': '#f0c4a4' } as React.CSSProperties}>
      <header className="topbar">
        <div className="brand-lockup">
          <img className="brand-mark" src={asset('/brand/logo.png')} alt="" />
          <img className="brand-wordmark" src={asset('/brand/patternmate-wordmark.svg')} alt="PatternMate" />
        </div>
        <div className="project-title-view"><span>{t('工艺创作 Sandbox', 'Process sandbox')}</span></div>
        <nav className="steps">
          <button type="button" className={variant === 'print' ? 'step active' : 'step'} onClick={() => { setVariant('print'); setPreviewUrl(MOCK_PREVIEW); setAdopted(null); }}><span>1</span>{t('印花', 'Print')}</button>
          <button type="button" className={variant === 'tie-dye' ? 'step active' : 'step'} onClick={() => { setVariant('tie-dye'); setPreviewUrl(MOCK_PREVIEW); setAdopted(null); }}><span>2</span>{t('扎染', 'Tie-dye')}</button>
        </nav>
        <label className="language-picker" aria-label={t('语言', 'Language')}>
          <select value={language} onChange={(event) => setLanguage(event.target.value as 'zh' | 'en')}>
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </label>
      </header>
      <main className="workspace resizable-workspace" style={{ gridTemplateColumns: `${sidebarWidth}px minmax(0,1fr)`, ['--sidebar-width' as string]: `${sidebarWidth}px` }}>
        <aside className="sidebar">
          <div className="side-heading">
            <img className="side-heading-mark" src={asset('/icon/Group 282.svg')} alt="" aria-hidden="true" />
            {t('工艺创作', 'Process')}
          </div>
          <PrintDesignPanel
            key={variant}
            variant={variant}
            basePreview={basePreview}
            currentPreview={previewUrl}
            onPreviewChange={setPreviewUrl}
            onAdopt={setAdopted}
            designContext={{ sandbox: true }}
            onExport={() => { if (adopted) window.alert(t('Sandbox 不导出，只用来改第四步。', 'Sandbox does not export. Use this page to tune step 4.')); }}
          />
        </aside>
        <div
          className="column-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label={t('调整左侧栏宽度', 'Resize sidebar')}
          onPointerDown={(event) => {
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { x: event.clientX, w: sidebarWidth };
          }}
          onPointerMove={(event) => {
            if (!drag.current) return;
            const workspace = event.currentTarget.parentElement;
            const scale = workspace && workspace.offsetWidth ? workspace.getBoundingClientRect().width / workspace.offsetWidth : 1;
            setSidebarWidth(Math.min(640, Math.max(220, Math.round(drag.current.w + (event.clientX - drag.current.x) / (scale || 1)))));
          }}
          onPointerUp={() => { drag.current = null; }}
          onPointerCancel={() => { drag.current = null; }}
        />
        <section className="canvas-area">
          <PrintDesignPreview src={previewUrl} />
        </section>
      </main>
    </div>
  );
}
