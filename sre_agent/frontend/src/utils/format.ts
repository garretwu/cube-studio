export function formatTimestamp(value?: string | null) {
  if (!value) {
    return "\u6682\u65e0";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDateTime(value?: string | null) {
  if (!value) {
    return "\u6682\u65e0";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDurationSeconds(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }

  const totalSeconds = Math.max(0, Math.floor(value));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}\u5c0f\u65f6 ${minutes}\u5206`;
  }

  if (minutes > 0) {
    return `${minutes}\u5206 ${seconds}\u79d2`;
  }

  return `${seconds}\u79d2`;
}

export function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}
