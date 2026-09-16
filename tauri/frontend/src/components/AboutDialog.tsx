/**
 * 关于弹窗（对应 Tk 版 open_about_dialog）
 * 版本 / GitHub / 赞助 / 技术栈
 * 外链通过 Tauri shell 插件在系统浏览器打开（Tauri 2 会拦截 <a target=_blank>）
 */
import { Github, Coffee } from "lucide-react";
import { Modal } from "./ui";
import { inTauri } from "../lib/window";
import { useI18n } from "../i18n";

const VERSION = "3.1.18";
const GITHUB_URL = "https://github.com/Ray1979ANYWAY/FewType";
const KO_FI_URL = "https://ko-fi.com/rayhu";

/** 在系统浏览器中打开外链（Tauri 内走 shell 插件，浏览器环境走 window.open） */
async function openExternal(url: string) {
  if (inTauri()) {
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open(url);
      return;
    } catch (e) {
      console.warn("[about] 打开外链失败，回退 window.open", e);
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export default function AboutDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Modal open={open} onClose={onClose} title={t("about.title")} width={380}>
      <div className="flex flex-col items-center gap-1 py-2 text-center">
        <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl border border-border bg-input text-[23px]">
          🎙️
        </div>
        <p className="text-[17.25px] font-bold text-text">{t("app.title")}</p>
        <p className="text-[12.65px] text-muted">v{VERSION}</p>
        <p className="mt-2 text-[12.65px] leading-relaxed text-mid">
          {t("about.subtitle")}
          <br />
          {t("about.made_by")}
        </p>
      </div>

      <div className="mt-4 flex flex-col gap-2">
        <button
          type="button"
          onClick={() => void openExternal(GITHUB_URL)}
          className="flex items-center justify-center gap-2 rounded-lg border border-border bg-input py-2 text-[13.8px] text-text hover:border-accent hover:text-accent2"
        >
          <Github size={13} />
          {t("about.github")}
        </button>
        <button
          type="button"
          onClick={() => void openExternal(KO_FI_URL)}
          className="flex items-center justify-center gap-2 rounded-lg border border-border bg-input py-2 text-[13.8px] text-text hover:border-accent hover:text-accent2"
        >
          <Coffee size={13} />
          {t("about.coffee")}
        </button>
      </div>

      <p className="mt-3 text-center text-[11.5px] text-muted">
        {t("about.footer")}
      </p>
    </Modal>
  );
}
