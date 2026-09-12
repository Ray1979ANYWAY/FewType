/**
 * 电子书朗读视图（场景 2）
 * 卡片一：Chrome 扩展服务桥接状态
 * 卡片二：快速使用指南
 */
import { useEffect, useState } from "react";
import { Puzzle, Zap, BookOpen } from "lucide-react";
import { getExtensionStatus } from "../api";
import { Card, CardTitle, Btn, StatusDot } from "./ui";
import { useI18n } from "../i18n";

export default function EbookReader() {
  const { t } = useI18n();
  const [online, setOnline] = useState<boolean | null>(null);

  const PLATFORMS = [t("ebook.platform_google"), t("ebook.platform_koodo")];

  const GUIDE = [
    t("ebook.guide_1"),
    t("ebook.guide_2"),
    t("ebook.guide_3"),
  ];

  useEffect(() => {
    let disposed = false;
    const poll = async () => {
      try {
        const st = await getExtensionStatus();
        if (!disposed) setOnline(st.online);
      } catch {
        if (!disposed) setOnline(false);
      }
    };
    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* 卡片一：实时服务桥接 */}
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <StatusDot ok={online !== false} />
            <span className="text-[14.95px] font-bold text-accent2">
              {online === null
                ? t("ebook.checking")
                : online
                  ? t("ebook.ready")
                  : t("ebook.load")}
            </span>
          </div>
          <Btn
            variant="ghost"
            onClick={() => {
              // Tauri 壳内打开 chrome://extensions（生产环境由 Tauri 处理）
              window.open("chrome://extensions", "_blank");
            }}
          >
            <Puzzle size={13} />
            {t("ebook.btn")}
          </Btn>
        </div>
        <p className="mt-3 text-[12.65px] text-mid">
          {t("ebook.support")} {PLATFORMS.join(" / ")}
        </p>
        {online === false ? (
          <p className="mt-1.5 text-[12.65px] text-warn">
            {t("ebook.missing")}
          </p>
        ) : null}
      </Card>

      {/* 卡片二：快速使用指南 */}
      <Card>
        <CardTitle>
          <Zap size={14} className="text-accent2" />
          {t("ebook.guide_title")}
        </CardTitle>
        <div className="flex flex-col gap-2">
          {GUIDE.map((line, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-lg bg-input/60 px-3 py-2 text-[13.8px] text-mid"
            >
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[11.5px] font-bold text-accent2">
                {i + 1}
              </span>
              {line}
            </div>
          ))}
        </div>
      </Card>

      {/* 平台说明（支持 Google Play Books / Koodo Reader） */}
      <Card className="flex items-center gap-2 text-[12.65px] text-muted">
        <BookOpen size={13} className="text-accent2/70" />
        {t("ebook.footer")}
      </Card>
    </div>
  );
}
