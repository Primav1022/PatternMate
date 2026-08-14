import React, { useCallback, useEffect, useState } from 'react';
import { defaultSelections, fabricOptions, GarmentFamily, optionsForFamily } from './catalogs';

type CatalogItem = {
  case_id: string;
  category: string;
  supported: boolean;
  base_option_ids?: Record<string, string | null>;
};

type StrategyPlan = {
  mode?: string;
  roles?: string[];
  drop_host_sleeves?: boolean;
  slug?: string;
  source?: string;
  reason?: string | null;
  llm_ok?: boolean;
  llm_error?: string | null;
  rule_fallback?: Record<string, any>;
};

type SandboxResult = {
  ok: boolean;
  group?: string;
  option_id?: string;
  strategy?: StrategyPlan;
  rule_strategy?: Record<string, any>;
  png_data_url?: string;
  pieces?: Array<{ role?: string; source_case_id?: string; width_mm?: number; height_mm?: number }>;
  component_results?: any[];
  sources?: Record<string, any>;
  sleeve_cap_match?: Record<string, any>;
  status?: string;
  model_configured?: boolean;
};

const GEOMETRY = import.meta.env.VITE_GEOMETRY_BASE_URL || 'http://127.0.0.1:8788';

function familyOf(item?: CatalogItem | null): GarmentFamily {
  return item?.category === 'shirt' || item?.category === 'blouse' ? 'shirt' : 'tshirt';
}

