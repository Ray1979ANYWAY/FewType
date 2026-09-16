/**
 * 语音输入视图（场景 1）
 * 上屏走全局快捷键（不依赖本应用窗口），因此本版面只保留：
 *   快捷键说明 / 模式与风格 / 偏好开关 / 一个「重启服务」按钮（状态条卡死时用）
 */
import { useEffect, useRef, useState } from "react";
import { Mic, Pencil, RotateCcw } from "lucide-react";
import {
  SpeechClient,
  getConfig,
  updateConfig,
  SttStyle,
  API_BASE,
  type SpeechStartConfig,
} from "../api";
import { Card, CardTitle, Segmented, Select, EditableSelect, Switch, Btn } from "./ui";
import { useI18n } from "../i18n";

const MODES = [
  { value: "verbatim", labelKey: "voice.mode_verbatim" },
  { value: "fluent", labelKey: "voice.mode_fluent" },
  { value: "formal", labelKey: "voice.mode_formal" },
  { value: "custom", labelKey: "voice.mode_custom" },
] as const;

const MODE_DESC_KEYS: Record<string, string> = {
  verbatim: "voice.desc_verbatim",
  fluent: "voice.desc_fluent",
  formal: "voice.desc_formal",
  custom: "voice.desc_custom",
};

/** 翻译目标语言：value 为后端 LLM 直接理解的自然语言名；label 随界面语言显示 */
const TARGET_LANGS: { value: string; zh: string; hant: string; en: string }[] = [
  { value: "简体中文", zh: "简体中文", hant: "简体中文", en: "Simplified Chinese" },
  { value: "繁體中文", zh: "繁體中文", hant: "繁體中文", en: "Traditional Chinese" },
  { value: "English", zh: "English", hant: "English", en: "English" },
  { value: "西班牙语", zh: "西班牙语", hant: "西班牙語", en: "Spanish" },
  { value: "法语", zh: "法语", hant: "法語", en: "French" },
  { value: "德语", zh: "德语", hant: "德語", en: "German" },
  { value: "日本語", zh: "日本語", hant: "日本語", en: "Japanese" },
  { value: "한국어", zh: "한국어", hant: "한국어", en: "Korean" },
];

/** 按界面语言取目标语言显示名 */
function targetLangLabel(lang: string, o: { value: string; zh: string; hant: string; en: string }): string {
  if (lang === "zh-TW") return o.hant;
  if (lang === "en-US") return o.en;
  return o.zh;
}

/** 快捷键显示：double_ctrl → 双击 Ctrl；alt+win → ALT + WIN */
function hotkeyDisplay(combo: string): string {
  if ((combo || "").trim().toLowerCase() === "double_ctrl") return "double_ctrl"; // 由调用方 t() 翻译
  const names: Record<string, string> = {
    ctrl: "CTRL",
    alt: "ALT",
    win: "WIN",
    shift: "SHIFT",
    space: "Space",
  };
  return (combo || "ctrl+win")
    .split("+")
    .filter(Boolean)
    .map((p) => names[p.trim().toLowerCase()] ?? p.trim().toUpperCase())
    .join(" + ");
}

