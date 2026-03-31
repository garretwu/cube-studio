import { useEffect, useMemo, useState } from "react";

import { apiClient } from "../api/client";
import type { SkillDescriptor, ToolChannelStatus } from "../api/types";
import SkillCard from "../components/SkillCard";
import { SectionHeader, StatusChip, SurfaceCard } from "../components/ui";

function channelTone(health: ToolChannelStatus["health"]): "success" | "warning" | "danger" | "neutral" {
  if (health === "ready") {
    return "success";
  }
  if (health === "degraded") {
    return "warning";
  }
  if (health === "disabled") {
    return "neutral";
  }
  return "danger";
}

function channelLabel(health: ToolChannelStatus["health"]): string {
  if (health === "ready") {
    return "可用";
  }
  if (health === "degraded") {
    return "降级";
  }
  if (health === "disabled") {
    return "未启用";
  }
  return "不可用";
}

function SkillsPage() {
  const [skills, setSkills] = useState<SkillDescriptor[]>([]);
  const [channels, setChannels] = useState<ToolChannelStatus[]>([]);
  const [runtimeMode, setRuntimeMode] = useState<"strict" | "degraded">("degraded");
  const [error, setError] = useState("");
  const [channelError, setChannelError] = useState("");

  useEffect(() => {
    void (async () => {
      setError("");
      try {
        setSkills(await apiClient.getSkills());
      } catch (err) {
        const message = err instanceof Error ? err.message : "unknown request failure";
        setError(`技能接口不可用：${message}`);
        setSkills([]);
      }
    })();
  }, []);

  useEffect(() => {
    void (async () => {
      setChannelError("");
      try {
        const payload = await apiClient.getToolChannelsStatus();
        setChannels(payload.channels ?? []);
        setRuntimeMode(payload.runtime_mode ?? "degraded");
      } catch (err) {
        const message = err instanceof Error ? err.message : "unknown request failure";
        setChannelError(`通道状态接口不可用：${message}`);
        setChannels([]);
      }
    })();
  }, []);

  const unavailableCount = useMemo(
    () => channels.filter((item) => item.health === "unavailable" || item.health === "disabled").length,
    [channels],
  );

  return (
    <div className="page-grid">
      <div className="page-intro">
        <SectionHeader
          description="查看当前运行时可调用的技能，以及每个工具通道的就绪状态。"
          eyebrow="能力注册"
          title="技能与通道状态"
        />
      </div>

      <SurfaceCard title="通道就绪矩阵" description="用于定位工具失败是“通道问题”还是“工具逻辑问题”。">
        <div className="status-row">
          <StatusChip tone={runtimeMode === "strict" ? "warning" : "neutral"}>
            运行模式：{runtimeMode === "strict" ? "严格" : "降级"}
          </StatusChip>
          <StatusChip tone={unavailableCount > 0 ? "warning" : "success"}>异常通道：{unavailableCount}</StatusChip>
          <StatusChip tone="neutral">通道总数：{channels.length}</StatusChip>
        </div>
        {channelError ? (
          <div className="mini-card">
            <p className="mini-card__copy">{channelError}</p>
          </div>
        ) : null}
        {!channelError && channels.length === 0 ? (
          <div className="mini-card">
            <p className="mini-card__copy">当前未返回通道状态。</p>
          </div>
        ) : null}
        <div className="mini-card-list">
          {channels.map((item) => (
            <div key={item.name} className="mini-card">
              <div className="status-row">
                <StatusChip tone={channelTone(item.health)}>{channelLabel(item.health)}</StatusChip>
                <StatusChip tone="neutral">{item.mode}</StatusChip>
              </div>
              <p className="mini-card__title">{item.name}</p>
              {item.required_by_tools.length > 0 ? (
                <p className="mini-card__copy">影响工具：{item.required_by_tools.slice(0, 3).join("、")}</p>
              ) : (
                <p className="mini-card__copy">影响工具：无</p>
              )}
              {item.last_error ? <p className="mini-card__copy">原因：{item.last_error}</p> : null}
            </div>
          ))}
        </div>
      </SurfaceCard>

      {error ? (
        <SurfaceCard title="请求失败" description="未能从后端加载技能列表。">
          <div className="mini-card">
            <p className="mini-card__copy">{error}</p>
          </div>
        </SurfaceCard>
      ) : null}

      {!error && skills.length === 0 ? (
        <SurfaceCard title="暂无技能" description="后端返回成功，但当前技能目录为空。">
          <div className="mini-card">
            <p className="mini-card__copy">当前运行时未注册任何技能。</p>
          </div>
        </SurfaceCard>
      ) : null}

      <div className="card-grid--two">{skills.map((skill) => <SkillCard key={skill.id} skill={skill} />)}</div>
    </div>
  );
}

export default SkillsPage;
