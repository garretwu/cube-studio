import type { ReactNode } from "react";

export type AppIconVariant = "outline" | "fill";

type IconRenderContext = {
  strokeWidth: number;
};

type IconPlate = "square" | "circle" | "document";

type IconSpec = {
  outline: (context: IconRenderContext) => ReactNode;
  fill?: (context: IconRenderContext) => ReactNode;
  plate?: IconPlate;
  keywords?: readonly string[];
};

const CANONICAL_ICON_NAMES = [
  "logo",
  "grid",
  "home",
  "homeAlt",
  "command",
  "boardTasks",
  "folderFile",
  "users3",
  "chart",
  "document",
  "send",
  "timeSquare",
  "successDocuments",
  "receiptBill",
  "moneyBag",
  "dollarBadge",
  "wallet",
  "resizeVertical",
  "down",
  "right",
  "up",
  "left",
  "plus",
  "infoMenu",
  "search",
  "play",
  "notification",
  "settings",
  "arrowRight",
  "arrowLeft",
  "clipboardTasks",
  "notebookCheck",
  "sortsComplete",
  "documentCheck",
  "documentStar",
  "timeCircle",
  "calendar",
  "edit",
  "pencilEdit",
  "documentZip",
  "aiChat",
  "aiFolder",
  "algorithm",
  "rocket",
  "helpSquare",
  "shoppingBag",
  "inbox",
  "layers",
  "infoCircle",
  "star",
  "university",
  "energy",
  "creditCardAdd",
  "creditCardFreeze",
  "smartPhone",
  "checkmarkCircle",
  "cancelCircle",
  "upload",
  "refresh",
] as const;

export type CanonicalAppIconName = (typeof CANONICAL_ICON_NAMES)[number];

export type LegacyAppIconName =
  | "topology"
  | "alerts"
  | "diagnosis"
  | "remediation"
  | "chat"
  | "knowledge"
  | "memory"
  | "skills"
  | "chevronDown"
  | "chevronRight"
  | "spark"
  | "help";

export type AppIconName = CanonicalAppIconName | LegacyAppIconName;

type AppIconAliasMap = Record<LegacyAppIconName, CanonicalAppIconName>;

const SOFT_FILL_OPACITY = 0.16;

function outlineGroup(children: ReactNode, strokeWidth: number) {
  return (
    <g
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
    >
      {children}
    </g>
  );
}

function renderPlate(plate: IconPlate) {
  switch (plate) {
    case "circle":
      return <circle cx="12" cy="12" r="8" fill="currentColor" opacity={SOFT_FILL_OPACITY} />;
    case "document":
      return (
        <path
          d="M8 3.5h6.5l3 3V18a2 2 0 0 1-2 2H8A2.5 2.5 0 0 1 5.5 17.5V6A2.5 2.5 0 0 1 8 3.5Z"
          fill="currentColor"
          opacity={SOFT_FILL_OPACITY}
        />
      );
    case "square":
    default:
      return <rect x="4" y="4" width="16" height="16" rx="4.5" fill="currentColor" opacity={SOFT_FILL_OPACITY} />;
  }
}

function renderWithPlate(plate: IconPlate, outline: ReactNode) {
  return (
    <>
      {renderPlate(plate)}
      {outline}
    </>
  );
}

