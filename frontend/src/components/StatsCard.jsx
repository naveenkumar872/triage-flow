import React from "react";

export default function StatsCard({
  title, value, icon: Icon, iconColor, iconBg,
  cardBg = "#ffffff", cardBorder = "#ebebeb",
  titleColor = "#6b7280", valueColor = "#111827",
}) {
  return (
    <div
      className="flex-1 rounded-xl px-6 py-5 flex flex-col justify-between"
      style={{
        backgroundColor: cardBg,
        border: `1px solid ${cardBorder}`,
        minWidth: "180px",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
        transition: "background-color 0.2s, border-color 0.2s",
      }}
    >
      <div className="flex items-start justify-between">
        <span
          style={{
            fontSize: "13px",
            fontWeight: 500,
            color: titleColor,
            letterSpacing: "0.1px",
          }}
        >
          {title}
        </span>
        <div
          className="flex items-center justify-center rounded-lg"
          style={{
            width: "34px",
            height: "34px",
            backgroundColor: iconBg,
            flexShrink: 0,
          }}
        >
          <Icon size={17} color={iconColor} strokeWidth={2} />
        </div>
      </div>
      <div
        style={{
          fontSize: "32px",
          fontWeight: 700,
          color: valueColor,
          lineHeight: 1.1,
          marginTop: "12px",
        }}
      >
        {value}
      </div>
    </div>
  );
}