export default function VoiceInput({
  onOpenStyles,
  refreshKey = 0,
}: {
  onOpenStyles: () => void;
  /** 风格管理弹窗保存后 +1，触发重新加载配置（新增/重命名风格同步到下拉框） */
  refreshKey?: number;
}) {
  const { t, lang } = useI18n();
  const [mode, setMode] = useState<SpeechStartConfig["mode"]>("verbatim");
  const [customStyle, setCustomStyle] = useState("");
  const [styles, setStyles] = useState<SttStyle[]>([]);
  const [autoCommit, setAutoCommit] = useState(true);
  const [translate, setTranslate] = useState(false);
  const [targetLang, setTargetLang] = useState("简体中文");
  const [hotkeyText, setHotkeyText] = useState("");
  const [connected, setConnected] = useState(false);
  const [restarting, setRestarting] = useState(false);

  const clientRef = useRef<SpeechClient | null>(null);

  // 加载配置（风格列表 / 快捷键 / 模式 / 风格 / 自动上屏开关）
  useEffect(() => {
    let disposed = false;
    getConfig()
      .then((cfg) => {
        if (disposed) return;
        const styleList = (cfg.stt_styles ?? []).map((s) => ({ ...s }));
        setStyles(styleList);
        setHotkeyText(hotkeyDisplay(cfg.stt_hotkey || "ctrl+win"));
        setAutoCommit(cfg.stt_auto_commit ?? true);
        setMode((cfg.stt_mode as SpeechStartConfig["mode"]) ?? "verbatim");
        setTranslate(cfg.stt_translate ?? false);
        setTargetLang(cfg.stt_target_lang ?? "简体中文");
        // 自愈：当前选中名不在风格列表里（被重命名/删除）→ 自动选第一个并写回，
        // 保证下拉框与 stt_custom_style 永远一致
        const cur = cfg.stt_custom_style ?? "";
        const names = styleList.map((s) => s.name);
        if (cur && names.includes(cur)) {
          setCustomStyle(cur);
        } else if (styleList.length > 0 && styleList[0].name) {
          setCustomStyle(styleList[0].name);
          updateConfig({ stt_custom_style: styleList[0].name }).catch(() => {});
        } else {
          setCustomStyle(cur);
        }
      })
      .catch(() => {
        if (disposed) return;
        // 配置加载失败时也显示默认快捷键，避免 kbd 处留空
        setHotkeyText(hotkeyDisplay("ctrl+win"));
      });
    return () => {
      disposed = true;
    };
  }, [refreshKey]);

  // 保持 WS 连接：仅用于显示后端在线状态（语音功能本身走全局热键）
  useEffect(() => {
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      const client = new SpeechClient({
        onOpen: () => {
          if (disposed) return;
          setConnected(true);
        },
        onClose: () => {
          if (disposed) return;
          setConnected(false);
          retryTimer = setTimeout(connect, 2000);
        },
        onEvent: () => {
          /* 语音事件由悬浮状态条（HUD）呈现，本版面不再展示 */
        },
      });
      clientRef.current = client;
      client.connect().catch(() => {
        /* onClose 里重连 */
      });
    };
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      clientRef.current?.close();
      clientRef.current = null;
    };
  }, []);

  /** 重启语音服务：状态条（HUD）假死卡住时点击 */
  const restartService = async () => {
    if (restarting) return;
    setRestarting(true);
    try {
      await fetch(`${API_BASE}/api/speech/restart`, { method: "POST" });
      // 后端已广播 idle，HUD 会回到就绪并淡出
    } catch {
      /* 后端未就绪时静默 */
    } finally {
      setTimeout(() => setRestarting(false), 800);
    }
  };

  // 硬件麦克风检测：无麦克风 → 图标变黄并提示
  // WebView2 的 devicechange 在禁用/启用设备时不触发，故用 3s 轮询 + 事件双保险
  const [micAvailable, setMicAvailable] = useState<boolean | null>(null);
  useEffect(() => {
    const md = navigator.mediaDevices as MediaDevices | undefined;
    if (!md?.enumerateDevices) return;
    const update = () => {
      md.enumerateDevices()
        .then((devices) =>
          setMicAvailable(devices.some((d) => d.kind === "audioinput"))
        )
        .catch(() => setMicAvailable(null));
    };
    update();
    md.addEventListener?.("devicechange", update);
    const timer = window.setInterval(update, 3000);
    return () => {
      md.removeEventListener?.("devicechange", update);
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* 卡片一：快捷键与触发 */}
      <Card>
        <CardTitle>
          <Mic
            size={15}
            aria-label={micAvailable === false ? t("voice.no_mic_title") : undefined}
            className={
              micAvailable === false ? "text-warn" : "text-accent2"
            }
          />
          {t("voice.hint")}
          {micAvailable === false ? (
            <span className="ml-1.5 rounded bg-warn/10 px-1.5 py-0.5 text-[11px] font-normal text-warn">
              {t("voice.no_mic")}
            </span>
          ) : null}
        </CardTitle>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <kbd className="rounded-lg border border-border bg-input px-2.5 py-1 text-[12.65px] font-bold text-accent2">
              {hotkeyText === "double_ctrl" ? t("voice.double_ctrl") : hotkeyText}
            </kbd>
            <span className="text-[13.8px] text-mid">
              {t("voice.hold_hint")}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[11.5px]">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  connected ? "bg-accent shadow-[0_0_5px_var(--color-accent)]" : "bg-warn"
                }`}
              />
              <span className={connected ? "text-accent2" : "text-warn"}>
                {connected ? t("voice.online") : t("voice.offline")}
              </span>
            </span>
            <button
              type="button"
              onClick={restartService}
              disabled={restarting}
              title={t("voice.restart_title")}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-input px-2.5 py-1.5 text-[12.65px] text-mid transition-colors hover:border-accent hover:text-accent2 disabled:opacity-50"
            >
              <RotateCcw
                size={12}
                className={restarting ? "animate-spin" : ""}
              />
              {t("voice.restart")}
            </button>
          </div>
        </div>
      </Card>

      {/* 卡片二：模式 + 风格 */}
      <Card>
        <Segmented
          value={mode ?? "verbatim"}
          onChange={(v) => {
            setMode(v);
            // 同步到后端配置：热键触发转写时按此模式处理
            updateConfig({ stt_mode: v }).catch(() => {});
          }}
          items={MODES.map((m) => ({ value: m.value, label: t(m.labelKey) }))}
        />
        <div className="mt-3 flex items-center gap-2">
          <Select
            value={customStyle}
            onChange={(v) => {
              setCustomStyle(v);
              // 同步到后端配置：热键触发时按此风格名取 Prompt
              updateConfig({ stt_custom_style: v }).catch(() => {});
            }}
            placeholder={t("voice.style_placeholder")}
            options={styles.map((s) => ({ value: s.name, label: s.name }))}
            disabled={mode !== "custom"}
          />
          <Btn onClick={onOpenStyles}>
            <Pencil size={12} />
            {t("voice.manage_styles")}
          </Btn>
        </div>
        <p className="mt-2 text-[12.65px] text-muted">
          {t(MODE_DESC_KEYS[mode ?? "verbatim"])}
        </p>
      </Card>

      {/* 卡片三：偏好开关 */}
      <Card className="flex items-center justify-between">
        <Switch
          checked={autoCommit}
          onChange={(v) => {
            setAutoCommit(v);
            // 同步到后端配置：热键触发时是否自动上屏（Ctrl+V 粘贴到光标处）
            updateConfig({ stt_auto_commit: v }).catch(() => {});
          }}
          label={t("voice.auto_commit")}
        />
        <div className="flex items-center gap-3">
          <Switch
            checked={translate}
            onChange={(v) => {
              setTranslate(v);
              updateConfig({ stt_translate: v }).catch(() => {});
            }}
            label={t("voice.auto_translate")}
          />
          <EditableSelect
            value={targetLang}
            onChange={(v) => {
              setTargetLang(v);
              updateConfig({ stt_target_lang: v }).catch(() => {});
            }}
            options={TARGET_LANGS.map((l) => ({ value: l.value, label: targetLangLabel(lang, l) }))}
            className="w-[168px]"
            editable={false}
          />
        </div>
      </Card>

      {/* 模式说明注释 */}
      <p className="text-[11.5px] text-muted">{t("voice.mode_note")}</p>
    </div>
  );
}
