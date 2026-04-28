import React, { useState, useEffect, useCallback } from "react";
import { Mail, AlertCircle, Clock, CheckCircle2, RefreshCw, Loader2, Eye, GitBranch } from "lucide-react";
import Sidebar from "./components/Sidebar";
import StatsCard from "./components/StatsCard";
import EmailTable from "./components/EmailTable";
import MonitorView from "./components/MonitorView";
import EmailPreviewModal from "./components/EmailPreviewModal";
import PipelineJourneyModal from "./components/PipelineJourneyModal";

const API_BASE = "http://localhost:8080";

// ── Pipeline step derivation ────────────────────────────────
// Infers the agent pipeline journey from a case result dict.
// Returns an array of { agent, kind, note, link?, linkLabel? }.
function derivePipelineSteps(c) {
  const steps = [];
  const add = (agent, kind, note, link, linkLabel) =>
    steps.push({ agent, kind, note: note || "", link, linkLabel });

  // IntakeAgent — always ran
  add("IntakeAgent", "success", `Email received from ${c.sender_email || "unknown"}`);

  // ValidationAgent
  const vr = c.validation_result || {};
  if (vr.label === "spam") {
    add("ValidationAgent", "stopped", `Identified as spam — ${vr.reason || "no reason"} (confidence: ${vr.confidence || "?"})`);
    const vs = c.validation_slack_result || {};
    add("SlackNotify", vs.status === "sent" ? "success" : vs.status ? "error" : "success",
      vs.status === "sent" ? "Slack notified: spam alert sent" : vs.status === "no_channel" ? "Slack: no channel found" : "Slack notified");
    return steps;
  }
  if (vr.label === "non_actionable") {
    add("ValidationAgent", "stopped", `Non-actionable — ${vr.reason || "no reason"} (confidence: ${vr.confidence || "?"})`);
    const vs = c.validation_slack_result || {};
    add("SlackNotify", vs.status === "sent" ? "success" : vs.status ? "error" : "success",
      vs.status === "sent" ? "Slack notified: non-actionable alert sent" : vs.status === "no_channel" ? "Slack: no channel found" : "Slack notified");
    return steps;
  }
  if (vr.label) add("ValidationAgent", "success", `Valid issue — ${vr.reason || ""} (confidence: ${vr.confidence || "?"})`);

  // CustomerContextAgent
  if (c.duplicate_check !== undefined) {
    const profile = c.customer_profile || {};
    add("CustomerContextAgent", "success",
      profile.found ? `Customer profile found — tier: ${profile.profile?.tier || "?"}` : "No customer profile found in DB");
  }

  // DuplicateGate
  const dc = c.duplicate_check || {};
  if (dc.is_duplicate) {
    const first = (dc.matched_issues || [])[0] || {};
    add("DuplicateGate", "stopped",
      `Duplicate of existing ticket — ${dc.duplicate_count || 1} match(es) found`,
      first.key ? `https://ragworks-ai.atlassian.net/browse/${first.key}` : undefined,
      first.key ? `View ${first.key}` : undefined);
    const rr = c.reply_result || {};
    if (rr.status) add("ReplyAgent", rr.status === "sent" ? "success" : "error",
      rr.status === "sent" ? "Duplicate notice sent to customer" : `Reply failed: ${rr.error || "unknown"}`);
    const ds = c.duplicate_slack_result || {};
    add("SlackNotify", ds.status === "sent" ? "success" : ds.status ? "error" : "success",
      ds.status === "sent" ? "Slack notified: duplicate case alert" : ds.status === "no_channel" ? "Slack: no channel found" : "Slack notified");
    return steps;
  }
  if (c.duplicate_check !== undefined) add("DuplicateGate", "success", "Not a duplicate — continuing pipeline");

  // ConfluenceSearchAgent
  const cr = c.confluence_result || {};
  if (cr.docs_found !== undefined) {
    add("ConfluenceSearchAgent", "success",
      cr.docs_found > 0 ? `${cr.docs_found} doc(s) found — "${cr.doc_title || "untitled"}"` : "No matching docs found",
      cr.doc_url || undefined, cr.doc_title || undefined);

    // AutoReplyGate
    if (!cr.escalate_to_human && cr.reply_msg) {
      add("AutoReplyGate", "stopped", `Auto-replied using KB doc — confidence: ${cr.confidence || "?"}`);
      const ar = c.reply_result || {};
      if (ar.status) add("ReplyAgent", ar.status === "sent" ? "success" : "error",
        ar.status === "sent" ? "Auto-reply sent to customer" : `Reply failed: ${ar.error || "unknown"}`);
      const as_ = c.auto_reply_slack_result || {};
      add("SlackNotify", as_.status === "sent" ? "success" : as_.status ? "error" : "success",
        as_.status === "sent" ? "Slack notified: auto-reply sent" : as_.status === "no_channel" ? "Slack: no channel found" : "Slack notified");
      return steps;
    }
    add("AutoReplyGate", "success", `Escalated to human pipeline — ${cr.escalate_to_human ? "no confident KB answer" : "needs human review"}`);
  }

  // TriageClassificationAgent
  const tr = c.triage_result || {};
  if (tr.category) {
    add("TriageClassificationAgent", "success",
      `${tr.category} · ${tr.priority} · ${tr.sentiment} → ${tr.suggested_team || "?"}`);
  }

  // JiraAgent
  const jr = c.jira_result || {};
  if (jr.issue_type) {
    add("JiraAgent", jr.success || jr.ticket_key ? "success" : "error",
      jr.ticket_key ? `Ticket created: ${jr.ticket_key}` : `Failed: ${jr.error || "unknown error"}`,
      jr.ticket_url || undefined, jr.ticket_key ? `View ${jr.ticket_key}` : undefined);
  }

  // NotifySlackAgent
  const sr = c.slack_result || {};
  if (sr.status) {
    add("SlackNotify", sr.status === "sent" ? "success" : "error",
      sr.status === "sent" ? `Slack notified — team channel` : `Slack failed: ${sr.error || "unknown"}`);
  }

  // ReplyAgent
  const rr = c.reply_result || {};
  if (rr.status) {
    add("ReplyAgent", rr.status === "sent" ? "success" : "error",
      rr.status === "sent" ? "Reply draft sent to customer" : `Reply failed: ${rr.error || "unknown"}`);
  }

  return steps;
}

