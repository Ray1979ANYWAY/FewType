/**
 * VoxEcho 前端根组件（Tauri 迁移阶段二）
 * 布局：固定 842×668 画布 + 左侧极简 Icon 侧边栏 + 右侧内容区
 * 右侧卷帘日志抽屉（Curtain Drawer）：absolute 覆盖不挤压主界面，
 * 顶部微型绿色箭头按钮展开/收起，日志轮询后端 /api/logs
 */
import { useEffect, useState } from "react";
import Sidebar, { ViewKey } from "./components/Sidebar";
import VoiceInput from "./components/VoiceInput";
import EbookReader from "./components/EbookReader";
import LongTextTTS from "./components/LongTextTTS";
import Settings from "./components/Settings";
import StyleManager from "./components/StyleManager";
import ProviderDialog from "./components/ProviderDialog";
import HotkeyDialog from "./components/HotkeyDialog";
import AboutDialog from "./components/AboutDialog";
import { resizeForTab, inTauri } from "./lib/window";
import { getLogs } from "./api";
import { useI18n } from "./i18n";

const VIEW_TITLES: Record<ViewKey, string> = {
  voice: "app.view_voice",
  ebook: "app.view_ebook",
  tts: "app.view_tts",
  settings: "app.view_settings",
};

export default function App() {
  const { t } = useI18n();
  const [view, setView] = useState<ViewKey>("voice");
  const [stylesOpen, setStylesOpen] = useState(false);
  /** 风格管理保存后 +1：通知 VoiceInput 重新加载风格列表 */
  const [stylesRev, setStylesRev] = useState(0);
  const [providerOpen, setProviderOpen] = useState(false);
  const [hotkeyOpen, setHotkeyOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);

  // 日志抽屉
  const [showLog, setShowLog] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  /** 切换 Tab：切换视图并同步调整窗口规格 */
  const handleChange = (v: ViewKey) => {
    setView(v);
    resizeForTab(v);
  };

  /** 日志卷帘：窗口向右扩展 320px（842→1162），高度锁死 668。
   *  main 固定 778px（842-64 侧边栏），与面板宽度完全解耦 → 动画期间内容零跳动 */
  const toggleLog = async () => {
    const next = !showLog;
    if (inTauri()) {
      try {
        const [{ getCurrentWindow }, { LogicalSize }] = await Promise.all([
          import("@tauri-apps/api/window"),
          import("@tauri-apps/api/dpi"),
        ]);
        // 先改窗口尺寸，再触发面板宽度动画，避免中途裁剪
        await getCurrentWindow().setSize(
          new LogicalSize(next ? 842 + 320 : 842, 668)
        );
      } catch (e) {
        console.warn("[log] 窗口尺寸调整失败", e);
      }
    }
    setShowLog(next);
  };

  /** 复制全部日志到剪贴板 */
  const copyLogs = async () => {
    try {
      await navigator.clipboard.writeText(logs.join("\n"));
    } catch (e) {
      console.warn("[log] 复制日志失败", e);
    }
  };

  // 启动时应用初始 Tab（语音输入）的窗口规格：默认 = 最小
  useEffect(() => {
    resizeForTab("voice");
  }, []);

  /** 窗口标题固定为应用名（不再显示宽高） */
  useEffect(() => {
    if (!inTauri()) return;
    let win: { setTitle: (title: string) => Promise<void> } | null = null;
    const updateTitle = () => {
      win?.setTitle(t("app.title"));
    };
    // 动态导入避免在非 Tauri 环境解析模块失败
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => {
      win = getCurrentWindow();
      updateTitle();
    });
    window.addEventListener("resize", updateTitle);
    return () => window.removeEventListener("resize", updateTitle);
  }, []);

  /** 启动时创建悬浮录音状态条窗口（隐藏，事件驱动显示） */
  useEffect(() => {
    if (!inTauri()) return;
    let cancelled = false;
    (async () => {
      try {
        const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
        const existing = await WebviewWindow.getByLabel("hud");
        if (existing || cancelled) return;
        await new WebviewWindow("hud", {
          url: "/#hud",
          title: "VoxEcho HUD",
          width: 500,
          height: 64,
          transparent: true,
          decorations: false,
          alwaysOnTop: true,
          skipTaskbar: true,
          resizable: false, // 禁止手动拖拽调大小；自适应高度由代码 setSize 控制（不受此限制）
          shadow: false,
          visible: false,
          focus: false,
        });
      } catch (e) {
        console.warn("[hud] 悬浮状态条创建失败", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** 日志抽屉：展开时轮询后端日志快照（2s），收起时停止 */
  useEffect(() => {
    if (!showLog) return;
    let alive = true;
    const pull = async () => {
      try {
        const lines = await getLogs();
        if (alive) setLogs(lines);
      } catch {
        /* 后端短暂不可用时静默，下一轮重试 */
      }
    };
    void pull();
    const timer = setInterval(pull, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [showLog]);

  return (
    <div className="relative m-0 flex h-full w-full select-none overflow-hidden rounded-xl border border-emerald-500/20 bg-bg text-text shadow-2xl">
      {/* 左侧 Icon 侧边栏 */}
      <Sidebar active={view} onChange={handleChange} />

      {/* 主面板区域：固定 778px（842-64 侧边栏），展开日志时尺寸零变化 */}
      <main className="flex h-full w-[778px] shrink-0 flex-col overflow-hidden p-6">
        {/* 顶部控制栏：标题 + 本地服务 + 微型绿色日志箭头 */}
        <div className="mb-4 flex shrink-0 items-center justify-between">
          <div className="flex items-center gap-2">
            {/* 自定义 Logo：Moss Black 品牌图标（VoxEcho-extension/icon） */}
            <img
              src="/voxecho-logo.svg"
              alt="VoxEcho"
              className="h-5 w-5 shrink-0 object-contain"
            />
            {/* VoxEcho 品牌名：香槟金渐变 */}
            <span className="bg-gradient-to-r from-amber-200 via-orange-100 to-amber-400 bg-clip-text text-[14.95px] font-bold tracking-tight text-transparent drop-shadow-[0_2px_10px_rgba(251,191,36,0.15)]">
              VoxEcho
            </span>
            <span className="text-[14.95px] font-bold tracking-wide text-text">
              {t("app.title_suffix")}
            </span>
            <span className="ml-1 rounded-md bg-input px-2 py-0.5 text-[11.5px] text-muted">
              {t(VIEW_TITLES[view])}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-[11.5px] text-muted">
              <span>{t("app.local_service")} 127.0.0.1:5010</span>
              <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_5px_#10B981]" />
            </div>

            {/* 微型低调绿色箭头 + Tooltip */}
            <div className="group relative">
              <button
                onClick={() => void toggleLog()}
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 transition-all hover:bg-emerald-500/20"
              >
                <span
                  className={`text-xs transition-transform duration-300 ${
                    showLog ? "rotate-90" : ""
                  }`}
                >
                  ‹
                </span>
              </button>
              <div className="pointer-events-none absolute right-0 top-9 z-50 whitespace-nowrap rounded bg-[#0C120E] px-2 py-1 text-[10px] text-emerald-300 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                {showLog ? t("app.log_close") : t("app.log_open")}
              </div>
            </div>
          </div>
        </div>

        {/* Tab 内容区 */}
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
          {view === "voice" ? (
            <VoiceInput
              onOpenStyles={() => setStylesOpen(true)}
              refreshKey={stylesRev}
            />
          ) : null}
          {view === "ebook" ? <EbookReader /> : null}
          {view === "tts" ? <LongTextTTS /> : null}
          {view === "settings" ? (
            <Settings
              onOpenProvider={() => setProviderOpen(true)}
              onOpenHotkey={() => setHotkeyOpen(true)}
              onOpenAbout={() => setAboutOpen(true)}
            />
          ) : null}
        </div>
      </main>

      {/* 全局弹窗 */}
      <StyleManager
        open={stylesOpen}
        onClose={() => setStylesOpen(false)}
        onSaved={() => {
          setStylesOpen(false);
          setStylesRev((r) => r + 1); // 风格列表变化 → 语音输入下拉框同步刷新
        }}
      />
      <ProviderDialog
        open={providerOpen}
        onClose={() => setProviderOpen(false)}
        onSaved={() => setProviderOpen(false)}
      />
      <HotkeyDialog open={hotkeyOpen} onClose={() => setHotkeyOpen(false)} />
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />

      {/* 右侧卷帘日志抽屉：窗口向右扩展 320px 时占新增区域，左侧内容零变化 */}
      <div
        className={`flex h-full shrink-0 flex-col overflow-hidden border-l border-emerald-500/20 bg-[#0C120E]/95 shadow-[-10px_0_30px_rgba(0,0,0,0.5)] backdrop-blur-xl transition-all duration-300 ease-out will-change-transform ${
          showLog ? "w-[320px] opacity-100" : "w-0 opacity-0"
        }`}
      >
        {/* 抽屉头部 */}
        <div className="flex shrink-0 items-center justify-between border-b border-emerald-500/10 px-4 py-3">
          <span className="font-mono text-xs font-bold tracking-wider text-emerald-400">
            › {t("app.log_title")}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => void copyLogs()}
              className="rounded px-1.5 py-0.5 text-xs text-emerald-500/60 transition-colors hover:text-emerald-400"
              title="复制全部日志"
            >
              复制
            </button>
            <button
              onClick={() => void toggleLog()}
              className="rounded px-1.5 py-0.5 text-xs text-emerald-500/60 transition-colors hover:text-emerald-400"
            >
              ✕
            </button>
          </div>
        </div>

        {/* 日志内容流：可划选复制 */}
        <div className="select-text flex-1 space-y-1.5 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-emerald-400/90 scrollbar-thin">
          {logs.length === 0 ? (
            <p className="text-emerald-500/40">{t("app.log_empty")}</p>
          ) : (
            logs.map((line, i) => <p key={i}>{line}</p>)
          )}
        </div>
      </div>
    </div>
  );
}
