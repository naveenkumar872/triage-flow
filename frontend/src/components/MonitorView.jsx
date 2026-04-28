import React, { useState } from "react";
import { Activity, DollarSign, Zap, Clock, ChevronDown, ChevronRight, Eye } from "lucide-react";
import EmailPreviewModal from "./EmailPreviewModal";

// ── Colour per agent ──────────────────────────────────────────
const AGENT_COLORS = {
  ConfluenceSearchAgent:    "#6366f1",
  CustomerContextAgent:     "#ec4899",
  TriageClassificationAgent:"#f59e0b",
  AutoReplyAgent:           "#10b981",
  ReplyAgent:               "#3b82f6",
  default:                  "#8b5cf6",
};

function agentColor(name) {
  return AGENT_COLORS[name] || AGENT_COLORS.default;
}

// ── Small stat card ───────────────────────────────────────────
function MiniCard({ label, value, icon: Icon, color, bg, cardBg, border, textPrimary, textSecondary }) {
  return (
    <div style={{
      backgroundColor: cardBg, border: `1px solid ${border}`,
      borderRadius: "12px", padding: "16px 20px",
      display: "flex", alignItems: "center", gap: "14px", minWidth: "160px",
    }}>
      <div style={{ backgroundColor: bg, borderRadius: "10px", padding: "10px", flexShrink: 0 }}>
        <Icon size={18} color={color} strokeWidth={2} />
      </div>
      <div>
        <div style={{ fontSize: "11px", fontWeight: 600, color: textSecondary, letterSpacing: "0.4px", textTransform: "uppercase" }}>
          {label}
        </div>
        <div style={{ fontSize: "20px", fontWeight: 700, color: textPrimary, marginTop: "2px" }}>
          {value}
        </div>
      </div>
    </div>
  );
}

// ── Per-email expandable row ──────────────────────────────────
function EmailCostRow({ entry, isDark, t, emails }) {
  const [expanded, setExpanded] = useState(false);
  const [previewEmail, setPreviewEmail] = useState(null);

  const totalTokens = entry.llm_usage.reduce((s, u) => s + (u.total_tokens || 0), 0);
  const cost        = entry.total_cost_usd || 0;

  const fullEmail = (emails || []).find((e) => e.gmail_id === entry.gmail_id)
    || { subject: entry.subject, gmail_id: entry.gmail_id };

  return (
    <>
      {/* Summary row */}
      <tr
        onClick={() => setExpanded((v) => !v)}
        style={{
          cursor: "pointer",
          backgroundColor: expanded ? (isDark ? "#1a2c45" : "#f0f9ff") : "transparent",
          transition: "background 0.1s",
        }}
        onMouseEnter={(e) => { if (!expanded) e.currentTarget.style.backgroundColor = isDark ? "#1a2a3a" : "#f9fafb"; }}
        onMouseLeave={(e) => { if (!expanded) e.currentTarget.style.backgroundColor = "transparent"; }}
      >
        <td style={{ padding: "13px 16px 13px 20px", width: "28px" }}>
          {expanded
            ? <ChevronDown size={14} color={t.textSecondary} />
            : <ChevronRight size={14} color={t.textSecondary} />}
        </td>
        <td style={{ padding: "13px 20px", fontSize: "13.5px", fontWeight: 500, color: t.textPrimary, maxWidth: "320px" }}>
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            title={entry.subject}>
            {entry.subject || "(no subject)"}
          </div>
          <div style={{ fontSize: "12px", color: t.textSecondary, marginTop: "2px" }}>
            {entry.gmail_id}
          </div>
        </td>
        <td style={{ padding: "13px 20px", fontSize: "13px", color: t.textSecondary, whiteSpace: "nowrap" }}>
          {entry.llm_usage.length} call{entry.llm_usage.length !== 1 ? "s" : ""}
        </td>
        <td style={{ padding: "13px 20px", fontSize: "13px", color: t.textSecondary, whiteSpace: "nowrap" }}>
          {totalTokens.toLocaleString()}
        </td>
        <td style={{ padding: "13px 20px", whiteSpace: "nowrap" }}>
          <span style={{
            fontFamily: "monospace", fontSize: "13.5px", fontWeight: 700,
            color: cost > 0.001 ? "#f59e0b" : "#10b981",
          }}>
            ${cost.toFixed(6)}
          </span>
        </td>
        <td style={{ padding: "13px 20px", width: "48px" }}>
          <button
            onClick={(e) => { e.stopPropagation(); setPreviewEmail(fullEmail); }}
            title="Preview email"
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "28px", height: "28px", borderRadius: "6px",
              border: `1px solid ${t.borderColor}`,
              backgroundColor: "transparent",
              cursor: "pointer", color: t.textSecondary,
            }}
          >
            <Eye size={14} strokeWidth={2} />
          </button>
        </td>
      </tr>

      {/* Expanded per-agent breakdown */}
      {expanded && entry.llm_usage.map((u, i) => (
        <tr key={i} style={{ backgroundColor: isDark ? "#111e2e" : "#f8faff" }}>
          <td style={{ padding: "0" }} />
          <td colSpan={5} style={{ padding: "0 20px 0 36px" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: "12px",
              padding: "10px 0",
              borderBottom: i < entry.llm_usage.length - 1 ? `1px solid ${t.borderColor}` : "none",
            }}>
              {/* Agent badge */}
              <span style={{
                backgroundColor: agentColor(u.agent) + "22",
                color: agentColor(u.agent),
                border: `1px solid ${agentColor(u.agent)}55`,
                borderRadius: "6px", padding: "3px 10px",
                fontSize: "11.5px", fontWeight: 600, whiteSpace: "nowrap", flexShrink: 0,
              }}>
                {u.agent}
              </span>

              {/* Token breakdown */}
              <div style={{ display: "flex", gap: "16px", flex: 1, flexWrap: "wrap" }}>
                <span style={{ fontSize: "12.5px", color: t.textSecondary }}>
                  <span style={{ color: t.textMuted }}>in </span>
                  <span style={{ color: t.textPrimary, fontWeight: 600 }}>
                    {(u.prompt_tokens || 0).toLocaleString()}
                  </span>
                </span>
                <span style={{ fontSize: "12.5px", color: t.textSecondary }}>
                  <span style={{ color: t.textMuted }}>out </span>
                  <span style={{ color: t.textPrimary, fontWeight: 600 }}>
                    {(u.completion_tokens || 0).toLocaleString()}
                  </span>
                </span>
                <span style={{ fontSize: "12.5px", color: t.textSecondary }}>
                  <span style={{ color: t.textMuted }}>total </span>
                  <span style={{ color: t.textPrimary, fontWeight: 600 }}>
                    {(u.total_tokens || 0).toLocaleString()}
                  </span>
                </span>
              </div>

              {/* Duration */}
              <span style={{ fontSize: "12px", color: t.textMuted, whiteSpace: "nowrap" }}>
                {u.duration_ms ? `${Math.round(u.duration_ms)} ms` : "—"}
              </span>

              {/* Cost */}
              <span style={{ fontFamily: "monospace", fontSize: "12.5px", fontWeight: 600,
                color: agentColor(u.agent), whiteSpace: "nowrap" }}>
                ${(u.cost_usd || 0).toFixed(6)}
              </span>
            </div>
          </td>
        </tr>
      ))}
      {previewEmail && (
        <EmailPreviewModal
          email={previewEmail}
          onClose={() => setPreviewEmail(null)}
          isDark={isDark}
        />
      )}
    </>
  );
}


