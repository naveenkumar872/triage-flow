import React from "react";
import { Mail, BarChart2, Activity, Settings, Sun, Moon } from "lucide-react";

const navItems = [
  { icon: Mail,      label: "Inbox",     id: "inbox"     },
  { icon: BarChart2, label: "Analytics", id: "analytics" },
  { icon: Activity,  label: "Monitor",   id: "monitor"   },
];

export default function Sidebar({ activeView, onNavigate, isDark, onThemeToggle }) {
  const t = isDark
    ? {
        bg:          "#1e293b",
        border:      "#334155",
        activeBg:    "#334155",
        hoverBg:     "#273448",
        iconActive:  "#f1f5f9",
        iconMuted:   "#64748b",
        labelActive: "#f1f5f9",
        labelMuted:  "#64748b",
        subtitle:    "#64748b",
      }
    : {
        bg:          "#ffffff",
        border:      "#e5e7eb",
        activeBg:    "#f3f4f6",
        hoverBg:     "#f9fafb",
        iconActive:  "#111827",
        iconMuted:   "#9ca3af",
        labelActive: "#111827",
        labelMuted:  "#9ca3af",
        subtitle:    "#6b7280",
      };

  return (
    <div
      className="flex flex-col items-center py-5"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        width: "68px",
        height: "100vh",
        backgroundColor: t.bg,
        borderRight: `1px solid ${t.border}`,
        zIndex: 100,
        overflowY: "auto",
        flexShrink: 0,
        transition: "background-color 0.2s, border-color 0.2s",
      }}
    >
      {/* Logo + Title */}
      <div className="flex flex-col items-center gap-1 mb-8">
        <div
          className="flex items-center justify-center rounded-lg font-bold text-white select-none"
          style={{
            width: "38px",
            height: "38px",
            backgroundColor: "#2563eb",
            fontSize: "11px",
            letterSpacing: "0.5px",
            borderRadius: "10px",
          }}
        >
          TF
        </div>
        <span
          style={{
            fontSize: "9px",
            fontWeight: 600,
            color: t.subtitle,
            letterSpacing: "0.4px",
            textTransform: "uppercase",
            textAlign: "center",
            lineHeight: 1.2,
          }}
        >
          Triage<br />Flow
        </span>
      </div>

      {/* Nav Items */}
      <nav className="flex flex-col gap-0.5 flex-1 w-full px-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className="flex flex-col items-center gap-1 py-3 rounded-xl w-full transition-all"
              style={{
                backgroundColor: isActive ? t.activeBg : "transparent",
                border: "1px solid transparent",
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.backgroundColor = t.hoverBg;
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.backgroundColor = "transparent";
              }}
            >
              <Icon
                size={17}
                color={isActive ? t.iconActive : t.iconMuted}
                strokeWidth={isActive ? 2 : 1.5}
              />
              <span
                style={{
                  fontSize: "9px",
                  color: isActive ? t.labelActive : t.labelMuted,
                  fontWeight: isActive ? 600 : 400,
                  letterSpacing: "0.3px",
                }}
              >
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>

      {/* Bottom controls: theme toggle → settings → avatar */}
      <div className="flex flex-col items-center gap-3 pb-2 w-full px-2">
        {/* Theme toggle */}
        <button
          title={isDark ? "Switch to light theme" : "Switch to dark theme"}
          onClick={onThemeToggle}
          className="flex flex-col items-center gap-1 py-2 rounded-xl w-full"
          style={{ background: "none", border: "none", cursor: "pointer" }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = t.hoverBg)}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
        >
          {isDark
            ? <Sun  size={17} color={t.iconMuted} strokeWidth={1.5} />
            : <Moon size={17} color={t.iconMuted} strokeWidth={1.5} />
          }
          <span style={{ fontSize: "9px", color: t.labelMuted, letterSpacing: "0.3px" }}>
            {isDark ? "Light" : "Dark"}
          </span>
        </button>

        {/* Settings */}
        <button
          className="flex flex-col items-center gap-1 py-2 rounded-xl w-full"
          style={{ background: "none", border: "none", cursor: "pointer" }}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = t.hoverBg)}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
        >
          <Settings size={17} color={t.iconMuted} strokeWidth={1.5} />
        </button>

        {/* Avatar */}
        <div
          className="flex items-center justify-center rounded-full font-semibold select-none"
          style={{
            width: "30px",
            height: "30px",
            backgroundColor: "#2563eb",
            color: "#ffffff",
            fontSize: "10px",
            letterSpacing: "0.3px",
          }}
        >
          AD
        </div>
      </div>
    </div>
  );
}
