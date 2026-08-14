import React from 'react';
import { ComponentResult, ReviewLedger } from './compositionTypes';

type Props = {
  componentResults?: ComponentResult[];
  reviewLedger?: ReviewLedger | null;
  warnings?: string[];
  t?: (zh: string, en: string) => string;
};

const zh = (value: string) => value;
const statusLabel: Record<string, string> = {
  applied: '已自动应用 · 待审核',
  applied_review_required: '已自动应用 · 待审核',
  retained_current: '保留当前部件',
  unchanged: '未变更',
  human_accepted: '人工接受',
  human_rejected: '人工拒绝',
  human_modified: '人工修改',
};

export function CompositionReviewPanel({ componentResults = [], reviewLedger, warnings = [], t = zh }: Props) {
  const needsReview = Boolean(reviewLedger?.human_review_required || componentResults.some((row) => row.review_required !== false));
  if (!needsReview && !componentResults.length && !warnings.length) return null;
  return <section className="composition-review-panel" aria-label={t('组合审核清单', 'Composition review list')}>
    <h3>{t('组合审核', 'Composition review')}</h3>
    <p className="validation-note">{needsReview ? t('自动结果为试样，需要纸样师复核后才能量产。', 'This is an automatic trial and requires pattern-maker review before production.') : t('暂无需人工审核的自动操作。', 'No automatic operation currently requires human review.')}</p>
    {componentResults.length > 0 && <div className="review-operation-list">{componentResults.map((row) => {
      const modified = row.modified_entity_ids?.length || 0;
      const donorCount = row.provenance?.donor_candidates?.length || 0;
      return <article className={`review-operation ${row.status}`} key={row.operation_id || row.group}>
        <strong>{row.group}</strong>
        <span>{statusLabel[row.status] || row.status}</span>
        <small>{t('修改图元', 'Modified entities')}：{modified} · {t('候选供体', 'Donor candidates')}：{donorCount}</small>
        {row.validation_issues?.map((issue) => <em key={`${row.operation_id}-${issue.code}`}>{issue.message}</em>)}
      </article>;
    })}</div>}
    {warnings.slice(0, 4).map((warning) => <p className="validation-note" key={warning}>{warning}</p>)}
  </section>;
}
