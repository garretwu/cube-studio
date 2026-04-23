import type { Alert } from "../api/types";

export function extractAlertEventId(alert: Alert): string | null {
  const labelValue = String(alert.labels.event_id ?? "").trim();
  if (labelValue) {
    return labelValue;
  }

  const annotationValue = String(alert.annotations.event_id ?? "").trim();
  if (annotationValue) {
    return annotationValue;
  }

  return null;
}

export function normalizeAlertStartsAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toISOString().replace(".000Z", "Z");
}

export function buildAlertIncidentKey(alert: Alert): string {
  const startsAt = normalizeAlertStartsAt(alert.starts_at);
  return `fpst:${alert.fingerprint}|${startsAt}`;
}