const iconRegistry: Record<CanonicalAppIconName, IconSpec> = {
  logo: {
    plate: "square",
    keywords: ["brand", "mark"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M6.5 15.5v-7" />
          <path d="M10.5 18v-12" />
          <path d="M14.5 18v-12" />
          <path d="M18.5 15.5v-7" />
        </>,
        strokeWidth,
      ),
  },
  grid: {
    plate: "square",
    keywords: ["dashboard", "topology"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="5" y="5" width="5" height="5" rx="1.2" />
          <rect x="14" y="5" width="5" height="5" rx="1.2" />
          <rect x="5" y="14" width="5" height="5" rx="1.2" />
          <rect x="14" y="14" width="5" height="5" rx="1.2" />
        </>,
        strokeWidth,
      ),
    fill: () => (
      <>
        <rect x="5" y="5" width="5" height="5" rx="1.2" fill="currentColor" />
        <rect x="14" y="5" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.84" />
        <rect x="5" y="14" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.7" />
        <rect x="14" y="14" width="5" height="5" rx="1.2" fill="currentColor" opacity="0.92" />
      </>
    ),
  },
  home: {
    plate: "square",
    keywords: ["shell", "home"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M4.5 10.5 12 4.5l7.5 6" />
          <path d="M6.5 9.8v8.2h11V9.8" />
          <path d="M10 18v-4h4v4" />
        </>,
        strokeWidth,
      ),
  },
  homeAlt: {
    plate: "square",
    keywords: ["alternate-home"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M5 10.2 12 4.5l7 5.7" />
          <path d="M7 9.8v8.4h10V9.8" />
          <path d="M9 10.5h6" />
        </>,
        strokeWidth,
      ),
  },
  command: {
    plate: "square",
    keywords: ["skills", "automation"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="8" cy="8" r="2.5" />
          <circle cx="16" cy="8" r="2.5" />
          <circle cx="8" cy="16" r="2.5" />
          <circle cx="16" cy="16" r="2.5" />
        </>,
        strokeWidth,
      ),
  },
  boardTasks: {
    plate: "square",
    keywords: ["task-board", "remediation"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="4.5" y="5" width="15" height="14" rx="3" />
          <path d="M8 9h2" />
          <path d="M8 13h2" />
          <path d="M12.5 9h3.5" />
          <path d="M12.5 13h3.5" />
          <path d="m7.5 16 1 1 2-2" />
        </>,
        strokeWidth,
      ),
  },
  folderFile: {
    plate: "square",
    keywords: ["folder", "knowledge"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M4.5 8a2.5 2.5 0 0 1 2.5-2.5h3l1.5 1.5h5.5A2.5 2.5 0 0 1 19.5 9.5v7A2.5 2.5 0 0 1 17 19H7a2.5 2.5 0 0 1-2.5-2.5Z" />
          <path d="M12.5 10.5h3" />
          <path d="M12.5 13h3" />
          <path d="M12.5 15.5h2.2" />
        </>,
        strokeWidth,
      ),
  },
  users3: {
    plate: "square",
    keywords: ["team", "operators"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="10" r="2.4" />
          <path d="M7.5 17.2a4.7 4.7 0 0 1 9 0" />
          <path d="M6.5 12.1a2.1 2.1 0 1 0-1.4-3.7" />
          <path d="M17.5 12.1a2.1 2.1 0 1 1 1.4-3.7" />
        </>,
        strokeWidth,
      ),
  },
  chart: {
    plate: "square",
    keywords: ["diagnosis", "metrics"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M5 18h14" />
          <path d="M8 15v-3" />
          <path d="M12 15V8" />
          <path d="M16 15v-5" />
        </>,
        strokeWidth,
      ),
  },
  document: {
    plate: "document",
    keywords: ["doc", "knowledge"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 3.5h6.5l3 3V18a2 2 0 0 1-2 2H8A2.5 2.5 0 0 1 5.5 17.5V6A2.5 2.5 0 0 1 8 3.5Z" />
          <path d="M14.5 3.8V7h3.1" />
          <path d="M9 11h6" />
          <path d="M9 14h6" />
        </>,
        strokeWidth,
      ),
  },
  send: {
    plate: "circle",
    keywords: ["submit", "chat"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M4 11.5l15-7-4.5 15-2.5-5.5L4 11.5Z" />
          <path d="M11.9 14.1l7.1-9.6" />
        </>,
        strokeWidth,
      ),
  },
  timeSquare: {
    plate: "square",
    keywords: ["schedule", "memory"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="5" y="5" width="14" height="14" rx="3.5" />
          <path d="M12 8.5v3.5l2.2 1.3" />
        </>,
        strokeWidth,
      ),
  },
  successDocuments: {
    plate: "document",
    keywords: ["checked-document", "done"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 4.5h7l3 3V18a2 2 0 0 1-2 2H8a2.5 2.5 0 0 1-2.5-2.5V7A2.5 2.5 0 0 1 8 4.5Z" />
          <path d="M14.5 4.8V8h3" />
          <path d="m9 14 1.6 1.6 3.4-3.6" />
        </>,
        strokeWidth,
      ),
  },
  receiptBill: {
    plate: "document",
    keywords: ["invoice", "bill"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 4.5h8l2 2.2V18a2 2 0 0 1-2 2H8a2.5 2.5 0 0 1-2.5-2.5V7A2.5 2.5 0 0 1 8 4.5Z" />
          <path d="M9 10h6" />
          <path d="M9 13h6" />
          <path d="M9 16h4" />
        </>,
        strokeWidth,
      ),
  },
  moneyBag: {
    plate: "square",
    keywords: ["budget", "finance"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M10 5.5h4l-1 2h-2Z" />
          <path d="M8.2 10.2a4.8 4.8 0 1 1 7.6 0c1.1 1 1.7 2.4 1.7 3.9a5.5 5.5 0 1 1-11 0c0-1.5.6-2.9 1.7-3.9Z" />
          <path d="M12 10.7v5" />
          <path d="M10.3 12.2h2a1.2 1.2 0 0 1 0 2.4h-1.1a1.2 1.2 0 1 0 0 2.4h2.4" />
        </>,
        strokeWidth,
      ),
  },
  dollarBadge: {
    plate: "circle",
    keywords: ["badge", "payment"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="11" r="4.5" />
          <path d="M12 8.5v5" />
          <path d="M10.5 10h1.8a1.1 1.1 0 1 1 0 2.2h-1a1.1 1.1 0 1 0 0 2.2h2.2" />
          <path d="M10 15.3 8.7 18l3.3-1.1L15.3 18 14 15.3" />
        </>,
        strokeWidth,
      ),
  },
  wallet: {
    plate: "square",
    keywords: ["payment", "wallet"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M6 8.5h12.5A1.5 1.5 0 0 1 20 10v6.5A1.5 1.5 0 0 1 18.5 18H6.5A2.5 2.5 0 0 1 4 15.5v-7A2.5 2.5 0 0 1 6.5 6H17" />
          <path d="M15.5 13.2h4.2" />
          <circle cx="15.5" cy="13.2" r=".6" fill="currentColor" stroke="none" />
        </>,
        strokeWidth,
      ),
  },
  resizeVertical: {
    plate: "square",
    keywords: ["expand", "resize"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m12 4.5 3 3" />
          <path d="m12 4.5-3 3" />
          <path d="M12 4.5v15" />
          <path d="m12 19.5 3-3" />
          <path d="m12 19.5-3-3" />
        </>,
        strokeWidth,
      ),
  },
  down: {
    plate: "square",
    keywords: ["chevron-down"],
    outline: ({ strokeWidth }) => outlineGroup(<path d="m7 10 5 5 5-5" />, strokeWidth),
  },
  right: {
    plate: "square",
    keywords: ["chevron-right"],
    outline: ({ strokeWidth }) => outlineGroup(<path d="m10 7 5 5-5 5" />, strokeWidth),
  },
  up: {
    plate: "square",
    keywords: ["chevron-up"],
    outline: ({ strokeWidth }) => outlineGroup(<path d="m7 14 5-5 5 5" />, strokeWidth),
  },
  left: {
    plate: "square",
    keywords: ["chevron-left"],
    outline: ({ strokeWidth }) => outlineGroup(<path d="m14 7-5 5 5 5" />, strokeWidth),
  },
  plus: {
    plate: "square",
    keywords: ["add", "new"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M12 7v10" />
          <path d="M7 12h10" />
        </>,
        strokeWidth,
      ),
  },
  infoMenu: {
    plate: "square",
    keywords: ["menu", "more"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M6 12h.01" />
          <path d="M12 12h.01" />
          <path d="M18 12h.01" />
        </>,
        strokeWidth,
      ),
  },
  search: {
    plate: "circle",
    keywords: ["find"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="11" cy="11" r="5.5" />
          <path d="M16 16l4 4" />
        </>,
        strokeWidth,
      ),
  },
  play: {
    plate: "circle",
    keywords: ["start"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="m10.2 9.3 5.2 2.7-5.2 2.7Z" fill="currentColor" stroke="none" />
        </>,
        strokeWidth,
      ),
  },
  notification: {
    plate: "circle",
    keywords: ["alerts", "bell"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 18h8" />
          <path d="M10 20h4" />
          <path d="M6.5 15.5h11a1 1 0 0 0 .9-1.42L17 11.2V9.4a5 5 0 1 0-10 0v1.8l-1.4 2.88a1 1 0 0 0 .9 1.42Z" />
        </>,
        strokeWidth,
      ),
  },
  settings: {
    plate: "circle",
    keywords: ["preferences", "settings"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="2.3" />
          <path d="M12 5.5v1.4" />
          <path d="M12 17.1v1.4" />
          <path d="M18.5 12h-1.4" />
          <path d="M6.9 12H5.5" />
          <path d="m16.6 7.4-1 1" />
          <path d="m8.4 15.6-1 1" />
          <path d="m16.6 16.6-1-1" />
          <path d="m8.4 8.4-1-1" />
        </>,
        strokeWidth,
      ),
  },
  arrowRight: {
    plate: "square",
    keywords: ["forward"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M5 12h13" />
          <path d="m13 7 5 5-5 5" />
        </>,
        strokeWidth,
      ),
  },
  arrowLeft: {
    plate: "square",
    keywords: ["back"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M19 12H6" />
          <path d="m11 7-5 5 5 5" />
        </>,
        strokeWidth,
      ),
  },
  clipboardTasks: {
    plate: "square",
    keywords: ["tasks", "remediation"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="6" y="5.5" width="12" height="14" rx="2.5" />
          <path d="M9.5 5.5h5a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1h-5a1 1 0 0 0-1 1v.5a1 1 0 0 0 1 1Z" />
          <path d="m9 12 1.4 1.4L13 10.8" />
          <path d="M9 16h5" />
        </>,
        strokeWidth,
      ),
  },
  notebookCheck: {
    plate: "square",
    keywords: ["memory", "notebook"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M7 5.5h10a2 2 0 0 1 2 2v9.5a2 2 0 0 1-2 2H7.5A2.5 2.5 0 0 1 5 16.5V8a2.5 2.5 0 0 1 2-2.5Z" />
          <path d="M8 5.5V19" />
          <path d="m11 12 1.5 1.5 3-3" />
        </>,
        strokeWidth,
      ),
  },
  sortsComplete: {
    plate: "square",
    keywords: ["checklist", "complete"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m7.5 9.5 1 1 2-2" />
          <path d="M12 9.5h4.5" />
          <path d="m7.5 14.5 1 1 2-2" />
          <path d="M12 14.5h4.5" />
        </>,
        strokeWidth,
      ),
  },
  documentCheck: {
    plate: "document",
    keywords: ["diagnosis", "document-check"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 4h7l3 3v10.5A2.5 2.5 0 0 1 15.5 20h-7A2.5 2.5 0 0 1 6 17.5V6.5A2.5 2.5 0 0 1 8.5 4Z" />
          <path d="M15 4.3V8h3" />
          <path d="m9 14 1.5 1.5 3.5-3.5" />
        </>,
        strokeWidth,
      ),
  },
  documentStar: {
    plate: "document",
    keywords: ["document-star", "featured"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 4h7l3 3v10.5A2.5 2.5 0 0 1 15.5 20h-7A2.5 2.5 0 0 1 6 17.5V6.5A2.5 2.5 0 0 1 8.5 4Z" />
          <path d="M15 4.3V8h3" />
          <path d="m12 10.3.8 1.6 1.8.3-1.3 1.2.3 1.8-1.6-.8-1.6.8.3-1.8-1.3-1.2 1.8-.3Z" />
        </>,
        strokeWidth,
      ),
  },
  timeCircle: {
    plate: "circle",
    keywords: ["history", "memory"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="M12 8v4l2.7 1.7" />
        </>,
        strokeWidth,
      ),
  },
  calendar: {
    plate: "square",
    keywords: ["schedule"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="5" y="6.5" width="14" height="12.5" rx="2.5" />
          <path d="M8 4.5v4" />
          <path d="M16 4.5v4" />
          <path d="M5 10h14" />
        </>,
        strokeWidth,
      ),
  },
  edit: {
    plate: "square",
    keywords: ["edit", "pencil"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m8 16.5-1.2 2.8L9.6 18l7.2-7.2-1.6-1.6Z" />
          <path d="m13.6 6 1.6-1.6a1.8 1.8 0 0 1 2.6 2.6L16.2 8.6" />
          <path d="M7 19h10" />
        </>,
        strokeWidth,
      ),
  },
  pencilEdit: {
    plate: "square",
    keywords: ["edit", "pencil-edit"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m8 16.5-1.2 2.8L9.6 18l7.2-7.2-1.6-1.6Z" />
          <path d="m13.6 6 1.6-1.6a1.8 1.8 0 0 1 2.6 2.6L16.2 8.6" />
        </>,
        strokeWidth,
      ),
  },
  documentZip: {
    plate: "document",
    keywords: ["zip", "archive"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M8 4h7l3 3v10.5A2.5 2.5 0 0 1 15.5 20h-7A2.5 2.5 0 0 1 6 17.5V6.5A2.5 2.5 0 0 1 8.5 4Z" />
          <path d="M15 4.3V8h3" />
          <path d="M11.7 9.2h.1" />
          <path d="M11.7 11.7h.1" />
          <path d="M11.7 14.2h.1" />
          <path d="M11.7 16.7h.1" />
        </>,
        strokeWidth,
      ),
  },
  aiChat: {
    plate: "square",
    keywords: ["assistant", "chat"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M6 7.5h11a2.5 2.5 0 0 1 2.5 2.5v5A2.5 2.5 0 0 1 17 17.5h-5.8L8 20v-2.5H6A2.5 2.5 0 0 1 3.5 15v-5A2.5 2.5 0 0 1 6 7.5Z" />
          <path d="m12 5.3.8 1.6 1.8.3-1.3 1.2.3 1.8-1.6-.8-1.6.8.3-1.8-1.3-1.2 1.8-.3Z" />
        </>,
        strokeWidth,
      ),
  },
  aiFolder: {
    plate: "square",
    keywords: ["assistant", "folder"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M4.5 8a2.5 2.5 0 0 1 2.5-2.5h3l1.5 1.5h5.5A2.5 2.5 0 0 1 19.5 9.5v7A2.5 2.5 0 0 1 17 19H7a2.5 2.5 0 0 1-2.5-2.5Z" />
          <path d="m10.8 10.1.7 1.4 1.6.2-1.1 1 .2 1.5-1.4-.7-1.4.7.2-1.5-1.1-1 1.6-.2Z" />
        </>,
        strokeWidth,
      ),
  },
  algorithm: {
    plate: "square",
    keywords: ["automation", "skills"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="7" cy="8" r="2" />
          <circle cx="17" cy="8" r="2" />
          <circle cx="12" cy="16" r="2" />
          <path d="M8.7 9.2 10.8 14" />
          <path d="M15.3 9.2 13.2 14" />
          <path d="M9 8h6" />
        </>,
        strokeWidth,
      ),
  },
  rocket: {
    plate: "square",
    keywords: ["launch", "remediation"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M12.2 5.2c3.3 0 5.3 1.8 6.1 5.3-2.4.8-4.4 2.8-5.2 5.2-3.5-.8-5.3-2.8-5.3-6.1 0-2.8 1.8-4.4 4.4-4.4Z" />
          <path d="M9 15 6 18" />
          <path d="M8.5 18.5 5.5 15.5" />
          <circle cx="13.7" cy="8.8" r="1.2" />
        </>,
        strokeWidth,
      ),
  },
  helpSquare: {
    plate: "square",
    keywords: ["help", "support"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="5" y="5" width="14" height="14" rx="4" />
          <path d="M9.8 9.2a2.5 2.5 0 1 1 3.7 2.2c-.8.4-1.5 1-1.5 2" />
          <path d="M12 16.8h.01" />
        </>,
        strokeWidth,
      ),
  },
  shoppingBag: {
    plate: "square",
    keywords: ["bag", "commerce"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M7 8.5h10l-1 9A2 2 0 0 1 14 19H10a2 2 0 0 1-2-1.5Z" />
          <path d="M9 9V7.8a3 3 0 1 1 6 0V9" />
        </>,
        strokeWidth,
      ),
  },
  inbox: {
    plate: "square",
    keywords: ["inbox", "mail"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M5.5 8h13l1.5 7H15l-1.5 2h-3L9 15H4l1.5-7Z" />
          <path d="M9 12h6" />
        </>,
        strokeWidth,
      ),
  },
  layers: {
    plate: "square",
    keywords: ["topology", "stack"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m12 4.5 7 3.8-7 3.7-7-3.7Z" />
          <path d="m5 12 7 3.7 7-3.7" />
          <path d="m5 15.5 7 4 7-4" />
        </>,
        strokeWidth,
      ),
  },
  infoCircle: {
    plate: "circle",
    keywords: ["info"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="M12 10.2v4.3" />
          <path d="M12 7.8h.01" />
        </>,
        strokeWidth,
      ),
  },
  star: {
    plate: "circle",
    keywords: ["favorite", "spark"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <path d="m12 4.6 2.1 4.2 4.7.7-3.4 3.3.8 4.7-4.2-2.2-4.2 2.2.8-4.7-3.4-3.3 4.7-.7Z" />,
        strokeWidth,
      ),
  },
  university: {
    plate: "square",
    keywords: ["building", "campus"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="m4.5 9 7.5-4 7.5 4" />
          <path d="M6.5 10.5V17" />
          <path d="M10 10.5V17" />
          <path d="M14 10.5V17" />
          <path d="M17.5 10.5V17" />
          <path d="M4.5 18h15" />
        </>,
        strokeWidth,
      ),
  },
  energy: {
    plate: "circle",
    keywords: ["energy", "power"],
    outline: ({ strokeWidth }) =>
      outlineGroup(<path d="M13.5 4.8 7.8 12h3.5l-1 7.2 5.9-7.4h-3.4Z" />, strokeWidth),
  },
  creditCardAdd: {
    plate: "square",
    keywords: ["card", "add"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="4.5" y="7" width="15" height="10" rx="2.5" />
          <path d="M4.5 10h15" />
          <path d="M15.5 13.5h4" />
          <path d="M17.5 11.5v4" />
        </>,
        strokeWidth,
      ),
  },
  creditCardFreeze: {
    plate: "square",
    keywords: ["card", "freeze"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="4.5" y="7" width="15" height="10" rx="2.5" />
          <path d="M4.5 10h15" />
          <path d="M15.5 13.5h4" />
        </>,
        strokeWidth,
      ),
  },
  smartPhone: {
    plate: "square",
    keywords: ["mobile", "device"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <rect x="8" y="4.5" width="8" height="15" rx="2.5" />
          <path d="M10.5 7h3" />
          <path d="M12 16.5h.01" />
        </>,
        strokeWidth,
      ),
  },
  checkmarkCircle: {
    plate: "circle",
    keywords: ["success", "check"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="m9 12.2 2 2 4-4.2" />
        </>,
        strokeWidth,
      ),
  },
  cancelCircle: {
    plate: "circle",
    keywords: ["error", "cancel"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <circle cx="12" cy="12" r="8" />
          <path d="m9.5 9.5 5 5" />
          <path d="m14.5 9.5-5 5" />
        </>,
        strokeWidth,
      ),
  },
  upload: {
    plate: "square",
    keywords: ["upload"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M12 16V6" />
          <path d="m8 10 4-4 4 4" />
          <path d="M5 18.5h14" />
        </>,
        strokeWidth,
      ),
  },
  refresh: {
    plate: "circle",
    keywords: ["refresh", "reload"],
    outline: ({ strokeWidth }) =>
      outlineGroup(
        <>
          <path d="M20 7.5V4h-3.5" />
          <path d="M4 16.5V20h3.5" />
          <path d="M18.5 10A7 7 0 0 0 6.2 6.3L4 8.5" />
          <path d="M5.5 14A7 7 0 0 0 17.8 17.7l2.2-2.2" />
        </>,
        strokeWidth,
      ),
  },
};

