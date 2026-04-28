import React, { useEffect } from "react";
import { X, GitBranch, CheckCircle2, AlertTriangle, XCircle, ExternalLink } from "lucide-react";

const KIND_CONFIG = {
  success: { color: "#16a34a", bg: "#f0fdf4", border: "#bbf7d0", Icon: CheckCircle2,  label: "Passed"  },
  stopped: { color: "#ea580c", bg: "#fff7ed", border: "#fed7aa", Icon: AlertTriangle, label: "Stopped" },
  error:   { color: "#dc2626", bg: "#fef2f2", border: "#fecaca", Icon: XCircle,       label: "Error"   },
};

function StepRow({ step, isLast, isDark }) {
  const cfg  = KIND_CONFIG[step.kind] || KIND_CONFIG.success;
  const line = isDark ? "#334155" : "#e5e7eb";

  return (
    <div style={{ display: "flex", gap: "14px" }}>
      {/* Spine */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "22px", flexShrink: 0 }}>
        <div style={{
          width: "12px", height: "12px", borderRadius: "50%",
          backgroundColor: cfg.color,
          marginTop: "13px", flexShrink: 0,
          boxShadow: `0 0 0 3px ${isDark ? "#1e293b" : "#ffffff"}`,
        }} />
        {!isLast && (
          <div style={{ width: "2px", flex: 1, backgroundColor: line, marginTop: "3px" }} />
        )}
      </div>

      {/* Content */}
      <div style={{ paddingBottom: isLast ? "0" : "14px", paddingTop: "6px", flex: 1, minWidth: 0 }}>
        {/* Agent name + badge */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <span style={{
            fontSize: "13px", fontWeight: 600,
            color: isDark ? "#f1f5f9" : "#111827",
          }}>
            {step.agent}
          </span>
          <span style={{
            fontSize: "10px", fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.5px", padding: "2px 7px", borderRadius: "9999px",
            backgroundColor: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
          }}>
            {cfg.label}
          </span>
        </div>

        {/* One-line note */}
        <p style={{
          fontSize: "12.5px", margin: "3px 0 0 0", lineHeight: "1.55",
          color: isDark ? "#94a3b8" : "#6b7280",
        }}>
          {step.note}
        </p>

        {/* Optional link */}
        {step.link && (
          <a
            href={step.link}
            target="_blank"
            rel="noreferrer"
            style={{
              display: "inline-flex", alignItems: "center", gap: "4px",
              fontSize: "11.5px", color: "#2563eb", textDecoration: "none", marginTop: "4px",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")}
            onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}
          >
            {step.linkLabel || step.link}
            <ExternalLink size={11} strokeWidth={2} />
          </a>
        )}
      </div>
    </div>
  );
}

export default function PipelineJourneyModal({ email, steps, onClose, isDark }) {
  // Close on ESC
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!email || !steps) return null;

  const finalStep    = steps[steps.length - 1];
  const outcomeCfg   = KIND_CONFIG[finalStep?.kind] || KIND_CONFIG.success;
  const outcomeLabel = finalStep?.kind === "stopped" ? "Stopped Early" : finalStep?.kind === "error" ? "Error" : "Fully Completed";

  const bg     = isDark ? "#0f172a"  : "rgba(0,0,0,0.45)";
  const cardBg = isDark ? "#1e293b"  : "#ffffff";
  const border = isDark ? "#334155"  : "#e5e7eb";
  const textPrimary   = isDark ? "#f1f5f9" : "#111827";
  const textSecondary = isDark ? "#94a3b8" : "#6b7280";
  const textMuted     = isDark ? "#64748b" : "#9ca3af";
  const headerBg      = isDark ? "#162032" : "#f9fafb";

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        backgroundColor: isDark ? "rgba(0,0,0,0.7)" : "rgba(0,0,0,0.45)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "24px", backdropFilter: "blur(3px)",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: cardBg, border: `1px solid ${border}`,
          borderRadius: "16px", width: "100%", maxWidth: "540px",
          maxHeight: "82vh", display: "flex", flexDirection: "column",
          boxShadow: "0 24px 48px rgba(0,0,0,0.22)",
          overflow: "hidden",
        }}
      >
        {/* ── Header ── */}
        <div style={{
          padding: "18px 22px",
          borderBottom: `1px solid ${border}`,
          backgroundColor: headerBg,
          display: "flex", alignItems: "flex-start", gap: "12px",
        }}>
          <div style={{
            width: "34px", height: "34px", borderRadius: "8px",
            backgroundColor: isDark ? "#1e3a5f" : "#eff6ff",
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
          }}>
            <GitBranch size={17} color="#2563eb" strokeWidth={2} />
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: "14px", fontWeight: 700, color: textPrimary, margin: 0 }}>
              Pipeline Journey
            </p>
            <p style={{
              fontSize: "12px", color: textSecondary, margin: "3px 0 0 0",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
              title={email.subject}>
              {email.subject || "(no subject)"}
            </p>
          </div>

          {/* Outcome badge */}
          <span style={{
            fontSize: "11px", fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.4px", padding: "3px 10px", borderRadius: "9999px",
            backgroundColor: outcomeCfg.bg, color: outcomeCfg.color,
            border: `1px solid ${outcomeCfg.border}`, whiteSpace: "nowrap",
            flexShrink: 0,
          }}>
            {outcomeLabel}
          </span>

          <button
            onClick={onClose}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "28px", height: "28px", borderRadius: "6px",
              border: `1px solid ${border}`, backgroundColor: "transparent",
              cursor: "pointer", color: textMuted, flexShrink: 0,
            }}
          >
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* ── Meta row ── */}
        <div style={{
          padding: "10px 22px",
          borderBottom: `1px solid ${border}`,
          display: "flex", alignItems: "center", gap: "16px",
          flexWrap: "wrap",
        }}>
          {email.sender_name && (
            <span style={{ fontSize: "12px", color: textSecondary }}>
              <span style={{ fontWeight: 600, color: textPrimary }}>{email.sender_name}</span>
              {email.sender_email && ` <${email.sender_email}>`}
            </span>
          )}
          <span style={{ fontSize: "12px", color: textMuted }}>
            {steps.length} agent step{steps.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* ── Timeline body ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
          {steps.map((step, i) => (
            <StepRow
              key={i}
              step={step}
              isLast={i === steps.length - 1}
              isDark={isDark}
            />
          ))}
        </div>

        {/* ── Footer ── */}
        <div style={{
          padding: "14px 22px",
          borderTop: `1px solid ${border}`,
          display: "flex", justifyContent: "flex-end",
          backgroundColor: headerBg,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: "7px 20px", borderRadius: "8px", fontSize: "13px", fontWeight: 600,
              backgroundColor: isDark ? "#334155" : "#111827",
              color: "#ffffff", border: "none", cursor: "pointer",
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