export default function App() {
  const [activeView, setActiveView] = useState("inbox");
  const [isDark, setIsDark] = useState(false);
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [processingIds, setProcessingIds] = useState(new Set());
  const [processedResults, setProcessedResults] = useState({});
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [costLogs, setCostLogs] = useState([]);
  const [pipelineLogs, setPipelineLogs] = useState({});  // gmail_id → case result
  const [analyticsPreviewEmail, setAnalyticsPreviewEmail] = useState(null);
  const [pipelineExpanded, setPipelineExpanded] = useState({});  // gmail_id → bool
  const [pipelineModalEmail, setPipelineModalEmail] = useState(null); // email object for journey modal

  // ── Fetch inbox from Gmail ─────────────────────────────────
  const fetchEmails = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/emails`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setEmails(data);
      setSelectedIds(new Set());
      setProcessedResults({});
    } catch (err) {
      setError(`Failed to fetch emails: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchEmails(); }, [fetchEmails]);

  // ── Derived display list with live status ──────────────────
  const displayEmails = emails.map((e) => ({
    ...e,
    status: processedResults[e.gmail_id]
      ? processedResults[e.gmail_id]
      : processingIds.has(e.gmail_id)
        ? "Processing"
        : "Unprocessed",
  }));

  // ── Process one email ──────────────────────────────────────
  const handleProcess = async (gmailId) => {
    setProcessingIds((prev) => new Set([...prev, gmailId]));
    try {
      const res = await fetch(
        `${API_BASE}/emails/${encodeURIComponent(gmailId)}/process`,
        { method: "POST" }
      );
      const data = await res.json();
      setProcessedResults((prev) => ({
        ...prev,
        [gmailId]: data.status === "SUCCESS" ? "Processed" : "Failed",
      }));
      // Store cost data for Monitor view
      const email = emails.find((e) => e.gmail_id === gmailId);
      if (data.llm_usage && data.llm_usage.length > 0) {
        setCostLogs((prev) => [
          ...prev,
          {
            gmail_id:      gmailId,
            subject:       email?.subject || gmailId,
            llm_usage:     data.llm_usage,
            total_cost_usd: data.total_cost_usd || 0,
          },
        ]);
      }
      // Store per-case pipeline data for Analytics Pipeline Journey
      if (data.case_results && data.case_results.length > 0) {
        setPipelineLogs((prev) => {
          const updated = { ...prev };
          data.case_results.forEach((c) => { if (c.gmail_id) updated[c.gmail_id] = c; });
          return updated;
        });
      }
    } catch {
      setProcessedResults((prev) => ({ ...prev, [gmailId]: "Failed" }));
    } finally {
      setProcessingIds((prev) => { const n = new Set(prev); n.delete(gmailId); return n; });
    }
  };

  // ── Process selected emails ────────────────────────────────
  const handleProcessSelected = async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    setProcessingIds((prev) => new Set([...prev, ...ids]));
    try {
      const res = await fetch(`${API_BASE}/emails/process-selected`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gmail_ids: ids }),
      });
      const data = await res.json();
      const st = data.status === "SUCCESS" ? "Processed" : "Failed";
      setProcessedResults((prev) => { const n = { ...prev }; ids.forEach((id) => (n[id] = st)); return n; });
      // Store cost data for Monitor view — group usage entries by gmail_id
      if (data.llm_usage && data.llm_usage.length > 0) {
        const usageByEmail = {};
        data.llm_usage.forEach((u) => {
          const gid = u.gmail_id || "";
          if (!usageByEmail[gid]) usageByEmail[gid] = [];
          usageByEmail[gid].push(u);
        });
        const newLogs = ids.map((id) => {
          const email = emails.find((e) => e.gmail_id === id);
          const usage = usageByEmail[id] || [];
          const sliceCost = usage.reduce((s, u) => s + (u.cost_usd || 0), 0);
          return { gmail_id: id, subject: email?.subject || id, llm_usage: usage, total_cost_usd: sliceCost };
        });
        setCostLogs((prev) => [...prev, ...newLogs.filter((l) => l.llm_usage.length > 0)]);
      }
      // Store per-case pipeline data for Analytics Pipeline Journey
      if (data.case_results && data.case_results.length > 0) {
        setPipelineLogs((prev) => {
          const updated = { ...prev };
          data.case_results.forEach((c) => { if (c.gmail_id) updated[c.gmail_id] = c; });
          return updated;
        });
      }
    } catch {
      setProcessedResults((prev) => { const n = { ...prev }; ids.forEach((id) => (n[id] = "Failed")); return n; });
    } finally {
      setProcessingIds((prev) => { const n = new Set(prev); ids.forEach((id) => n.delete(id)); return n; });
      setSelectedIds(new Set());
    }
  };

  // ── Process all emails ─────────────────────────────────────
  const handleProcessAll = async () => {
    const allIds = emails.map((e) => e.gmail_id);
    setProcessingIds(new Set(allIds));
    try {
      const res = await fetch(`${API_BASE}/trigger`, { method: "POST" });
      const data = await res.json();
      const st = data.status === "SUCCESS" ? "Processed" : "Failed";
      setProcessedResults((prev) => { const n = { ...prev }; allIds.forEach((id) => (n[id] = st)); return n; });
    } catch {
      setProcessedResults((prev) => { const n = { ...prev }; allIds.forEach((id) => (n[id] = "Failed")); return n; });
    } finally {
      setProcessingIds(new Set());
    }
  };

  // ── Selection ──────────────────────────────────────────────
  const handleSelect = (gmailId, checked) => {
    setSelectedIds((prev) => { const n = new Set(prev); checked ? n.add(gmailId) : n.delete(gmailId); return n; });
  };
  const handleSelectAll = (checked) => {
    setSelectedIds(checked ? new Set(emails.map((e) => e.gmail_id)) : new Set());
  };

  // ── Stats ──────────────────────────────────────────────────
  const totalEmails  = displayEmails.length;
  const unprocessed  = displayEmails.filter((e) => e.status === "Unprocessed").length;
  const inProgress   = displayEmails.filter((e) => e.status === "Processing").length;
  const processed    = displayEmails.filter((e) => e.status === "Processed").length;
  const isAnyBusy    = processingIds.size > 0 || loading;

  // ── Theme tokens ───────────────────────────────────────────
  const t = isDark ? {
    pageBg:        "#0f172a",
    cardBg:        "#1e293b",
    borderColor:   "#334155",
    textPrimary:   "#f1f5f9",
    textSecondary: "#94a3b8",
    textMuted:     "#64748b",
    textBody:      "#cbd5e1",
    tableHeadBg:   "#162032",
    rowBorder:     "#293548",
    btnBg:         "#273448",
    btnColor:      "#cbd5e1",
    btnBorder:     "#334155",
    unprocessedBar:"#334155",
  } : {
    pageBg:        "#f4f4f5",
    cardBg:        "#ffffff",
    borderColor:   "#e5e7eb",
    textPrimary:   "#111827",
    textSecondary: "#6b7280",
    textMuted:     "#9ca3af",
    textBody:      "#374151",
    tableHeadBg:   "#f9fafb",
    rowBorder:     "#f3f4f6",
    btnBg:         "#ffffff",
    btnColor:      "#374151",
    btnBorder:     "#e5e7eb",
    unprocessedBar:"#e5e7eb",
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", backgroundColor: t.pageBg, fontFamily: "Inter, system-ui, sans-serif", transition: "background-color 0.2s" }}>
      <Sidebar activeView={activeView} onNavigate={setActiveView} isDark={isDark} onThemeToggle={() => setIsDark((d) => !d)} />

      <div style={{ flex: 1, marginLeft: "68px", padding: "32px 36px", overflowY: "auto", minHeight: "100vh" }}>

        {/* ── ANALYTICS VIEW ────────────────────────────────────── */}
        {activeView === "analytics" && (
          <>
            <div style={{ marginBottom: "28px" }}>
              <h1 style={{ fontSize: "22px", fontWeight: 700, color: t.textPrimary, letterSpacing: "-0.3px" }}>Analytics</h1>
              <p style={{ fontSize: "13.5px", color: t.textMuted, marginTop: "4px" }}>Email processing breakdown for the current inbox snapshot</p>
            </div>

            {/* Summary stat cards */}
            <div style={{ display: "flex", gap: "16px", marginBottom: "32px" }}>
              <StatsCard title="Total Emails" value={totalEmails}  icon={Mail}         iconColor="#dc2626" iconBg="#fef2f2" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
              <StatsCard title="Unprocessed"  value={unprocessed}  icon={AlertCircle}  iconColor="#ea580c" iconBg="#fff7ed" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
              <StatsCard title="Processing"   value={inProgress}   icon={Clock}        iconColor="#2563eb" iconBg="#eff6ff" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
              <StatsCard title="Processed"    value={processed}    icon={CheckCircle2} iconColor="#16a34a" iconBg="#f0fdf4" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
            </div>

            {/* Status breakdown bar */}
            {totalEmails > 0 && (
              <div style={{ backgroundColor: t.cardBg, border: `1px solid ${t.borderColor}`, borderRadius: "14px", padding: "24px 28px", marginBottom: "24px", transition: "background-color 0.2s" }}>
                <p style={{ fontSize: "13px", fontWeight: 600, color: t.textBody, marginBottom: "16px" }}>Status Distribution</p>
                <div style={{ display: "flex", height: "12px", borderRadius: "9999px", overflow: "hidden", gap: "2px" }}>
                  {[
                    { count: processed,  color: "#16a34a", label: "Processed"   },
                    { count: inProgress, color: "#2563eb", label: "Processing"  },
                    { count: unprocessed,color: t.unprocessedBar, label: "Unprocessed" },
                    { count: displayEmails.filter(e => e.status === "Failed").length, color: "#dc2626", label: "Failed" },
                  ].filter(s => s.count > 0).map((s) => (
                    <div key={s.label}
                      style={{ flex: s.count, backgroundColor: s.color, transition: "flex 0.4s ease" }}
                      title={`${s.label}: ${s.count}`}
                    />
                  ))}
                </div>
                <div style={{ display: "flex", gap: "20px", marginTop: "12px", flexWrap: "wrap" }}>
                  {[
                    { label: "Processed",   count: processed,   color: "#16a34a" },
                    { label: "Processing",  count: inProgress,  color: "#2563eb" },
                    { label: "Unprocessed", count: unprocessed, color: t.textMuted },
                    { label: "Failed",      count: displayEmails.filter(e => e.status === "Failed").length, color: "#dc2626" },
                  ].map((s) => (
                    <div key={s.label} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: s.color }} />
                      <span style={{ fontSize: "12.5px", color: t.textSecondary }}>{s.label}</span>
                      <span style={{ fontSize: "12.5px", fontWeight: 600, color: t.textPrimary }}>{s.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Per-email status list */}
            <div style={{ backgroundColor: t.cardBg, border: `1px solid ${t.borderColor}`, borderRadius: "14px", overflow: "hidden", transition: "background-color 0.2s" }}>
              <div style={{ padding: "16px 24px", borderBottom: `1px solid ${t.rowBorder}` }}>
                <p style={{ fontSize: "13px", fontWeight: 600, color: t.textBody }}>Email Status Breakdown</p>
              </div>
              {displayEmails.length === 0 ? (
                <p style={{ padding: "24px", color: t.textMuted, fontSize: "13.5px", textAlign: "center" }}>No emails loaded. Go to Inbox and click Refresh.</p>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                  <thead>
                    <tr style={{ backgroundColor: t.tableHeadBg }}>
                      {["Subject", "From", "Date", "Status"].map((h) => (
                        <th key={h} style={{ padding: "10px 20px", textAlign: "left", fontWeight: 600, color: t.textSecondary, fontSize: "11.5px", textTransform: "uppercase", letterSpacing: "0.5px" }}>{h}</th>
                      ))}
                      <th style={{ padding: "10px 20px", width: "48px" }} />
                    </tr>
                  </thead>
                  <tbody>
                    {displayEmails.map((e, i) => {
                      const statusColors = {
                        Processed:   { bg: "#f0fdf4", color: "#15803d" },
                        Processing:  { bg: "#eff6ff", color: "#1d4ed8" },
                        Unprocessed: { bg: isDark ? "#1e293b" : "#f9fafb", color: isDark ? "#94a3b8" : "#374151" },
                        Failed:      { bg: "#fef2f2", color: "#dc2626" },
                      };
                      const sc = statusColors[e.status] || statusColors.Unprocessed;
                      return (
                        <tr key={e.gmail_id} style={{ borderTop: i > 0 ? `1px solid ${t.rowBorder}` : "none" }}>
                          <td style={{ padding: "12px 20px", color: t.textPrimary, fontWeight: 500, maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.subject || "(no subject)"}</td>
                          <td style={{ padding: "12px 20px", color: t.textSecondary }}>{e.sender_name || e.sender_email || "—"}</td>
                          <td style={{ padding: "12px 20px", color: t.textMuted, whiteSpace: "nowrap" }}>{e.date ? new Date(e.date).toLocaleDateString() : "—"}</td>
                          <td style={{ padding: "12px 20px" }}>
                            <span style={{ backgroundColor: sc.bg, color: sc.color, fontSize: "11.5px", fontWeight: 500, padding: "3px 10px", borderRadius: "9999px" }}>{e.status}</span>
                          </td>
                          <td style={{ padding: "12px 20px" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                              {/* Eye — preview email */}
                              <button
                                onClick={() => setAnalyticsPreviewEmail(e)}
                                title="Preview email"
                                style={{
                                  display: "flex", alignItems: "center", justifyContent: "center",
                                  width: "28px", height: "28px", borderRadius: "6px",
                                  border: `1px solid ${t.btnBorder}`, backgroundColor: "transparent",
                                  cursor: "pointer", color: t.textSecondary,
                                }}
                              >
                                <Eye size={14} strokeWidth={2} />
                              </button>

                              {/* Pipeline Journey — only shown when data available */}
                              {pipelineLogs[e.gmail_id] && (
                                <button
                                  onClick={() => setPipelineModalEmail(e)}
                                  title="View pipeline journey"
                                  style={{
                                    display: "flex", alignItems: "center", justifyContent: "center",
                                    width: "28px", height: "28px", borderRadius: "6px",
                                    border: `1px solid ${isDark ? "#1e3a5f" : "#bfdbfe"}`,
                                    backgroundColor: isDark ? "#1e3a5f" : "#eff6ff",
                                    cursor: "pointer", color: "#2563eb",
                                  }}
                                >
                                  <GitBranch size={14} strokeWidth={2} />
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
            {/* Pipeline Journey — per-email agent breakdown */}
            {Object.keys(pipelineLogs).length > 0 && (
              <div style={{ backgroundColor: t.cardBg, border: `1px solid ${t.borderColor}`, borderRadius: "14px", overflow: "hidden", marginTop: "24px", transition: "background-color 0.2s" }}>
                <div style={{ padding: "16px 24px", borderBottom: `1px solid ${t.rowBorder}` }}>
                  <p style={{ fontSize: "13px", fontWeight: 600, color: t.textBody }}>Pipeline Journey</p>
                  <p style={{ fontSize: "12px", color: t.textMuted, marginTop: "2px" }}>Per-email agent trace — what ran, what stopped, and why</p>
                </div>
                {displayEmails.filter(e => pipelineLogs[e.gmail_id]).map((e, i) => {
                  const caseData = pipelineLogs[e.gmail_id];
                  const steps = derivePipelineSteps(caseData);
                  const isOpen = !!pipelineExpanded[e.gmail_id];
                  const finalStep = steps[steps.length - 1];
                  const outcomeColor = finalStep?.kind === "stopped" ? "#ea580c" : finalStep?.kind === "error" ? "#dc2626" : "#16a34a";
                  const outcomeLabel = finalStep?.kind === "stopped" ? "Stopped" : finalStep?.kind === "error" ? "Error" : "Completed";
                  return (
                    <div key={e.gmail_id} style={{ borderTop: i > 0 ? `1px solid ${t.rowBorder}` : "none" }}>
                      {/* Row header — clickable to expand */}
                      <div
                        onClick={() => setPipelineExpanded(prev => ({ ...prev, [e.gmail_id]: !prev[e.gmail_id] }))}
                        style={{ display: "flex", alignItems: "center", padding: "12px 24px", cursor: "pointer", gap: "12px", userSelect: "none" }}
                      >
                        <span style={{ fontSize: "13px", fontWeight: 500, color: t.textPrimary, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.subject || "(no subject)"}</span>
                        <span style={{ fontSize: "11.5px", color: t.textSecondary, whiteSpace: "nowrap" }}>{e.sender_name || e.sender_email || "—"}</span>
                        <span style={{ fontSize: "11.5px", fontWeight: 600, color: outcomeColor, backgroundColor: isDark ? "rgba(0,0,0,0.25)" : "#f9fafb", padding: "2px 10px", borderRadius: "9999px", border: `1px solid ${isDark ? "rgba(255,255,255,0.08)" : "#e5e7eb"}` }}>{outcomeLabel} · {steps.length} step{steps.length !== 1 ? "s" : ""}</span>
                        <span style={{ fontSize: "11px", color: t.textMuted, transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>▼</span>
                      </div>
                      {/* Expanded pipeline steps */}
                      {isOpen && (
                        <div style={{ padding: "4px 24px 20px 24px", backgroundColor: isDark ? "rgba(0,0,0,0.15)" : "#fafafa" }}>
                          <div style={{ display: "flex", flexDirection: "column", gap: "0" }}>
                            {steps.map((step, si) => {
                              const dotColor = step.kind === "success" ? "#16a34a" : step.kind === "stopped" ? "#ea580c" : step.kind === "error" ? "#dc2626" : "#6b7280";
                              const isLast = si === steps.length - 1;
                              return (
                                <div key={si} style={{ display: "flex", gap: "12px" }}>
                                  {/* Timeline spine */}
                                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "20px", flexShrink: 0 }}>
                                    <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: dotColor, marginTop: "14px", flexShrink: 0, boxShadow: `0 0 0 2px ${isDark ? "#1e293b" : "#fafafa"}` }} />
                                    {!isLast && <div style={{ width: "2px", flex: 1, backgroundColor: isDark ? "#334155" : "#e5e7eb", marginTop: "2px" }} />}
                                  </div>
                                  {/* Step content */}
                                  <div style={{ paddingBottom: isLast ? 0 : "10px", paddingTop: "8px" }}>
                                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                      <span style={{ fontSize: "12.5px", fontWeight: 600, color: t.textPrimary }}>{step.agent}</span>
                                      <span style={{ fontSize: "10.5px", fontWeight: 600, color: dotColor, textTransform: "uppercase", letterSpacing: "0.4px" }}>{step.kind}</span>
                                    </div>
                                    <p style={{ fontSize: "12px", color: t.textSecondary, margin: "2px 0 0 0", lineHeight: "1.5" }}>{step.note}</p>
                                    {step.link && (
                                      <a href={step.link} target="_blank" rel="noreferrer" style={{ fontSize: "11.5px", color: "#2563eb", textDecoration: "none" }}>
                                        {step.linkLabel || step.link} ↗
                                      </a>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {analyticsPreviewEmail && (
          <EmailPreviewModal
            email={analyticsPreviewEmail}
            onClose={() => setAnalyticsPreviewEmail(null)}
            isDark={isDark}
          />
        )}

        {pipelineModalEmail && pipelineLogs[pipelineModalEmail.gmail_id] && (
          <PipelineJourneyModal
            email={pipelineModalEmail}
            steps={derivePipelineSteps(pipelineLogs[pipelineModalEmail.gmail_id])}
            onClose={() => setPipelineModalEmail(null)}
            isDark={isDark}
          />
        )}

        {/* ── INBOX VIEW ────────────────────────────────────────── */}
        {activeView === "inbox" && (<>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "28px" }}>
          <div>
            <h1 style={{ fontSize: "22px", fontWeight: 700, color: t.textPrimary, letterSpacing: "-0.3px" }}>Support Inbox</h1>
            <p style={{ fontSize: "13.5px", color: t.textMuted, marginTop: "4px" }}>
              Manage customer support tickets powered by AI agents
            </p>
          </div>

          <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            {/* Refresh */}
            <button
              onClick={fetchEmails}
              disabled={loading}
              style={{
                display: "flex", alignItems: "center", gap: "7px",
                padding: "9px 16px", borderRadius: "10px",
                fontSize: "13.5px", fontWeight: 600,
                backgroundColor: t.btnBg, color: t.btnColor,
                border: `1px solid ${t.btnBorder}`,
                cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.7 : 1,
              }}
            >
              <RefreshCw size={14} strokeWidth={2.5}
                style={loading ? { animation: "spin 1s linear infinite" } : {}}
              />
              {loading ? "Fetching..." : "Refresh Inbox"}
            </button>

            {/* Process All */}
            <button
              onClick={handleProcessAll}
              disabled={emails.length === 0 || isAnyBusy}
              style={{
                display: "flex", alignItems: "center", gap: "7px",
                padding: "9px 18px", borderRadius: "10px",
                fontSize: "13.5px", fontWeight: 600,
                backgroundColor: isAnyBusy ? "#d1d5db" : "#111827",
                color: isAnyBusy ? "#6b7280" : "#ffffff",
                border: "none",
                cursor: emails.length === 0 || isAnyBusy ? "not-allowed" : "pointer",
              }}
            >
              {isAnyBusy && processingIds.size === emails.length && emails.length > 0 ? (
                <><Loader2 size={14} strokeWidth={2.5} style={{ animation: "spin 1s linear infinite" }} />Processing...</>
              ) : (
                <><Mail size={14} strokeWidth={2.5} />Process All</>
              )}
            </button>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div style={{ padding: "12px 16px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", borderRadius: "10px", color: "#dc2626", fontSize: "13.5px", marginBottom: "20px" }}>
            {error}
          </div>
        )}

        {/* Stats Cards */}
        <div style={{ display: "flex", gap: "16px", marginBottom: "28px" }}>
          <StatsCard title="Total Emails" value={totalEmails} icon={Mail}         iconColor="#dc2626" iconBg="#fef2f2" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
          <StatsCard title="Unprocessed"  value={unprocessed} icon={AlertCircle}  iconColor="#ea580c" iconBg="#fff7ed" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
          <StatsCard title="Processing"   value={inProgress}  icon={Clock}        iconColor="#2563eb" iconBg="#eff6ff" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
          <StatsCard title="Processed"    value={processed}   icon={CheckCircle2} iconColor="#16a34a" iconBg="#f0fdf4" cardBg={t.cardBg} cardBorder={t.borderColor} titleColor={t.textSecondary} valueColor={t.textPrimary} />
        </div>

        {/* Email Table */}
        <EmailTable
          emails={displayEmails}
          selectedIds={selectedIds}
          onSelect={handleSelect}
          onSelectAll={handleSelectAll}
          onProcess={handleProcess}
          onProcessSelected={handleProcessSelected}
          isDark={isDark}
        />
        </>)}

        {/* ── MONITOR VIEW ────────────────────────────────────── */}
        {activeView === "monitor" && (
          <MonitorView costLogs={costLogs} isDark={isDark} emails={displayEmails} />
        )}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
