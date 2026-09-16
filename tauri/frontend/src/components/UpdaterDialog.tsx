/**
 * 更新弹窗（tauri-plugin-updater 一键更新）
 * - 启动时 App 检查到新版本后打开本弹窗
 * - 「立即更新」：下载安装包 → 显示进度 → 静默安装 → relaunch 重启
 * - 非 Tauri 环境（浏览器 vite dev）不会触发（由调用方 inTauri 把关）
 */
import { useState } from "react";
import { Modal } from "./ui";
import { useI18n } from "../i18n";

type Phase = "idle" | "downloading" | "installing" | "error";

export default function UpdaterDialog({
  open,
  version,
  onClose,
}: {
  open: boolean;
  /** 检测到的新版本号（如 3.1.15）；null 表示尚未检测 */
  version: string | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");

  const start = async () => {
    try {
      setPhase("downloading");
      setProgress(0);
      const { check } = await import("@tauri-apps/plugin-updater");
      const update = await check();
      if (!update) {
        // 检查期间版本已变（例如刚手动装好）——直接关闭
        setPhase("idle");
        onClose();
        return;
      }
      let total = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          if (total > 0) {
            setProgress(
              Math.min(Math.round(((event.data.chunkLength ?? 0) / total) * 100), 99)
            );
          }
        }
      });
      setPhase("installing");
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (e) {
      setPhase("error");
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const later = () => {
    setPhase("idle");
    setError("");
    onClose();
  };

  return (
    <Modal open={open} onClose={later} title={t("updater.title")} width={380}>
      <div className="flex flex-col gap-3 py-1">
        <p className="text-[13.8px] leading-relaxed text-mid">
          {t("updater.new_version", { ver: version ?? "?" })}
        </p>

        {phase === "downloading" && (
          <div className="flex flex-col gap-1.5">
            <div className="h-2 w-full overflow-hidden rounded-full border border-border bg-input">
              <div
                className="h-full rounded-full bg-accent transition-all duration-300"
                style={{ width: `${Math.max(progress, 2)}%` }}
              />
            </div>
            <span className="text-right text-[11.5px] text-muted">
              {t("updater.downloading", { p: progress })}
            </span>
          </div>
        )}

        {phase === "installing" && (
          <p className="text-[13px] text-accent2">{t("updater.installing")}</p>
        )}

        {phase === "error" && (
          <p className="text-[12.5px] leading-relaxed text-rose-400">
            {t("updater.error", { msg: error })}
            <br />
            <span className="text-muted">{t("updater.manual")}</span>
          </p>
        )}

        {phase === "idle" && (
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={later}
              className="rounded-lg border border-border bg-input px-4 py-1.5 text-[13px] text-text hover:border-accent"
            >
              {t("updater.later")}
            </button>
            <button
              type="button"
              onClick={() => void start()}
              className="rounded-lg border border-accent/50 bg-accent/15 px-4 py-1.5 text-[13px] font-semibold text-accent2 hover:bg-accent/25"
            >
              {t("updater.update_now")}
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}
