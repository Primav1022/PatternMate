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
  applied: '已替换',
  applied_review_required: '等待审核',
  retained_current: '保留原部件',
  unchanged: '未变更',
  human_accepted: '人工接受',
  human_rejected: '人工拒绝',
  human_modified: '人工修改',
  failed: '失败',
};

export function CompositionReviewPanel({ componentResults = [], reviewLedger, warnings = [], t = zh }: Props) {
  const needsReview = Boolean(reviewLedger?.human_review_required || componentResults.some((row) => row.review_required !== false));
  if (!needsReview && !componentResults.length && !warnings.length) return null;
  return <section className="composition-review-panel" aria-label={t('组合审核清单', 'Composition review list')}>
    <h3>{t('组合审核', 'Composition review')}</h3>
    <p className="validation-note">{needsReview ? t('自动结果需要纸样师复核。', 'Automatic results require pattern-maker review.') : t('当前操作无需人工审核。', 'No manual review is needed.')}</p>
    {componentResults.length > 0 && <div className="review-operation-list">{componentResults.map((row) => {
      const modified = row.modified_entity_ids?.length || 0;
      const donorCount = row.provenance?.donor_candidates?.length || 0;
      return <article className={`review-operation ${row.status}`} key={row.operation_id || row.group}>
        <strong>{row.group}</strong>
        <span>{statusLabel[row.status] || row.status}</span>
        <details><summary>{t('查看迁移详情', 'Migration details')}</summary><small>{t('修改图元', 'Modified entities')}：{modified} · {t('候选供体', 'Donor candidates')}：{donorCount}</small></details>
        {row.validation_issues?.map((issue) => <em key={`${row.operation_id}-${issue.code}`}>{issue.message}</em>)}
      </article>;
    })}</div>}
    {warnings.slice(0, 4).map((warning) => <p className="validation-note" key={warning}>{warning}</p>)}
  </section>;
}
