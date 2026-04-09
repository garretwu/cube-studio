import { create } from "zustand";

import { apiClient } from "../api/client";
import type {
  ChatMessage,
  DiagnosisLocalAuditRecord,
  DiagnosisSession,
  Observation,
  SessionEvent,
  ThinkingStep,
  WSEvent,
} from "../api/types";
import { formatDateTime } from "../utils/format";

type ConnectionState = "connecting" | "open" | "closed" | "error";
type BootstrapStatus = "idle" | "loading" | "ready" | "empty" | "error";
type TraceStatus = "unknown" | "empty" | "ready";

export type ApprovalDecisionInput = {
  approved: boolean;
  reason?: string;
  user?: string;
};

type DiagnosisState = {
  session?: DiagnosisSession;
  activeSessionId?: string;
  messages: ChatMessage[];
  events: SessionEvent[];
  localAuditRecords: DiagnosisLocalAuditRecord[];
  chatContextApplied: boolean;
  chatContextMeta?: Record<string, unknown>;
  isLoadingSession: boolean;
  bootstrapStatus: BootstrapStatus;
  traceStatus: TraceStatus;
  isSendingMessage: boolean;
  connectionState: ConnectionState;
  error?: string;
  isRevisingPlan: boolean;
  isApprovingPlan: boolean;
  approvalOverlayOpen: boolean;
  currentPlanVersion: number | null;
  latestPlanVersion: number | null;
  approvedPlanVersion: number | null;
  canApprove: boolean;
  approvalBlockReason?: string;
  hasPlan: boolean;
  planMissingReason?: string;
  effectiveReviseInstruction?: string;
  bootstrapSession: (sessionId?: string) => Promise<void>;
  sendMessage: (content: string) => Promise<ChatMessage | undefined>;
  revisePlan: (instruction: string) => Promise<void>;
  approvePlan: (input: ApprovalDecisionInput) => Promise<void>;
  setApprovalOverlayOpen: (value: boolean) => void;
  setConnectionState: (value: ConnectionState) => void;
  applyEvent: (event: WSEvent) => void;
};

const DEFAULT_REVISE_INSTRUCTION = "请优化当前修复方案，补充更稳妥步骤与验证";
const DEFAULT_APPROVER = "alice";
const LOCAL_AUDIT_STORAGE_KEY = "sre_diagnosis_local_audit_v1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toThinkingStep(event: WSEvent, fallbackStep: number): ThinkingStep | null {
  if (event.type !== "thinking_step") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const actionType = data.action_type;

  return {
    step: typeof data.step === "number" ? data.step : fallbackStep,
    timestamp: event.timestamp,
    thought:
      typeof data.thought === "string" && data.thought.trim()
        ? data.thought
        : "The diagnosis engine is expanding the current reasoning context.",
    action_type:
      actionType === "tool_call" || actionType === "remediate" || actionType === "conclude"
        ? actionType
        : "conclude",
    tool_name: typeof data.tool_name === "string" ? data.tool_name : null,
    tool_params: isRecord(data.tool_params) ? data.tool_params : null,
    confidence: typeof data.confidence === "number" ? data.confidence : null,
  };
}

function toObservation(event: WSEvent): Observation | null {
  if (event.type !== "tool_result") {
    return null;
  }

  const data = isRecord(event.data) ? event.data : {};
  const result = isRecord(data.result) ? data.result : data;

  return {
    tool:
      typeof data.tool_name === "string"
        ? data.tool_name
        : typeof data.tool === "string"
          ? data.tool
          : "tool_result",
    params: isRecord(data.params) ? data.params : {},
    result,
    timestamp: event.timestamp,
  };
}

function appendTraceEntries(
  session: DiagnosisSession | undefined,
  entries: Array<ThinkingStep | Observation>,
): DiagnosisSession | undefined {
  if (!session || entries.length === 0) {
    return session;
  }

  return {
    ...session,
    trace: {
      steps: [...(session.trace?.steps ?? []), ...entries],
    },
  };
}

function getEventError(event: WSEvent) {
  if (event.type !== "error") {
    return undefined;
  }

  const data = isRecord(event.data) ? event.data : {};
  if (typeof data.message === "string" && data.message.trim()) {
    return data.message;
  }
  if (typeof data.error === "string" && data.error.trim()) {
    return data.error;
  }
  return "The diagnosis engine returned an error event.";
}

function parsePlanVersionFromPlanId(planId: string | undefined): number | null {
  if (!planId) {
    return null;
  }
  const matched = /-v(\d+)$/.exec(planId.trim());
  if (!matched) {
    return null;
  }
  const version = Number(matched[1]);
  return Number.isFinite(version) && version > 0 ? version : null;
}

