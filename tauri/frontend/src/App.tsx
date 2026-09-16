/**
 * FewType 前端根组件（Tauri 迁移阶段二）
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
import UpdaterDialog from "./components/UpdaterDialog";
import { resizeForTab, inTauri } from "./lib/window";
import { getLogs, SpeechClient } from "./api";
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
  /** Provider 弹窗自动弹出时的原因提示（tooltip 显示数秒后消失） */
  const [providerReason, setProviderReason] = useState<string | null>(null);
  const [hotkeyOpen, setHotkeyOpen] = useState(false);
  /** 快捷键保存后 +1：通知 VoiceInput / Settings 重新加载快捷键显示 */
  const [hotkeyRev, setHotkeyRev] = useState(0);
  const [aboutOpen, setAboutOpen] = useState(false);

  // 自动更新：启动延迟检查 GitHub Releases，发现新版本弹窗
  const [updateOpen, setUpdateOpen] = useState(false);
  const [updateVersion, setUpdateVersion] = useState<string | null>(null);

  /** 检查更新（手动/自动共用）：命中新版本 → 打开更新弹窗并返回 true；无更新/失败 → false。
   *  手动按钮据此显示「已是最新版本」，自动检查据此静默。 */
  const checkForUpdate = async (): Promise<boolean> => {
    if (!inTauri()) return false;
    try {
      const { check } = await import("@tauri-apps/plugin-updater");
      const update = await check();
      if (update) {
        setUpdateVersion(update.version);
        setUpdateOpen(true);
        return true;
      }
      return false;
    } catch {
      return false;
    }
  };

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
  // 启动 4 秒后静默检查更新：命中新版本弹窗提醒；任何异常（离线/未配置）静默跳过
  useEffect(() => {
    if (!inTauri()) return;
    const timer = window.setTimeout(() => void checkForUpdate(), 4000);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 全局监听后端事件：热键触发但 Provider 未配置时，后端广播 open_settings → 弹出 Provider 设置
  useEffect(() => {
    if (!inTauri()) return;
    let disposed = false;
    const client = new SpeechClient({
      onEvent: (ev) => {
        if (disposed) return;
        if (ev.type === "open_settings") {
          setProviderReason(t("provider.notice_missing_key"));
          setProviderOpen(true);
          // 用户此刻在其他应用里（热键触发），必须把主窗口强制置顶 + 聚焦，
          // 否则 Provider 设置窗在后台弹出，用户看不到还在傻等。
          // 用 setAlwaysOnTop 而非仅 setFocus：Windows 前台锁定规则不允许后台进程
          // 的窗口抢前台（按键被后端钩子接收，前端进程没有前台权利），setFocus 会被静默拒绝。
          (async () => {
            try {
              const { getCurrentWindow } = await import("@tauri-apps/api/window");
              const win = getCurrentWindow();
              await win.unminimize();
              await win.show();
              await win.setAlwaysOnTop(true);
              await win.setFocus();
            } catch (e) {
              console.warn("[provider] 窗口置顶失败", e);
            }
          })();
        }
      },
    });
    client.connect().catch(() => {});
    return () => {
      disposed = true;
      client.close();
    };
  }, []);

  const toggleLog = async () => {
    const next = !showLog;
    // 日志抽屉：窗口向右扩展 320px（842→1162），抽屉占新增区域，面板内容零跳动
    if (inTauri()) {
      try {
        const [{ getCurrentWindow }, { LogicalSize }] = await Promise.all([
          import("@tauri-apps/api/window"),
          import("@tauri-apps/api/dpi"),
        ]);
        await getCurrentWindow().setSize(
          new LogicalSize(next ? 842 + 320 : 842, 668)
        );
      } catch (e) {
        console.warn("[log] 窗口尺寸调整失败", e);
      }
    }
    setShowLog(next);
  };

  /** 自定义标题栏：最小化窗口 */
  const windowMinimize = async () => {
    if (!inTauri()) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().minimize();
    } catch (e) {
      console.warn("[win] 最小化失败", e);
    }
  };

  /** 自定义标题栏：关闭窗口（隐藏到托盘，进程常驻，托盘/单实例可恢复） */
  const windowClose = async () => {
    if (!inTauri()) return;
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().close();
    } catch (e) {
      console.warn("[win] 关闭失败", e);
    }
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
          title: "FewType HUD",
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

  /** 面板空白处拖拽窗口：无边框窗口下，任何非交互区域的 mousedown 都开始拖动。
   *  控件（按钮/输入框/下拉/链接/可编辑区/[data-no-drag]）一律排除，滚动用滚轮。 */
  const onPanelMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const t = e.target as HTMLElement;
    if (
      t.closest(
        "button, input, textarea, select, a, [contenteditable='true'], [data-no-drag]"
      )
    ) {
      return;
    }
    if (!inTauri()) return;
    import("@tauri-apps/api/window")
      .then(({ getCurrentWindow }) => getCurrentWindow().startDragging())
      .catch((err) => console.warn("[win] 拖拽失败", err));
  };

  return (
    /* 外层：黑绿基底（不透明，彻底规避 Windows 透明窗口的白角/灰框 Bug）。
       窗口无边框，拖拽由 onMouseDownCapture 统一处理（控件自动排除，
       不依赖 data-tauri-drag-region 属性，避免内层所有子元素被拖拽捕获）。 */
    <div
      className="relative m-0 flex h-full w-full select-none flex-col overflow-hidden rounded-xl border border-emerald-500/20 bg-bg text-text shadow-2xl"
      onMouseDownCapture={onPanelMouseDown}
    >
      {/* 顶部 Topbar：品牌区 + 右侧两行（上行 = 最小化/关闭，下行 = 日志箭头贴右）。
          独立于内容区（加高 h-16），无边框窗口下整行空白可拖拽 */}
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-emerald-500/10 bg-bg px-6">
        <div className="flex items-center gap-2 self-end pb-1.5">
          {/* 自定义 Logo：Moss Black 品牌图标（FewType-extension/icon） */}
          <img
            src="/fewtype-logo.svg"
            alt="FewType"
            className="h-5 w-5 shrink-0 object-contain"
          />
          {/* FewType 品牌名：香槟金渐变 */}
          <span className="bg-gradient-to-r from-amber-200 via-orange-100 to-amber-400 bg-clip-text text-[14.95px] font-bold tracking-tight text-transparent drop-shadow-[0_2px_10px_rgba(251,191,36,0.15)]">
            FewType
          </span>
          <span className="text-[14.95px] font-bold tracking-wide text-text">
            {t("app.title_suffix")}
          </span>
          <span className="ml-1 rounded-md bg-input px-2 py-0.5 text-[11.5px] text-muted">
            {t(VIEW_TITLES[view])}
          </span>
          {/* 本地服务状态：低调小字 */}
          <div className="ml-3 flex items-center gap-2 text-[11px] text-muted">
            <span>{t("app.local_service")} 127.0.0.1:5010</span>
            <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_5px_#10B981]" />
          </div>
        </div>

        {/* 右侧两行控制区：控制按钮在日志箭头上方，箭头始终贴右缘 */}
        <div className="flex flex-col items-end justify-center gap-0.5">
          {/* 上行：最小化 + 关闭 */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => void windowMinimize()}
              className="flex h-5 w-5 cursor-pointer items-center justify-center rounded text-emerald-400/80 transition-all hover:bg-emerald-500/20 hover:text-emerald-300"
              title={t("app.window_minimize")}
            >
              <svg
                className="h-3 w-3"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M5 12h14" />
              </svg>
            </button>
            <button
              onClick={() => void windowClose()}
              className="flex h-5 w-5 cursor-pointer items-center justify-center rounded text-rose-400/80 transition-all hover:bg-rose-500/20 hover:text-rose-300"
              title={t("app.window_close")}
            >
              <svg
                className="h-3 w-3"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
          {/* 下行：微型低调绿色箭头 + Tooltip（贴右） */}
          <div className="group relative">
            <button
              onClick={() => void toggleLog()}
              className="flex h-5 w-5 cursor-pointer items-center justify-center rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 transition-all hover:bg-emerald-500/20"
            >
              <span
                className={`text-[10px] leading-none transition-transform duration-300 ${
                  showLog ? "rotate-90" : ""
                }`}
              >
                ‹
              </span>
            </button>
            <div className="pointer-events-none absolute right-0 top-7 z-50 whitespace-nowrap rounded bg-[#0C120E] px-2 py-1 text-[10px] text-emerald-300 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
              {showLog ? t("app.log_close") : t("app.log_open")}
            </div>
          </div>
        </div>
      </header>

      {/* 主体行：左侧 Icon 侧边栏 + 主面板 + 右侧日志抽屉（展开时占新增 320px） */}
      <div className="flex min-h-0 flex-1">
        <Sidebar active={view} onChange={handleChange} />

        {/* 主面板区域：固定 778px（842-64 侧边栏），展开日志时尺寸零变化 */}
        <main className="flex h-full w-[778px] shrink-0 flex-col overflow-hidden px-6 pb-6 pt-4">
          {/* Tab 内容区：滚动容器，不参与窗口拖拽（保留滚动条/控件交互） */}
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin" data-no-drag>
            {view === "voice" ? (
              <VoiceInput
                onOpenStyles={() => setStylesOpen(true)}
                refreshKey={stylesRev + hotkeyRev}
              />
            ) : null}
            {view === "ebook" ? <EbookReader /> : null}
            {view === "tts" ? (
              <LongTextTTS
                onOpenProvider={() => {
                  setProviderReason(t("provider.notice_missing_key"));
                  setProviderOpen(true);
                }}
              />
            ) : null}
            {view === "settings" ? (
              <Settings
                onOpenProvider={() => {
                  setProviderReason(null);
                  setProviderOpen(true);
                }}
                onOpenHotkey={() => setHotkeyOpen(true)}
                onOpenAbout={() => setAboutOpen(true)}
                onCheckUpdate={() => checkForUpdate()}
                refreshKey={hotkeyRev}
              />
            ) : null}
          </div>
        </main>

      {/* 右侧日志抽屉：窗口向右扩展 320px，抽屉占新增区域（面板零跳动） */}
      <div
        className={`flex h-full w-[320px] shrink-0 flex-col overflow-hidden border-l border-emerald-500/20 bg-[#0C120E]/95 shadow-[-10px_0_30px_rgba(0,0,0,0.5)] backdrop-blur-xl transition-all duration-300 ease-out will-change-transform ${
          showLog ? "opacity-100" : "w-0 opacity-0"
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

        {/* 日志内容流：可划选复制（滚动容器，不参与窗口拖拽） */}
        <div className="select-text flex-1 space-y-1.5 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-emerald-400/90 scrollbar-thin" data-no-drag>
          {logs.length === 0 ? (
            <p className="text-emerald-500/40">{t("app.log_empty")}</p>
          ) : (
            logs.map((line, i) => <p key={i}>{line}</p>)
          )}
        </div>
      </div>
      </div>

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
        notice={providerReason}
        onClose={() => {
          setProviderOpen(false);
          // 解除热键触发时的强制置顶，恢复正常窗口层级
          (async () => {
            try {
              const { getCurrentWindow } = await import("@tauri-apps/api/window");
              await getCurrentWindow().setAlwaysOnTop(false);
            } catch {
              /* 忽略 */
            }
          })();
        }}
        onSaved={() => {
          setProviderOpen(false);
          (async () => {
            try {
              const { getCurrentWindow } = await import("@tauri-apps/api/window");
              await getCurrentWindow().setAlwaysOnTop(false);
            } catch {
              /* 忽略 */
            }
          })();
        }}
      />
      <HotkeyDialog
        open={hotkeyOpen}
        onClose={() => setHotkeyOpen(false)}
        onSaved={() => setHotkeyRev((r) => r + 1)}
      />
      <AboutDialog open={aboutOpen} onClose={() => setAboutOpen(false)} />
      <UpdaterDialog
        open={updateOpen}
        version={updateVersion}
        onClose={() => setUpdateOpen(false)}
      />
    </div>
  );
}
