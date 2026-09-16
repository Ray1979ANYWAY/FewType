/**
 * 更新弹窗（tauri-plugin-updater 一键更新）
 * - 启动时 App 检查到新版本后打开本弹窗
 * - 「立即更新」：下载安装包 → 显示进度 → 静默安装 → relaunch 重启
 * - 下载失败自动重试（最多 MAX_ATTEMPTS 次）；最终失败给出「重试」与「手动下载」兜底
 * - 非 Tauri 环境（浏览器 vite dev）不会触发（由调用方 inTauri 把关）
 */
import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Modal } from "./ui";
import { useI18n } from "../i18n";

type Phase = "idle" | "downloading" | "installing" | "error";

/** 下载失败自动重试次数 */
const MAX_ATTEMPTS = 3;
/** 两次尝试之间的间隔（毫秒） */
const RETRY_DELAY_MS = 2000;

/** 手动下载兜底地址（GitHub Releases 最新版） */
const MANUAL_URL = "https://github.com/Ray1979ANYWAY/FewType/releases/latest";

/** 当前版本安装包直链（弹窗内直接给出，用户无需搜索） */
function setupUrlFor(ver: string): string {
  return `https://github.com/Ray1979ANYWAY/FewType/releases/download/v${ver}/FewType_${ver}_x64-setup.exe`;
}

function fmtMB(n: number): string {
  return (n / (1024 * 1024)).toFixed(1);
}

export default function UpdaterDialog({
  open,
  version,
  onClose,
}: {
  open: boolean;
  /** 检测到的新版本号（如 3.1.17）；null 表示尚未检测 */
  version: string | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [downloaded, setDownloaded] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  /** 用系统浏览器打开指定地址 */
  const openUrl = async (url: string) => {
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open(url);
    } catch {
      // 打开失败静默——用户仍可复制链接
    }
  };

  const start = async () => {
    // 下载失败自动重试：网络波动（深圳直连 GitHub 下载不稳定）时显著提高成功率
    for (let i = 1; i <= MAX_ATTEMPTS; i++) {
      setAttempt(i);
      setPhase("downloading");
      setProgress(0);
      setDownloaded(0);
      setTotal(0);
      setError("");
      try {
        const { check } = await import("@tauri-apps/plugin-updater");
        const update = await check();
        if (!update) {
          // 检查期间版本已变（例如刚手动装好）——直接关闭
          setPhase("idle");
          onClose();
          return;
        }
        let ttl = 0;
        await update.download((event) => {
          if (event.event === "Started") {
            ttl = event.data.contentLength ?? 0;
            setTotal(ttl);
          } else if (event.event === "Progress") {
            // chunkLength 是本次分块大小 → 用累计值算真实进度
            setDownloaded((prev) => prev + (event.data.chunkLength ?? 0));
            if (ttl > 0) {
              setProgress(
                Math.min(Math.round(((event.data.chunkLength ?? 0) / ttl) * 100), 99)
              );
            }
          }
        });
        // 下载完成 → 先结束后端 sidecar（否则 NSIS 无法覆盖 fewtype-backend.exe，
        // 会弹「无法打开要写入的文件」中止更新）→ 再启动安装器
        setPhase("installing");
        await invoke("kill_backend");
        await update.install();
        return; // install() 在 Windows 上会启动安装器后退出进程，通常不会返回
      } catch (e) {
        if (i < MAX_ATTEMPTS) {
          // 短暂等待后进入下一次尝试
          await sleep(RETRY_DELAY_MS);
        } else {
          setPhase("error");
          setError(String(e instanceof Error ? e.message : e));
        }
      }
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
            <span className="text-right text-[11px] text-muted">
              {total > 0
                ? t("updater.download_mb", {
                    done: fmtMB(downloaded),
                    total: fmtMB(total),
                  })
                : `${fmtMB(downloaded)} MB`}
              {attempt > 1 &&
                ` · ${t("updater.attempt", { n: attempt, max: MAX_ATTEMPTS })}`}
            </span>
          </div>
        )}

        {phase === "installing" && (
          <p className="text-[13px] text-accent2">{t("updater.installing")}</p>
        )}

        {phase === "error" && (
          <div className="flex flex-col gap-2.5">
            <p className="text-[12.5px] leading-relaxed text-rose-400">
              {t("updater.error", { msg: error })}
            </p>
            {version && (
              <div className="flex flex-col gap-1 rounded-lg border border-border bg-input/60 px-2.5 py-2">
                <span className="text-[10.5px] uppercase tracking-wide text-muted">
                  {t("updater.direct_link")}
                </span>
                <button
                  type="button"
                  onClick={() => void openUrl(setupUrlFor(version))}
                  title={setupUrlFor(version)}
                  className="cursor-pointer break-all text-left text-[11.5px] leading-relaxed text-accent2 hover:underline"
                >
                  {setupUrlFor(version)}
                </button>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => void start()}
                className="rounded-lg border border-border bg-input px-3.5 py-1.5 text-[12.5px] text-text hover:border-accent"
              >
                {t("updater.retry")}
              </button>
              <button
                type="button"
                onClick={() => void openUrl(MANUAL_URL)}
                className="rounded-lg border border-accent/50 bg-accent/15 px-3.5 py-1.5 text-[12.5px] font-semibold text-accent2 hover:bg-accent/25"
              >
                {t("updater.manual_download")}
              </button>
            </div>
          </div>
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
