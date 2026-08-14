export type ComponentStatus = 'unchanged' | 'applied' | 'applied_review_required' | 'retained_current' | 'human_accepted' | 'human_rejected' | 'human_modified';

export type ComponentResult = {
  operation_id: string;
  group: string;
  status: ComponentStatus | string;
  donor_case_id?: string | null;
  option_id?: string | null;
  modified_entity_ids?: string[];
  validation_issues?: { code: string; severity: string; message: string; operation_id?: string }[];
  review_required?: boolean;
  provenance?: Record<string, any>;
};

export type ReviewLedger = {
  schema: string;
  trial_status?: string;
  human_review_required?: boolean;
  operations?: ComponentResult[];
  protected_entity_hashes?: Record<string, string>;
};