function normalizePlanVersion(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function readLocalAuditStore(): Record<string, DiagnosisLocalAuditRecord[]> {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(LOCAL_AUDIT_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed)) {
      return {};
    }

    return Object.fromEntries(
      Object.entries(parsed).map(([sessionId, records]) => {
        const safeRecords = Array.isArray(records)
          ? records.filter((record): record is DiagnosisLocalAuditRecord => {
              return (
                isRecord(record) &&
                typeof record.id === "string" &&
                typeof record.sessionId === "string" &&
                typeof record.eventKind === "string" &&
                typeof record.source === "string" &&
                typeof record.dedupeKey === "string" &&
                typeof record.timestamp === "string" &&
                typeof record.summary === "string" &&
                Array.isArray(record.details) &&
                typeof record.statusTone === "string"
              );
            })
          : [];
        return [sessionId, safeRecords];
      }),
    );
  } catch {
    return {};
  }
}

function writeLocalAuditStore(store: Record<string, DiagnosisLocalAuditRecord[]>) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(LOCAL_AUDIT_STORAGE_KEY, JSON.stringify(store));
}

function loadLocalAuditRecords(sessionId?: string): DiagnosisLocalAuditRecord[] {
  if (!sessionId) {
    return [];
  }
  return readLocalAuditStore()[sessionId] ?? [];
}

function persistLocalAuditRecords(sessionId: string, records: DiagnosisLocalAuditRecord[]) {
  const store = readLocalAuditStore();
  if (records.length === 0) {
    delete store[sessionId];
  } else {
    store[sessionId] = records;
  }
  writeLocalAuditStore(store);
}

function upsertLocalAuditRecord(
  records: DiagnosisLocalAuditRecord[],
  nextRecord: DiagnosisLocalAuditRecord,
): DiagnosisLocalAuditRecord[] {
  const next = [...records];
  const matchedIndex = next.findIndex((record) => record.dedupeKey === nextRecord.dedupeKey);
  if (matchedIndex >= 0) {
    next[matchedIndex] = nextRecord;
  } else {
    next.push(nextRecord);
  }

  return next.sort((left, right) => {
    return new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
  });
}

function resolveDisplayUser(user?: string) {
  const candidate = user?.trim();
  return candidate || DEFAULT_APPROVER;
}

function buildPlanDetailLines(
  session: DiagnosisSession | undefined,
  planVersion: number | null,
  timestamp: string,
) {
  const plan = session?.diagnosis_result?.recommended_fix;
  const lines = [
    `审批时间：${formatDateTime(timestamp)}`,
    `方案版本：${planVersion ? `v${planVersion}` : "--"}`,
    `方案 ID：${plan?.plan_id ?? "--"}`,
    `方案标题：${plan?.root_cause ?? "--"}`,
    `方案说明：${plan?.description ?? "--"}`,
  ];

  const steps = plan?.steps ?? [];
  if (steps.length === 0) {
    lines.push("执行步骤：--");
  } else {
    steps.forEach((step, index) => {
      lines.push(`步骤 ${index + 1}：${step.description}`);
    });
  }

  return lines;
}

function buildApprovalAuditRecord(
  sessionId: string,
  session: DiagnosisSession | undefined,
  input: ApprovalDecisionInput,
  planVersion: number | null,
): DiagnosisLocalAuditRecord {
  const approver = resolveDisplayUser(input.user);
  const approved = input.approved;
  const reason = input.reason?.trim();
  const timestamp = new Date().toISOString();
  const versionLabel = planVersion ? `v${planVersion}` : "v?";
  const details = buildPlanDetailLines(session, planVersion, timestamp);

  details.splice(3, 0, `审批人：${approver}`);
  details.splice(4, 0, `审批动作：${approved ? "同意，通过执行" : "拒绝执行"}`);
  if (!approved && reason) {
    details.splice(5, 0, `拒绝原因：${reason}`);
  }

  return {
    id: `local-audit-${approved ? "approved" : "rejected"}-${Date.now()}`,
    sessionId,
    eventKind: "approval_result",
    source: approved ? "optimistic" : "local_audit",
    dedupeKey: `approval-result-${approved ? "approved" : "rejected"}-${versionLabel}`,
    timestamp,
    summary: approved
      ? `[系统] 已审批，通过执行（${versionLabel}，审批人 ${approver}）`
      : `[系统] 已审批，拒绝执行（原因：${reason || "--"}）`,
    details,
    statusTone: approved ? "success" : "danger",
  };
}

