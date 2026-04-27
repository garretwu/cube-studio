import type { DiagnosedRootCause, DiagnosisResult, RemediationPlan } from "../api/types";

/**
 * Normalize diagnosis root causes into a render-safe non-empty array.
 *
 * Purpose:
 * - provide one canonical conversion entry for all diagnosis pages/models.
 * Input/Output:
 * - input: optional DiagnosisResult from backend/session store;
 * - output: normalized DiagnosedRootCause array that is always non-empty.
 * Compatibility rationale:
 * - we only keep parser tolerance for malformed payloads; UI output always uses the new structure.
 * Why:
 * - centralizing this conversion avoids repeating root-cause fallback logic across pages and stores.
 */
export function getNormalizedRootCauses(result?: DiagnosisResult | null): DiagnosedRootCause[] {
  if (!result) {
    return [];
  }
  const rootCauses = Array.isArray(result.root_cause) ? result.root_cause : [];
  const normalized: DiagnosedRootCause[] = [];
  for (let index = 0; index < rootCauses.length; index += 1) {
    const item = rootCauses[index];
    const title = String(item?.title ?? "").trim();
    if (!title) {
      continue;
    }
    normalized.push({
      id: String(item.id || `rc-${index + 1}`),
      title,
      layer: item.layer,
      entities: Array.isArray(item.entities) ? item.entities.filter(Boolean) : [],
      confidence: Number.isFinite(item.confidence) ? item.confidence : result.confidence,
      certainty: item.certainty ?? result.diagnosis_certainty,
      status: item.status ?? "suspected",
      evidence_summary: String(item.evidence_summary ?? result.impact_summary ?? title).trim() || title,
      impact_summary: String(item.impact_summary ?? result.impact_summary ?? title).trim() || title,
      distinguishing_verification: item.distinguishing_verification ?? null,
      factor_type: item.factor_type ?? null,
      evidence_refs: Array.isArray(item.evidence_refs) ? item.evidence_refs.filter(Boolean) : [],
      evidence_interpretation: item.evidence_interpretation ?? null,
      recommended_fix: item.recommended_fix ?? null,
    });
  }

  if (normalized.length > 0) {
    return normalized;
  }

  // Single-item fallback: when no valid root cause item exists, force one safe placeholder item.
  return [
    {
      id: "rc-1",
      title: String(result.impact_summary || "证据不足，暂无法确认根因"),
      layer: "platform",
      entities: [],
      confidence: result.confidence,
      certainty: result.diagnosis_certainty,
      status: "suspected",
      evidence_summary: String(result.impact_summary || "证据不足，暂无法确认根因"),
      impact_summary: String(result.impact_summary || "证据不足，暂无法确认根因"),
      distinguishing_verification: null,
      factor_type: null,
      evidence_refs: [],
      evidence_interpretation: null,
      recommended_fix: null,
    },
  ];
}

/**
 * Return the primary root cause (`root_cause[0]`) for summary and primary-card rendering.
 *
 * Purpose:
 * - standardize first-root-cause lookup across view-model layers.
 * Input/Output:
 * - input: optional DiagnosisResult;
 * - output: primary DiagnosedRootCause or undefined.
 * Compatibility rationale:
 * - no fallback to removed legacy fields; only array normalization is used.
 * Why:
 * - makes "primary root cause" semantics explicit and consistent in the UI.
 */
export function getPrimaryRootCause(result?: DiagnosisResult | null): DiagnosedRootCause | undefined {
  const normalized = getNormalizedRootCauses(result);
  return normalized[0];
}

function sanitizeRootCausePlanKeySegment(rootCauseId?: string | null): string {
  return String(rootCauseId ?? "")
    .trim()
    .replace(/[^a-zA-Z0-9._:-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Build backend-compatible remediation plan_key from root-cause identity.
 *
 * Purpose:
 * - keep frontend-generated plan_key aligned with backend key strategy.
 * Input/Output:
 * - input: root-cause index and optional root-cause id;
 * - output: stable plan_key string.
 */
export function buildRootCausePlanKey(index: number, rootCauseId?: string | null): string {
  const cleaned = sanitizeRootCausePlanKeySegment(rootCauseId);
  if (cleaned) {
    return `rc:${cleaned}`;
  }
  return `rc:${index + 1}`;
}

/**
 * Return the primary remediation plan_key when root-cause plan metadata is available.
 *
 * Purpose:
 * - provide explicit plan targeting for approve/revise API calls.
 * Input/Output:
 * - input: optional DiagnosisResult;
 * - output: plan_key or undefined when key cannot be safely resolved.
 */
export function getPrimaryPlanKey(result?: DiagnosisResult | null): string | undefined {
  if (!result || !Array.isArray(result.root_cause) || result.root_cause.length === 0) {
    return undefined;
  }
  const primary = result.root_cause[0];
  if (!primary?.recommended_fix) {
    return undefined;
  }
  return buildRootCausePlanKey(0, primary.id);
}

/**
 * Resolve the single remediation plan used in current first-root-cause-first execution flow.
 *
 * Purpose:
 * - keep plan extraction logic consistent between pages and store.
 * Input/Output:
 * - input: optional DiagnosisResult;
 * - output: remediation plan or undefined.
 * Compatibility rationale:
 * - only top-level and primary-root-cause plan are considered; no legacy candidate plan lookup.
 * Why:
 * - this migration intentionally does not introduce multi-plan approval/execution in the same release.
 */
export function getPrimaryPlan(result?: DiagnosisResult | null): RemediationPlan | undefined {
  if (!result) {
    return undefined;
  }
  if (result.recommended_fix) {
    return result.recommended_fix;
  }
  return getPrimaryRootCause(result)?.recommended_fix ?? undefined;
}