// ── Main MonitorView ──────────────────────────────────────────
export default function MonitorView({ costLogs, isDark, emails }) {
  const t = isDark ? {
    pageBg:        "#0f172a",
    cardBg:        "#1e293b",
    borderColor:   "#334155",
    textPrimary:   "#f1f5f9",
    textSecondary: "#94a3b8",
    textMuted:     "#64748b",
    rowBorder:     "#293548",
    tableHeadBg:   "#162032",
  } : {
    pageBg:        "#f4f4f5",
    cardBg:        "#ffffff",
    borderColor:   "#e5e7eb",
    textPrimary:   "#111827",
    textSecondary: "#6b7280",
    textMuted:     "#9ca3af",
    rowBorder:     "#f3f4f6",
    tableHeadBg:   "#f9fafb",
  };

  // Aggregate stats across all processed emails
  const totalCost    = costLogs.reduce((s, e) => s + (e.total_cost_usd || 0), 0);
  const totalCalls   = costLogs.reduce((s, e) => s + (e.llm_usage?.length || 0), 0);
  const totalTokens  = costLogs.reduce((s, e) =>
    s + (e.llm_usage || []).reduce((ss, u) => ss + (u.total_tokens || 0), 0), 0);
  const avgDurationMs = totalCalls > 0
    ? costLogs.reduce((s, e) =>
        s + (e.llm_usage || []).reduce((ss, u) => ss + (u.duration_ms || 0), 0), 0) / totalCalls
    : 0;

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: "28px" }}>
        <h1 style={{ fontSize: "22px", fontWeight: 700, color: t.textPrimary, letterSpacing: "-0.3px" }}>
          Monitor
        </h1>
        <p style={{ fontSize: "13.5px", color: t.textMuted, marginTop: "4px" }}>
          LLM usage and cost breakdown per processed email — powered by Gemini 2.5 Flash
        </p>
      </div>

      {/* Summary cards */}
      <div style={{ display: "flex", gap: "16px", marginBottom: "32px", flexWrap: "wrap" }}>
        <MiniCard
          label="Total Cost"
          value={`$${totalCost.toFixed(6)}`}
          icon={DollarSign}
          color="#f59e0b" bg={isDark ? "#2d2000" : "#fffbeb"}
          cardBg={t.cardBg} border={t.borderColor}
          textPrimary={t.textPrimary} textSecondary={t.textSecondary}
        />
        <MiniCard
          label="LLM Calls"
          value={totalCalls}
          icon={Activity}
          color="#6366f1" bg={isDark ? "#1e1a3a" : "#eef2ff"}
          cardBg={t.cardBg} border={t.borderColor}
          textPrimary={t.textPrimary} textSecondary={t.textSecondary}
        />
        <MiniCard
          label="Total Tokens"
          value={totalTokens.toLocaleString()}
          icon={Zap}
          color="#10b981" bg={isDark ? "#062318" : "#f0fdf4"}
          cardBg={t.cardBg} border={t.borderColor}
          textPrimary={t.textPrimary} textSecondary={t.textSecondary}
        />
        <MiniCard
          label="Avg Latency"
          value={avgDurationMs > 0 ? `${Math.round(avgDurationMs)} ms` : "—"}
          icon={Clock}
          color="#3b82f6" bg={isDark ? "#0c1f3a" : "#eff6ff"}
          cardBg={t.cardBg} border={t.borderColor}
          textPrimary={t.textPrimary} textSecondary={t.textSecondary}
        />
      </div>

      {/* Pricing note */}
      <div style={{
        backgroundColor: t.cardBg, border: `1px solid ${t.borderColor}`,
        borderRadius: "12px", padding: "14px 20px", marginBottom: "24px",
        display: "flex", alignItems: "center", gap: "10px",
      }}>
        <span style={{ fontSize: "12.5px", color: t.textMuted }}>
          Pricing: Gemini 2.5 Flash — <strong style={{ color: t.textSecondary }}>$0.075 / 1M input tokens</strong>
          &nbsp;·&nbsp;<strong style={{ color: t.textSecondary }}>$0.30 / 1M output tokens</strong>
          &nbsp;·&nbsp;ConfluenceSearchAgent &amp; CustomerContextAgent token counts are estimated (FunctionAgent internals).
        </span>
      </div>

      {/* Per-email table */}
      <div style={{
        backgroundColor: t.cardBg, border: `1px solid ${t.borderColor}`,
        borderRadius: "14px", overflow: "hidden",
      }}>
        <div style={{ padding: "16px 24px", borderBottom: `1px solid ${t.rowBorder}` }}>
          <p style={{ fontSize: "13px", fontWeight: 600, color: t.textPrimary }}>
            Per-Email Cost Breakdown
          </p>
          <p style={{ fontSize: "12px", color: t.textMuted, marginTop: "2px" }}>
            Click a row to expand per-agent details
          </p>
        </div>

        {costLogs.length === 0 ? (
          <div style={{ padding: "56px 20px", textAlign: "center", color: t.textMuted, fontSize: "14px" }}>
            No data yet. Process emails from the Inbox to see cost tracking here.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ backgroundColor: t.tableHeadBg }}>
                <th style={{ padding: "10px 16px 10px 20px", width: "28px" }} />
                {["Email", "LLM Calls", "Total Tokens", "Cost (USD)"].map((h) => (
                  <th key={h} style={{
                    padding: "10px 20px", textAlign: "left",
                    fontSize: "12px", fontWeight: 600, color: t.textSecondary,
                    letterSpacing: "0.4px", textTransform: "uppercase",
                  }}>
                    {h}
                  </th>
                ))}
                <th style={{ padding: "10px 20px", width: "48px" }} />
              </tr>
            </thead>
            <tbody>
              {costLogs.map((entry, i) => (
                <EmailCostRow
                  key={i}
                  entry={entry}
                  isDark={isDark}
                  t={t}
                  emails={emails}
                />
              ))}
            </tbody>
            {/* Footer total */}
            <tfoot>
              <tr style={{ borderTop: `2px solid ${t.borderColor}` }}>
                <td />
                <td style={{ padding: "12px 20px", fontSize: "13px", fontWeight: 600, color: t.textPrimary }}>
                  Total ({costLogs.length} email{costLogs.length !== 1 ? "s" : ""})
                </td>
                <td style={{ padding: "12px 20px", fontSize: "13px", color: t.textSecondary }}>{totalCalls}</td>
                <td style={{ padding: "12px 20px", fontSize: "13px", color: t.textSecondary }}>{totalTokens.toLocaleString()}</td>
                <td style={{ padding: "12px 20px" }}>
                  <span style={{ fontFamily: "monospace", fontSize: "14px", fontWeight: 700, color: "#f59e0b" }}>
                    ${totalCost.toFixed(6)}
                  </span>
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        )}
      </div>
    </div>
  );
}