function shouldOpenApprovalOverlay(
  session: DiagnosisSession | undefined,
  approvalState: Pick<DiagnosisState, "hasPlan">,
) {
  return session?.status === "approval_required" && approvalState.hasPlan;
}

function deriveApprovalState(
  session: DiagnosisSession | undefined,
  events: SessionEvent[],
): Pick<
  DiagnosisState,
  | "currentPlanVersion"
  | "latestPlanVersion"
  | "approvedPlanVersion"
  | "canApprove"
  | "approvalBlockReason"
  | "hasPlan"
  | "planMissingReason"
> {
  const plan = session?.diagnosis_result?.recommended_fix;
  const hasPlan = Boolean(plan && plan.steps);
  const currentPlanVersion = parsePlanVersionFromPlanId(plan?.plan_id) ?? 1;
  let latestPlanVersion = currentPlanVersion;
  let approvedPlanVersion: number | null = null;

  events.forEach((event) => {
    const data = isRecord(event.data) ? event.data : {};
    const explicitVersion = normalizePlanVersion(data.plan_version);
    if (event.type === "plan_revised" || event.type === "approval_required") {
      if (explicitVersion && explicitVersion > latestPlanVersion) {
        latestPlanVersion = explicitVersion;
      }
      return;
    }
    if (event.type !== "remediation_progress") {
      return;
    }
    const stage = String(data.stage ?? "").trim().toLowerCase();
    if (stage === "execution_started" && explicitVersion) {
      approvedPlanVersion = explicitVersion;
      if (explicitVersion > latestPlanVersion) {
        latestPlanVersion = explicitVersion;
      }
    }
  });

  const canApprove = session?.status === "approval_required" && currentPlanVersion === latestPlanVersion;
  const approvalBlockReason = canApprove
    ? undefined
    : session?.status !== "approval_required"
      ? `当前状态为 ${session?.status ?? "unknown"}，暂不可审批。`
      : "仅最新版本可审批，请先刷新或重新生成最新方案。";
  const planMissingReason = hasPlan
    ? undefined
    : "当前会话尚未产出修复计划，请先完成诊断或切换会话。";

  return {
    currentPlanVersion,
    latestPlanVersion,
    approvedPlanVersion,
    canApprove,
    approvalBlockReason,
    hasPlan,
    planMissingReason,
  };
}

