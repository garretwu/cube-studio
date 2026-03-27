import { useEffect, useState } from "react";
import { Tabs } from "antd";

import { apiClient } from "../api/client";
import type { ConfigBaseline, IncidentRecord, LearnedPattern } from "../api/types";
import { SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { formatIncidentOutcome, formatLayer } from "../utils/display";
import { formatPercent, formatTimestamp } from "../utils/format";

function MemoryPage() {
  const [incidents, setIncidents] = useState<IncidentRecord[]>([]);
  const [patterns, setPatterns] = useState<LearnedPattern[]>([]);
  const [baseline, setBaseline] = useState<ConfigBaseline | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      setError("");
      try {
        const [loadedIncidents, loadedPatterns, loadedBaseline] = await Promise.all([
          apiClient.getMemoryIncidents(),
          apiClient.getMemoryPatterns(),
          apiClient.getMemoryBaseline(),
        ]);
        setIncidents(loadedIncidents);
        setPatterns(loadedPatterns);
        setBaseline(loadedBaseline);
      } catch (err) {
        setError(`记忆接口暂不可用，已降级展示。${err instanceof Error ? ` (${err.message})` : ""}`);
        setIncidents([]);
        setPatterns([]);
        setBaseline(null);
      }
    })();
  }, []);

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="展示历史事件、学习到的模式与配置基线规则，让值班人员优先复用已经验证有效的经验。"
          eyebrow="持续上下文"
          title="记忆面板"
        />
      </div>

      <SurfaceCard description="已沉淀的历史事件记忆与先验经验。" title="已沉淀上下文">
        {error ? (
          <div className="mini-card">
            <p className="mini-card__copy">{error}</p>
          </div>
        ) : null}
        <Tabs
          className="app-tabs"
          items={[
            {
              key: "incidents",
              label: "事件",
              children: (
                <div className="mini-card-list">
                  {incidents.map((item) => (
                    <div key={item.incident_id} className="mini-card">
                      <div className="status-row">
                        <StatusChip tone="neutral">{formatIncidentOutcome(item.outcome)}</StatusChip>
                        <StatusChip tone="accent">{formatLayer(item.root_cause_layer)}</StatusChip>
                      </div>
                      <p className="mini-card__title">{item.root_cause}</p>
                      <p className="mini-card__copy">
                        {formatTimestamp(item.timestamp)} | {item.alert.alert_name} | {item.resolution_time_seconds}s
                      </p>
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "patterns",
              label: "模式",
              children: (
                <div className="mini-card-list">
                  {patterns.map((item) => (
                    <div key={item.pattern_id} className="mini-card">
                      <div className="status-row">
                        <StatusChip tone="success">{formatPercent(item.confidence)}</StatusChip>
                        <StatusChip tone="neutral">{item.occurrence_count} 次命中</StatusChip>
                      </div>
                      <p className="mini-card__title">{item.root_cause}</p>
                      <p className="mini-card__copy">{item.effective_fix}</p>
                      <p className="data-list__copy">{item.symptom_signature.join(", ")}</p>
                    </div>
                  ))}
                </div>
              ),
            },
            {
              key: "baseline",
              label: "配置基线",
              children: (
                <div className="page-stack">
                  <div className="status-row">
                    <StatusChip tone="accent">版本 {baseline?.version ?? "暂无"}</StatusChip>
                    <StatusChip tone="neutral">{baseline?.aidc_id ?? "本地 AIDC"}</StatusChip>
                  </div>
                  <pre className="baseline-block">{JSON.stringify(baseline?.metric_baselines ?? {}, null, 2)}</pre>
                  <pre className="baseline-block">{JSON.stringify(baseline?.safety_thresholds ?? {}, null, 2)}</pre>
                </div>
              ),
            },
          ]}
        />
      </SurfaceCard>
    </div>
  );
}

export default MemoryPage;
