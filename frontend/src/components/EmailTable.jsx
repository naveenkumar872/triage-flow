import React, { useState } from "react";
import { Search, ChevronDown, Loader2, Eye } from "lucide-react";
import EmailPreviewModal from "./EmailPreviewModal";

const FILTERS = ["All", "Unprocessed", "Processing", "Processed", "Failed"];

const STATUS_STYLES = {
  Unprocessed: { bg: "#f9fafb", color: "#374151",  border: "1px solid #e5e7eb" },
  Processing:  { bg: "#eff6ff", color: "#1d4ed8",  border: "1px solid #bfdbfe" },
  Processed:   { bg: "#f0fdf4", color: "#15803d",  border: "1px solid #bbf7d0" },
  Failed:      { bg: "#fef2f2", color: "#dc2626",  border: "1px solid #fecaca" },
};

function StatusBadge({ label }) {
  const s = STATUS_STYLES[label] || STATUS_STYLES.Unprocessed;
  return (
    <span style={{ backgroundColor: s.bg, color: s.color, border: s.border,
      fontSize: "11.5px", fontWeight: 500, padding: "3px 10px",
      borderRadius: "9999px", whiteSpace: "nowrap" }}>
      {label}
    </span>
  );
}

export default function EmailTable({
  emails, selectedIds, onSelect, onSelectAll, onProcess, onProcessSelected, isDark,
}) {
  const [activeFilter, setActiveFilter] = useState("All");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("Newest");
  const [previewEmail, setPreviewEmail] = useState(null);

  const t = isDark ? {
    cardBg:           "#1e293b",
    border:           "#334155",
    rowBorder:        "#293548",
    toolbarBg:        "#1e293b",
    searchBg:         "#162032",
    searchBorder:     "#334155",
    searchText:       "#cbd5e1",
    searchPlaceholder:"#64748b",
    filterActiveBg:   "#f1f5f9",
    filterActiveColor:"#0f172a",
    filterColor:      "#94a3b8",
    colHeadColor:     "#64748b",
    rowHover:         "#1a2a3a",
    rowChecked:       "#1a2c45",
    subjectColor:     "#f1f5f9",
    snippetColor:     "#64748b",
    senderName:       "#cbd5e1",
    senderEmail:      "#64748b",
    dateColor:        "#94a3b8",
    sortColor:        "#94a3b8",
    sortValueColor:   "#f1f5f9",
    menuBg:           "#1e293b",
    menuBorder:       "#334155",
    menuItemColor:    "#cbd5e1",
    menuItemHover:    "#273448",
    dotBtnBg:         "#273448",
    dotBtnBorder:     "#334155",
    dotBtnColor:      "#94a3b8",
    emptyColor:       "#64748b",
    processBtnBg:     "#334155",
    processBtnColor:  "#f1f5f9",
  } : {
    cardBg:           "#ffffff",
    border:           "#e5e7eb",
    rowBorder:        "#f9fafb",
    toolbarBg:        "#ffffff",
    searchBg:         "#fafafa",
    searchBorder:     "#e5e7eb",
    searchText:       "#374151",
    searchPlaceholder:"#9ca3af",
    filterActiveBg:   "#111827",
    filterActiveColor:"#ffffff",
    filterColor:      "#6b7280",
    colHeadColor:     "#9ca3af",
    rowHover:         "#fafafa",
    rowChecked:       "#f8faff",
    subjectColor:     "#111827",
    snippetColor:     "#9ca3af",
    senderName:       "#374151",
    senderEmail:      "#9ca3af",
    dateColor:        "#6b7280",
    sortColor:        "#6b7280",
    sortValueColor:   "#111827",
    menuBg:           "#ffffff",
    menuBorder:       "#e5e7eb",
    menuItemColor:    "#374151",
    menuItemHover:    "#f9fafb",
    dotBtnBg:         "#ffffff",
    dotBtnBorder:     "#e5e7eb",
    dotBtnColor:      "#6b7280",
    emptyColor:       "#9ca3af",
    processBtnBg:     "#111827",
    processBtnColor:  "#ffffff",
  };

  const filtered = emails
    .filter((e) => {
      const matchFilter = activeFilter === "All" || e.status === activeFilter;
      const q = search.trim().toLowerCase();
      const matchSearch = !q ||
        (e.subject || "").toLowerCase().includes(q) ||
        (e.sender_name || "").toLowerCase().includes(q) ||
        (e.sender_email || "").toLowerCase().includes(q);
      return matchFilter && matchSearch;
    })
    .sort((a, b) =>
      sort === "Newest"
        ? (b.gmail_id || "").localeCompare(a.gmail_id || "")
        : (a.gmail_id || "").localeCompare(b.gmail_id || "")
    );

  const allSelected = filtered.length > 0 && filtered.every((e) => selectedIds.has(e.gmail_id));
  const someSelected = selectedIds.size > 0;

  return (
    <div style={{ backgroundColor: t.cardBg, borderRadius: "14px", border: `1px solid ${t.border}`, transition: "background-color 0.2s, border-color 0.2s" }}>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "16px 20px", flexWrap: "wrap", borderBottom: `1px solid ${t.rowBorder}`, borderRadius: "14px 14px 0 0" }}>
        {/* Search */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 12px", borderRadius: "8px", border: `1px solid ${t.searchBorder}`, backgroundColor: t.searchBg, width: "220px" }}>
          <Search size={14} color={t.searchPlaceholder} strokeWidth={2} />
          <input
            type="text"
            placeholder="Search emails..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ border: "none", outline: "none", background: "transparent",
              fontSize: "13px", color: t.searchText, width: "100%" }}
          />
        </div>

        {/* Filter Tabs */}
        <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          {FILTERS.map((f) => (
            <button key={f} onClick={() => setActiveFilter(f)}
              style={{ padding: "6px 14px", borderRadius: "8px", fontSize: "13px",
                fontWeight: activeFilter === f ? 600 : 400,
                color: activeFilter === f ? t.filterActiveColor : t.filterColor,
                backgroundColor: activeFilter === f ? t.filterActiveBg : "transparent",
                border: "none", cursor: "pointer" }}>
              {f}
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {someSelected && (
          <button onClick={onProcessSelected}
            style={{ display: "flex", alignItems: "center", gap: "6px",
              padding: "7px 16px", borderRadius: "8px", fontSize: "13px", fontWeight: 600,
              backgroundColor: "#2563eb", color: "#fff", border: "none", cursor: "pointer" }}>
            Process Selected ({selectedIds.size})
          </button>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "13px", color: t.sortColor }}>
          <span>Sort:</span>
          <button onClick={() => setSort(sort === "Newest" ? "Oldest" : "Newest")}
            style={{ display: "flex", alignItems: "center", gap: "3px", fontWeight: 500,
              color: t.sortValueColor, background: "none", border: "none", cursor: "pointer", fontSize: "13px" }}>
            {sort}<ChevronDown size={13} strokeWidth={2} />
          </button>
        </div>
      </div>

      {/* Table */}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${t.rowBorder}` }}>
            <th style={{ padding: "10px 16px 10px 20px", width: "36px" }}>
              <input type="checkbox" checked={allSelected}
                onChange={(e) => onSelectAll(e.target.checked)}
                style={{ cursor: "pointer", accentColor: isDark ? "#60a5fa" : "#111827" }} />
            </th>
            {["Subject", "Sender", "Received", "Status", "Actions"].map((col) => (
              <th key={col} style={{ padding: "10px 20px", textAlign: "left",
                fontSize: "12px", fontWeight: 600, color: t.colHeadColor,
                letterSpacing: "0.4px", textTransform: "uppercase", whiteSpace: "nowrap" }}>
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr>
              <td colSpan={6} style={{ padding: "56px 20px", textAlign: "center", fontSize: "14px", color: t.emptyColor }}>
                {emails.length === 0
                  ? "No emails found. Click \"Refresh Inbox\" to load from Gmail."
                  : "No matching emails."}
              </td>
            </tr>
          ) : (
            filtered.map((email, idx) => {
              const isLast = idx === filtered.length - 1;
              const isChecked = selectedIds.has(email.gmail_id);
              const isProcessing = email.status === "Processing";
              return (
                <tr key={email.gmail_id}
                  style={{
                    borderBottom: isLast ? "none" : `1px solid ${t.rowBorder}`,
                    backgroundColor: isChecked ? t.rowChecked : "transparent",
                    transition: "background 0.1s",
                  }}
                  onMouseEnter={(e) => { if (!isChecked) e.currentTarget.style.backgroundColor = t.rowHover; }}
                  onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = isChecked ? t.rowChecked : "transparent"; }}
                >
                  <td style={{ padding: "14px 16px 14px 20px" }}>
                    <input type="checkbox" checked={isChecked}
                      onChange={(e) => onSelect(email.gmail_id, e.target.checked)}
                      style={{ cursor: "pointer", accentColor: isDark ? "#60a5fa" : "#111827" }} />
                  </td>

                  <td style={{ padding: "14px 20px", maxWidth: "320px" }}>
                    <div style={{ fontSize: "13.5px", fontWeight: 500, color: t.subjectColor,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={email.subject}>
                      {email.subject}
                    </div>
                    {email.snippet && (
                      <div style={{ fontSize: "12px", color: t.snippetColor, marginTop: "2px",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={email.snippet}>
                        {email.snippet}
                      </div>
                    )}
                  </td>

                  <td style={{ padding: "14px 20px", whiteSpace: "nowrap" }}>
                    <div style={{ fontSize: "13px", fontWeight: 500, color: t.senderName }}>
                      {email.sender_name || "—"}
                    </div>
                    <div style={{ fontSize: "12px", color: t.senderEmail, marginTop: "2px" }}>
                      {email.sender_email}
                    </div>
                  </td>

                  <td style={{ padding: "14px 20px", fontSize: "13px", color: t.dateColor, whiteSpace: "nowrap" }}>
                    {email.date ? email.date.replace(/\s+[+-]\d{4}.*$/, "").trim() : "—"}
                  </td>

                  <td style={{ padding: "14px 20px" }}>
                    <StatusBadge label={email.status} />
                  </td>

                  <td style={{ padding: "14px 20px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      {/* Eye / preview button */}
                      <button
                        onClick={() => setPreviewEmail(email)}
                        title="Preview email"
                        style={{
                          display: "flex", alignItems: "center", justifyContent: "center",
                          width: "28px", height: "28px", borderRadius: "6px",
                          border: `1px solid ${t.dotBtnBorder}`, backgroundColor: t.dotBtnBg,
                          cursor: "pointer", color: t.dotBtnColor, flexShrink: 0,
                        }}
                      >
                        <Eye size={14} strokeWidth={2} />
                      </button>

                      {isProcessing ? (
                        <div style={{ display: "flex", alignItems: "center", gap: "5px",
                          color: "#2563eb", fontSize: "12.5px", fontWeight: 500 }}>
                          <Loader2 size={13} strokeWidth={2.5} style={{ animation: "spin 1s linear infinite" }} />
                          Processing...
                        </div>
                      ) : (email.status === "Unprocessed" || email.status === "Failed") && (
                        <button
                          onClick={() => onProcess(email.gmail_id)}
                          style={{
                            display: "flex", alignItems: "center", gap: "5px",
                            padding: "6px 14px", borderRadius: "8px",
                            fontSize: "12.5px", fontWeight: 600,
                            backgroundColor: email.status === "Failed" ? "#fef2f2" : t.processBtnBg,
                            color: email.status === "Failed" ? "#dc2626" : t.processBtnColor,
                            border: email.status === "Failed" ? "1px solid #fecaca" : "none",
                            cursor: "pointer", whiteSpace: "nowrap",
                          }}>
                          {email.status === "Failed" ? "Retry" : "Process"}
                        </button>
                      )}


                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>

      {previewEmail && (
        <EmailPreviewModal
          email={previewEmail}
          onClose={() => setPreviewEmail(null)}
          isDark={isDark}
        />
      )}
    </div>
  );
}