export const useDiagnosisStore = create<DiagnosisState>((set, get) => ({
  session: undefined,
  activeSessionId: undefined,
  messages: [],
  events: [],
  localAuditRecords: [],
  chatContextApplied: false,
  chatContextMeta: undefined,
  isLoadingSession: false,
  bootstrapStatus: "idle",
  traceStatus: "unknown",
  isSendingMessage: false,
  isRevisingPlan: false,
  isApprovingPlan: false,
  approvalOverlayOpen: false,
  currentPlanVersion: null,
  latestPlanVersion: null,
  approvedPlanVersion: null,
  canApprove: false,
  approvalBlockReason: undefined,
  hasPlan: false,
  planMissingReason: undefined,
  effectiveReviseInstruction: undefined,
  connectionState: "closed",
  error: undefined,
  bootstrapSession: async (sessionId) => {
    const explicitSessionId = sessionId?.trim();

    set({
      activeSessionId: explicitSessionId ?? get().activeSessionId,
      error: undefined,
      isLoadingSession: true,
      bootstrapStatus: "loading",
      traceStatus: "unknown",
      isSendingMessage: false,
      isRevisingPlan: false,
      isApprovingPlan: false,
      approvalOverlayOpen: false,
      messages: [],
      events: [],
      localAuditRecords: [],
      currentPlanVersion: null,
      latestPlanVersion: null,
      approvedPlanVersion: null,
      canApprove: false,
      approvalBlockReason: undefined,
      hasPlan: false,
      planMissingReason: undefined,
      effectiveReviseInstruction: undefined,
      chatContextApplied: false,
      chatContextMeta: undefined,
    });

    try {
      let session: DiagnosisSession | null = null;
      let resolvedSessionId = explicitSessionId;

      if (!resolvedSessionId) {
        session = await apiClient.getDiagnosisSession();
        resolvedSessionId = session?.session_id;
      } else {
        for (let attempt = 0; attempt < 10; attempt += 1) {
          try {
            const currentSession = await apiClient.getDiagnosisSession(resolvedSessionId);
            if (currentSession && currentSession.session_id === resolvedSessionId) {
              session = currentSession;
              break;
            }
          } catch (error) {
            if (attempt === 9) {
              throw error;
            }
          }
          await new Promise((resolve) => {
            globalThis.setTimeout(resolve, 1200);
          });
        }
      }

      if (!session || !resolvedSessionId) {
        set({
          session: undefined,
          activeSessionId: undefined,
          messages: [],
          events: [],
          localAuditRecords: [],
          currentPlanVersion: null,
          latestPlanVersion: null,
          approvedPlanVersion: null,
          canApprove: false,
          approvalBlockReason: undefined,
          hasPlan: false,
          planMissingReason: undefined,
          effectiveReviseInstruction: undefined,
          isLoadingSession: false,
          bootstrapStatus: "empty",
          traceStatus: "unknown",
          isSendingMessage: false,
          isRevisingPlan: false,
          isApprovingPlan: false,
          approvalOverlayOpen: false,
          chatContextApplied: false,
          chatContextMeta: undefined,
          error: undefined,
        });
        return;
      }

      const messages = await apiClient.getChatHistory(resolvedSessionId);
      const events = await apiClient.getSessionEvents(resolvedSessionId).catch(() => []);
      const traceStatus: TraceStatus = (session.trace?.steps ?? []).length > 0 ? "ready" : "empty";
      const approvalState = deriveApprovalState(session, events);
      const localAuditRecords = loadLocalAuditRecords(resolvedSessionId);

      set({
        session,
        activeSessionId: resolvedSessionId,
        messages,
        events,
        localAuditRecords,
        isLoadingSession: false,
        bootstrapStatus: "ready",
        traceStatus,
        isSendingMessage: false,
        isRevisingPlan: false,
        isApprovingPlan: false,
        approvalOverlayOpen: shouldOpenApprovalOverlay(session, approvalState),
        ...approvalState,
        effectiveReviseInstruction: undefined,
        chatContextApplied: false,
        chatContextMeta: undefined,
        error: undefined,
      });
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "加载会话失败",
        isLoadingSession: false,
        bootstrapStatus: "error",
        traceStatus: "unknown",
        session: undefined,
        activeSessionId: explicitSessionId,
        messages: [],
        events: [],
        localAuditRecords: [],
        currentPlanVersion: null,
        latestPlanVersion: null,
        approvedPlanVersion: null,
        canApprove: false,
        approvalBlockReason: undefined,
        hasPlan: false,
        planMissingReason: undefined,
        effectiveReviseInstruction: undefined,
        approvalOverlayOpen: false,
        chatContextApplied: false,
        chatContextMeta: undefined,
      });
    }
  },
  sendMessage: async (content: string) => {
    const message = content.trim();
    const sessionId = get().activeSessionId;

    if (!message || !sessionId) {
      return undefined;
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
      metadata: {
        session_id: sessionId,
      },
    };

    set((state) => ({
      messages: [...state.messages, userMessage],
      isSendingMessage: true,
      error: undefined,
    }));

    try {
      const reply = await apiClient.postChatMessage(sessionId, message);
      const chatMeta = (reply.metadata?.["chat_meta"] ?? null) as Record<string, unknown> | null;
      const contextApplied = chatMeta?.context_applied === true;
      set((state) => ({
        messages: [...state.messages, reply],
        isSendingMessage: false,
        chatContextApplied: contextApplied,
        chatContextMeta: chatMeta ?? undefined,
      }));
      return reply;
    } catch (error) {
      set({
        error: error instanceof Error ? error.message : "消息发送失败",
        isSendingMessage: false,
      });
      throw error;
    }
  },
  revisePlan: async (instruction: string) => {
    const sessionId = get().activeSessionId;
    const basePlanVersion = get().latestPlanVersion ?? undefined;
    const text = instruction.trim();
    const effectiveInstruction = text || DEFAULT_REVISE_INSTRUCTION;
    if (!sessionId) {
      return;
    }

    set({
      isRevisingPlan: true,
      error: undefined,
      effectiveReviseInstruction: effectiveInstruction,
    });

    try {
      const payload = await apiClient.reviseRemediationPlan(sessionId, effectiveInstruction, basePlanVersion);
      const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
      const approvalState = deriveApprovalState(payload.session, events);
      set((state) => ({
        session: payload.session,
        events,
        localAuditRecords: state.localAuditRecords,
        ...approvalState,
        approvalOverlayOpen: shouldOpenApprovalOverlay(payload.session, approvalState),
        effectiveReviseInstruction: effectiveInstruction,
        isRevisingPlan: false,
      }));
    } catch (error) {
      set({
        isRevisingPlan: false,
        error: error instanceof Error ? error.message : "修复计划修改失败",
      });
      throw error;
    }
  },
  approvePlan: async (input: ApprovalDecisionInput) => {
    const sessionId = get().activeSessionId;
    const planVersion = get().latestPlanVersion ?? undefined;
    const reason = input.reason?.trim();
    const user = resolveDisplayUser(input.user);

    if (!sessionId) {
      return;
    }

    if (!input.approved && !reason) {
      set({ error: "拒绝执行时请填写原因。" });
      return;
    }

    set({
      isApprovingPlan: true,
      error: undefined,
      approvalOverlayOpen: false,
    });

    try {
      await apiClient.approveRemediation(sessionId, input.approved, user, planVersion);
    } catch (error) {
      set({
        isApprovingPlan: false,
        error: error instanceof Error ? error.message : "审批操作失败",
        approvalOverlayOpen: true,
      });
      throw error;
    }

    const approvalRecord = buildApprovalAuditRecord(
      sessionId,
      get().session,
      { approved: input.approved, reason, user },
      planVersion ?? null,
    );
    const nextLocalAuditRecords = upsertLocalAuditRecord(get().localAuditRecords, approvalRecord);
    persistLocalAuditRecords(sessionId, nextLocalAuditRecords);

    const session = await apiClient.getDiagnosisSession(sessionId).catch(() => get().session ?? null);
    const events = await apiClient.getSessionEvents(sessionId).catch(() => []);
    const approvalState = deriveApprovalState(session ?? undefined, events);

    set({
      session: session ?? undefined,
      events,
      localAuditRecords: nextLocalAuditRecords,
      ...approvalState,
      isApprovingPlan: false,
      approvalOverlayOpen: false,
    });
  },
  setApprovalOverlayOpen: (approvalOverlayOpen) => set({ approvalOverlayOpen }),
  setConnectionState: (connectionState) => set({ connectionState }),
  applyEvent: (event) =>
    set((state) => {
      const activeSessionId = state.activeSessionId ?? state.session?.session_id;
      if (activeSessionId && event.session_id !== activeSessionId) {
        return state;
      }

      const currentTrace = state.session?.trace?.steps ?? [];
      const nextEntries = [
        toThinkingStep(event, currentTrace.length + 1),
        toObservation(event),
      ].filter((entry): entry is ThinkingStep | Observation => Boolean(entry));

      let nextSession = appendTraceEntries(state.session, nextEntries);

      if (event.type === "diagnosis_result" && nextSession) {
        nextSession = {
          ...nextSession,
          diagnosis_result: event.data as DiagnosisSession["diagnosis_result"],
        };
      }
      if (event.type === "approval_required" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "approval_required",
        };
      }
      if (event.type === "plan_revised" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "approval_required",
        };
      }
      if (event.type === "escalation_required" && nextSession) {
        nextSession = {
          ...nextSession,
          status: "escalated",
        };
      }
      if (event.type === "observation_result" && nextSession) {
        const data = isRecord(event.data) ? event.data : {};
        if (data.metrics_improved === true && data.alert_cleared === true) {
          nextSession = {
            ...nextSession,
            status: "resolved",
          };
        }
      }
      if (event.type === "remediation_progress" && nextSession) {
        const data = isRecord(event.data) ? event.data : {};
        const stage = String(data.stage ?? "").trim().toLowerCase();
        if (stage === "execution_started") {
          nextSession = {
            ...nextSession,
            status: "remediating",
          };
        }
        if (stage === "execution_succeeded") {
          nextSession = {
            ...nextSession,
            status: "resolved",
          };
        }
        if (stage === "execution_failed") {
          nextSession = {
            ...nextSession,
            status: "failed",
          };
        }
        if (stage === "execution_timeout") {
          nextSession = {
            ...nextSession,
            status: "timeout",
          };
        }
        if (stage === "escalation_required") {
          nextSession = {
            ...nextSession,
            status: "escalated",
          };
        }
      }

      const nextEvents = [...state.events, event];
      const approvalState = deriveApprovalState(nextSession, nextEvents);
      const approvalOverlayOpen =
        event.type === "approval_required" || event.type === "plan_revised"
          ? shouldOpenApprovalOverlay(nextSession, approvalState)
          : nextSession?.status === "approval_required"
            ? state.approvalOverlayOpen
            : false;

      return {
        session: nextSession,
        events: nextEvents,
        localAuditRecords: state.localAuditRecords,
        ...approvalState,
        approvalOverlayOpen,
        effectiveReviseInstruction: state.effectiveReviseInstruction,
        messages: state.messages,
        error: getEventError(event) ?? state.error,
        traceStatus:
          nextEntries.length > 0 || event.type === "diagnosis_result"
            ? "ready"
            : state.traceStatus,
      };
    }),
}));