export const appIconAliasMap: AppIconAliasMap = {
  topology: "layers",
  alerts: "notification",
  diagnosis: "chart",
  remediation: "clipboardTasks",
  chat: "aiChat",
  knowledge: "document",
  memory: "timeCircle",
  skills: "algorithm",
  chevronDown: "down",
  chevronRight: "right",
  spark: "star",
  help: "helpSquare",
};

const reverseAliasMap = CANONICAL_ICON_NAMES.reduce<Record<CanonicalAppIconName, LegacyAppIconName[]>>(
  (aliasesByIcon, iconName) => ({
    ...aliasesByIcon,
    [iconName]: Object.entries(appIconAliasMap)
      .filter(([, canonicalName]) => canonicalName === iconName)
      .map(([legacyName]) => legacyName as LegacyAppIconName),
  }),
  {} as Record<CanonicalAppIconName, LegacyAppIconName[]>,
);

export const appIconCatalog = CANONICAL_ICON_NAMES.map((name) => ({
  name,
  aliases: reverseAliasMap[name],
  keywords: [...(iconRegistry[name].keywords ?? [])],
}));

export function resolveAppIconName(name: AppIconName): CanonicalAppIconName | null {
  if ((iconRegistry as Partial<Record<string, IconSpec>>)[name]) {
    return name as CanonicalAppIconName;
  }

  return appIconAliasMap[name as LegacyAppIconName] ?? null;
}

export function renderAppIcon(
  name: CanonicalAppIconName,
  variant: AppIconVariant,
  context: IconRenderContext,
) {
  const spec = iconRegistry[name];

  if (!spec) {
    return null;
  }

  if (variant === "fill") {
    if (spec.fill) {
      return spec.fill(context);
    }

    if (spec.plate) {
      return renderWithPlate(spec.plate, spec.outline(context));
    }
  }

  return spec.outline(context);
}