export function SleeveVlmSandbox() {
  const [health, setHealth] = useState('checking…');
  const [modelStatus, setModelStatus] = useState<Record<string, any> | null>(null);
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [caseId, setCaseId] = useState('C2590529');
  const [group, setGroup] = useState<'sleeve' | 'neckline' | 'cuff'>('sleeve');
  const [optionId, setOptionId] = useState('tshirt.sleeve.puff');
  const [useLlm, setUseLlm] = useState(true);
  const [modelBaseUrl, setModelBaseUrl] = useState('');
  const [modelName, setModelName] = useState('');
  const [modelApiKey, setModelApiKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);

  const selected = items.find((item) => item.case_id === caseId) || null;
  const family = familyOf(selected);
  const groupOptions = optionsForFamily(family).filter((item) => item.group === group);

  useEffect(() => {
    fetch(`${GEOMETRY}/health`).then((r) => r.json()).then((d) => setHealth(d.ok ? 'ok' : 'bad')).catch(() => setHealth('down'));
    fetch(`${GEOMETRY}/sandbox/sleeve-vlm/status`).then((r) => r.json()).then(setModelStatus).catch(() => setModelStatus(null));
    fetch(`${GEOMETRY}/catalog`).then((r) => r.json()).then((d) => {
      const rows = (d.items || []).filter((item: CatalogItem) => item.supported !== false);
      setItems(rows);
      if (rows.length && !rows.some((item: CatalogItem) => item.case_id === caseId)) {
        setCaseId(rows[0].case_id);
      }
    }).catch(() => setError('无法加载 catalog'));
  }, []);

  useEffect(() => {
    const base = selected?.base_option_ids?.[group];
    if (base) setOptionId(String(base));
    else if (groupOptions[0]?.id) setOptionId(groupOptions[0].id);
  }, [selected?.case_id, family, group]);

  const run = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    setError('');
    setResult(null);
    const t0 = performance.now();
    const baseOpts = selected?.base_option_ids || {};
    const selections = {
      ...defaultSelections(family),
      ...Object.fromEntries(Object.entries(baseOpts).filter(([, v]) => v)),
      [group]: optionId,
    };
    const fabric = fabricOptions.find((item) => item.family === family)?.id || '';
    const body: Record<string, any> = {
      recipe: {
        family,
        sex: 'female',
        base_case_id: caseId,
        measurements_cm: { height: 160, chest: 85, waist: 66, shoulder: 38, neck: 34, sleeveLength: 52, upperArm: 26 },
        fit: 'regular',
        ease_cm: 8,
        material_id: fabric,
        fabric_color: '#ffffff',
        compact_layout: true,
        selections,
        base_option_ids: baseOpts,
        intent_constraints: {},
        execution_mode: 'simple_piece_swap',
      },
      group,
      use_llm: useLlm,
      png_width: 720,
    };
    if (modelBaseUrl.trim()) body.model_base_url = modelBaseUrl.trim();
    if (modelName.trim()) body.model_name = modelName.trim();
    if (modelApiKey.trim()) body.model_api_key = modelApiKey.trim();
    try {
      const res = await fetch(`${GEOMETRY}/sandbox/strategy-compose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.[0]?.msg || data.message || res.statusText);
      setResult(data);
      setElapsedMs(Math.round(performance.now() - t0));
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  }, [caseId, selected, family, group, optionId, useLlm, modelBaseUrl, modelName, modelApiKey]);

  const strategy = result?.strategy;

  return (
    <div className="compose-sandbox sleeve-vlm-sandbox">
      <header className="sandbox-top">
        <div>
          <strong>策略判断 → 合成 Sandbox</strong>
          <small>大模型判断改哪些片 → 一次 compose 看效果</small>
        </div>
        <div className="sandbox-meta">
          <span className={health === 'ok' ? 'ok' : 'bad'}>geometry {health}</span>
          <span className={modelStatus?.model_configured ? 'ok' : 'bad'}>
            model {modelStatus?.model_configured ? `ready (${modelStatus?.model_name || '…'})` : '未配置'}
          </span>
          <a href="#/sandbox">回 compose sandbox</a>
          <a href="#/">回主站</a>
        </div>
      </header>

      <div className="sandbox-body">
        <aside className="sandbox-side">
          <label>
            base case
            <select value={caseId} onChange={(e) => setCaseId(e.target.value)}>
              {items.map((item) => (
                <option key={item.case_id} value={item.case_id}>{item.case_id} · {item.category}</option>
              ))}
            </select>
          </label>

          <label>
            group
            <select value={group} onChange={(e) => setGroup(e.target.value as 'sleeve' | 'neckline' | 'cuff')}>
              <option value="sleeve">sleeve</option>
              <option value="neckline">neckline</option>
              <option value="cuff">cuff</option>
            </select>
          </label>

          <label>
            option
            <select value={optionId} onChange={(e) => setOptionId(e.target.value)}>
              {groupOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>{opt.label_zh} · {opt.slug}</option>
              ))}
            </select>
          </label>

          <label className="sandbox-auto">
            <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
            调用 LLM 判策略（关则纯规则）
          </label>

          <fieldset>
            <legend>LLM（可空 = 用服务端 .env）</legend>
            <label>
              base URL
              <input placeholder="https://…/v1" value={modelBaseUrl} onChange={(e) => setModelBaseUrl(e.target.value)} />
            </label>
            <label>
              model name
              <input placeholder="gpt-4o / gemini-…" value={modelName} onChange={(e) => setModelName(e.target.value)} />
            </label>
            <label>
              API key
              <input type="password" placeholder="sk-…（仅本次请求，不落盘）" value={modelApiKey} onChange={(e) => setModelApiKey(e.target.value)} autoComplete="off" />
            </label>
          </fieldset>

          <div className="sandbox-actions">
            <button className="primary" type="button" disabled={loading || !caseId} onClick={() => void run()}>
              {loading ? '判断+合成中…' : '判策略并合成'}
            </button>
          </div>

          {error && <p className="sandbox-error">{error}</p>}

          {result && strategy && (
            <div className="sandbox-stats">
              <div>耗时 {elapsedMs ?? '—'} ms · status {result.status}</div>
              <div>mode <strong>{strategy.mode}</strong> · source {strategy.source}</div>
              <div>drop_host_sleeves {String(strategy.drop_host_sleeves)}</div>
              <div>roles {(strategy.roles || []).join(', ') || '—'}</div>
              <div>{strategy.reason || '—'}</div>
              <div>
                LLM {strategy.llm_ok ? 'ok' : `fallback${strategy.llm_error ? `: ${String(strategy.llm_error).slice(0, 80)}` : ''}`}
              </div>
              {result.sleeve_cap_match && (
                <div>
                  袖山匹配 {result.sleeve_cap_match.applied ? 'applied' : 'skipped'}
                  {result.sleeve_cap_match.body_armhole != null && ` · AH=${result.sleeve_cap_match.body_armhole}`}
                  {result.sleeve_cap_match.ease != null && ` · ease=${result.sleeve_cap_match.ease}`}
                  {result.sleeve_cap_match.max_abs_error_ratio != null && ` · err≤${result.sleeve_cap_match.max_abs_error_ratio}`}
                  {!result.sleeve_cap_match.applied && result.sleeve_cap_match.reason
                    ? ` · ${result.sleeve_cap_match.reason}`
                    : ''}
                </div>
              )}
            </div>
          )}

          <p className="sandbox-fabric-note">
            袖迁移：袖山弧长 ≈ 袖窿×ease（只扭 sleeve_cap）。规则：puff/set-in→只袖；raglan→衣身+袖；flutter→衣身去袖。
          </p>
        </aside>

        <section className="sandbox-stage">
          <div className="sandbox-tools">
            <span>合成预览</span>
            {strategy?.mode && <span className="sandbox-preview-mode">{strategy.mode}</span>}
          </div>
          <div className="sleeve-vlm-grid" style={{ gridTemplateColumns: '1fr' }}>
            {result?.png_data_url ? (
              <figure>
                <img src={result.png_data_url} alt="strategy compose preview" />
                <figcaption>{result.option_id} · {strategy?.mode}</figcaption>
              </figure>
            ) : (
              <div className="sandbox-empty">{loading ? 'LLM 判策略 + compose…' : '点左侧「判策略并合成」'}</div>
            )}
          </div>
          {result && (
            <pre className="sandbox-json">
              {JSON.stringify(
                {
                  strategy: result.strategy,
                  sleeve_cap_match: result.sleeve_cap_match,
                  rule_strategy: result.rule_strategy,
                  pieces: result.pieces,
                  sources: result.sources,
                  component_results: result.component_results,
                },
                null,
                2,
              )}
            </pre>
          )}
        </section>
      </div>
    </div>
  );
}
