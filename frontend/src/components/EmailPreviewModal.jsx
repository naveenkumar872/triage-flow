import React, { useEffect, useRef, useState } from "react";
import { X, User, Calendar, Tag, Code, AlignLeft } from "lucide-react";

const STATUS_COLORS = {
  Processed:   { bg: "#f0fdf4", color: "#15803d", border: "#bbf7d0" },
  Processing:  { bg: "#eff6ff", color: "#1d4ed8", border: "#bfdbfe" },
  Unprocessed: { bg: "#f9fafb", color: "#374151", border: "#e5e7eb" },
  Failed:      { bg: "#fef2f2", color: "#dc2626", border: "#fecaca" },
};

function HtmlFrame({ html }) {
  const ref = useRef(null);

  useEffect(() => {
    const iframe = ref.current;
    if (!iframe) return;
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    if (!doc) return;
    doc.open();
    doc.write(html);
    doc.close();
    // Auto-resize to content height
    const resize = () => {
      try {
        const h = doc.documentElement.scrollHeight || doc.body?.scrollHeight || 400;
        iframe.style.height = h + "px";
      } catch { /* cross-origin guard */ }
    };
    iframe.addEventListener("load", resize);
    resize();
    return () => iframe.removeEventListener("load", resize);
  }, [html]);

  return (
    <iframe
      ref={ref}
      title="email-body"
      sandbox="allow-same-origin"
      style={{
        width: "100%", minHeight: "400px", border: "none",
        borderRadius: "10px", display: "block", backgroundColor: "#fff",
      }}
    />
  );
}

export default function EmailPreviewModal({ email, onClose, isDark }) {
  const hasHtml = !!(email?.body_html);
  const [viewHtml, setViewHtml] = useState(hasHtml);

  useEffect(() => {
    setViewHtml(!!(email?.body_html));
  }, [email]);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!email) return null;

  const sc         = STATUS_COLORS[email.status] || STATUS_COLORS.Unprocessed;
  const bg         = isDark ? "#1e293b" : "#ffffff";
  const border     = isDark ? "#334155" : "#e5e7eb";
  const textPri    = isDark ? "#f1f5f9" : "#111827";
  const textSec    = isDark ? "#94a3b8" : "#6b7280";
  const textMuted  = isDark ? "#64748b" : "#9ca3af";
  const bodyBg     = isDark ? "#162032" : "#f9fafb";
  const bodyBorder = isDark ? "#293548" : "#e5e7eb";
  const toggleBg   = isDark ? "#273448" : "#f3f4f6";
  const toggleActiveBg = isDark ? "#1d4ed8" : "#2563eb";

  const plainBody = email.body_text || email.snippet || "(no body available)";
  const date = email.date ? email.date.replace(/\s+[+-]\d{4}.*$/, "").trim() : null;

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        backgroundColor: "rgba(0,0,0,0.5)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: "24px",
      }}
    >
      <div style={{
        backgroundColor: bg, border: `1px solid ${border}`,
        borderRadius: "16px", width: "100%", maxWidth: "720px",
        maxHeight: "90vh", display: "flex", flexDirection: "column",
        boxShadow: "0 24px 64px rgba(0,0,0,0.28)",
        animation: "epModalIn 0.14s ease",
      }}>

        {/* ── Header ── */}
        <div style={{
          display: "flex", alignItems: "flex-start", justifyContent: "space-between",
          padding: "20px 24px 16px", borderBottom: `1px solid ${border}`,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "15px", fontWeight: 700, color: textPri, lineHeight: 1.35, wordBreak: "break-word" }}>
              {email.subject || "(no subject)"}
            </div>
            {email.status && (
              <span style={{
                display: "inline-block", marginTop: "8px",
                backgroundColor: sc.bg, color: sc.color, border: `1px solid ${sc.border}`,
                fontSize: "11.5px", fontWeight: 500, padding: "3px 10px", borderRadius: "9999px",
              }}>
                {email.status}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: "30px", height: "30px", borderRadius: "8px",
              border: `1px solid ${border}`, backgroundColor: "transparent",
              cursor: "pointer", color: textMuted, flexShrink: 0, marginLeft: "12px",
            }}
          >
            <X size={15} strokeWidth={2} />
          </button>
        </div>

        {/* ── Meta ── */}
        <div style={{ padding: "14px 24px", borderBottom: `1px solid ${border}`, display: "flex", flexDirection: "column", gap: "9px" }}>
          {(email.sender_name || email.sender_email) && (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <User size={13} color={textMuted} strokeWidth={2} />
              <span style={{ fontSize: "12.5px", color: textMuted, minWidth: "44px" }}>From</span>
              <span style={{ fontSize: "13px", color: textPri, fontWeight: 500 }}>
                {email.sender_name || ""}
              </span>
              {email.sender_email && (
                <span style={{ fontSize: "12.5px", color: textSec }}>
                  &lt;{email.sender_email}&gt;
                </span>
              )}
            </div>
          )}
          {date && (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Calendar size={13} color={textMuted} strokeWidth={2} />
              <span style={{ fontSize: "12.5px", color: textMuted, minWidth: "44px" }}>Date</span>
              <span style={{ fontSize: "13px", color: textSec }}>{date}</span>
            </div>
          )}
          {email.gmail_id && (
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Tag size={13} color={textMuted} strokeWidth={2} />
              <span style={{ fontSize: "12.5px", color: textMuted, minWidth: "44px" }}>ID</span>
              <span style={{ fontSize: "11.5px", fontFamily: "monospace", color: textMuted }}>{email.gmail_id}</span>
            </div>
          )}
        </div>

        {/* ── View toggle (only if HTML available) ── */}
        {hasHtml && (
          <div style={{
            display: "flex", gap: "6px", padding: "10px 24px",
            borderBottom: `1px solid ${border}`,
          }}>
            {[
              { label: "Rendered", icon: AlignLeft, value: true  },
              { label: "Plain text", icon: Code,      value: false },
            ].map(({ label, icon: Icon, value }) => (
              <button
                key={label}
                onClick={() => setViewHtml(value)}
                style={{
                  display: "flex", alignItems: "center", gap: "5px",
                  padding: "5px 12px", borderRadius: "7px", fontSize: "12px", fontWeight: 500,
                  border: "none", cursor: "pointer",
                  backgroundColor: viewHtml === value ? toggleActiveBg : toggleBg,
                  color: viewHtml === value ? "#fff" : textSec,
                  transition: "background 0.15s",
                }}
              >
                <Icon size={12} strokeWidth={2} />
                {label}
              </button>
            ))}
          </div>
        )}

        {/* ── Body ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px 20px" }}>
          {viewHtml && hasHtml ? (
            <div style={{
              border: `1px solid ${bodyBorder}`, borderRadius: "10px",
              overflow: "hidden", backgroundColor: "#fff",
            }}>
              <HtmlFrame html={email.body_html} />
            </div>
          ) : (
            <div style={{
              backgroundColor: bodyBg, border: `1px solid ${bodyBorder}`,
              borderRadius: "10px", padding: "16px",
            }}>
              <pre style={{
                margin: 0, fontSize: "13px", lineHeight: "1.75",
                color: textPri, whiteSpace: "pre-wrap", wordBreak: "break-word",
                fontFamily: "inherit",
              }}>
                {plainBody}
              </pre>
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes epModalIn {
          from { opacity: 0; transform: scale(0.96) translateY(6px); }
          to   { opacity: 1; transform: scale(1)    translateY(0);   }
        }
      `}</style>
    </div>
  );
}
