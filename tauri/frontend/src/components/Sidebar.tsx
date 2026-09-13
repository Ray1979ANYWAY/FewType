/**
 * 左侧极简 Icon 侧边栏（Spokenly 风格）
 * 语音输入 / 电子书朗读 / 长文本 TTS / 设置
 */
import React from "react";
import { Mic, BookOpen, AudioLines, Settings } from "lucide-react";
import { useI18n } from "../i18n";

export type ViewKey = "voice" | "ebook" | "tts" | "settings";

const NAV_KEYS: { key: ViewKey; icon: React.ReactNode; labelKey: string }[] = [
  { key: "voice", icon: <Mic size={18} />, labelKey: "app.view_voice" },
  { key: "ebook", icon: <BookOpen size={18} />, labelKey: "app.view_ebook" },
  { key: "tts", icon: <AudioLines size={18} />, labelKey: "app.view_tts" },
  { key: "settings", icon: <Settings size={18} />, labelKey: "app.view_settings" },
];

export default function Sidebar({
  active,
  onChange,
}: {
  active: ViewKey;
  onChange: (v: ViewKey) => void;
}) {
  const { t } = useI18n();
  const NAV = NAV_KEYS.map((item) => ({ ...item, label: t(item.labelKey) }));
  return (
    <aside className="flex w-[64px] shrink-0 flex-col items-center border-r border-border bg-card/60 py-4">
      {/* 导航图标 */}
      <nav className="flex flex-1 flex-col gap-1.5">
        {NAV.map((item) => {
          const isActive = active === item.key;
          return (
            <button
              key={item.key}
              type="button"
              title={item.label}
              onClick={() => onChange(item.key)}
              className={`relative flex h-10 w-10 items-center justify-center rounded-xl transition-all duration-150 ${
                isActive
                  ? "bg-accent/15 text-accent2"
                  : "text-mid hover:bg-input hover:text-text"
              }`}
            >
              {item.icon}
              {isActive ? (
                <span className="absolute -left-[13px] h-5 w-[3px] rounded-full bg-accent shadow-[0_0_8px_#10B981]" />
              ) : null}
            </button>
          );
        })}
      </nav>

      {/* 底部版本 */}
      <div className="flex flex-col items-center gap-1 text-[10.35px] text-muted/60">
        <span>VoxEcho</span>
        <span>V3.1.1</span>
      </div>
    </aside>
  );
}
