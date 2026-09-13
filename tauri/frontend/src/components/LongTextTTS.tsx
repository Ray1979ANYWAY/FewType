/**
 * 长文本转语音视图（场景 3）
 * 完整迁移 Tk 版逻辑：
 * - 自动检测输入语言（启发式），与输出语言比对是否需要翻译
 * - 输入输出一致 → 直接生成/试听；不一致 → 必须先「预览文本」翻译确认
 * - 翻译/合成期间按钮禁用 + 状态栏提示，避免重复合成
 * - 输出文件夹：原生系统对话框选择 / 资源管理器打开，记住上次选择
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  FolderOpen,
  FolderInput,
  Play,
  Zap,
  Wand2,
  Maximize2,
  Minimize2,
} from "lucide-react";
import {
  getVoices,
  getConfig,
  getTtsDefaultDir,
  groupVoicesByLocale,
  ttsGenerate,
  ttsSpeak,
  ttsTranslate,
  voiceLabel,
  pickOutputDir,
  openOutputDir,
  Voice,
} from "../api";
import { Card, EditableSelect, Btn } from "./ui";
import { useI18n } from "../i18n";

/** 输出语言选项：value 固定为中文名（后端翻译 prompt 语义），label 按界面语言翻译 */
const OUTPUT_LANGS = [
  { value: "简体中文", zh: "简体中文", hant: "簡體中文", en: "Simplified Chinese" },
  { value: "繁體中文", zh: "繁體中文", hant: "繁體中文", en: "Traditional Chinese" },
  { value: "English", zh: "English", hant: "English", en: "English" },
  { value: "Español 西班牙语", zh: "Español 西班牙语", hant: "Español 西班牙語", en: "Spanish" },
  { value: "Français 法语", zh: "Français 法语", hant: "Français 法語", en: "French" },
  { value: "Deutsch 德语", zh: "Deutsch 德语", hant: "Deutsch 德語", en: "German" },
  { value: "日本語", zh: "日本語", hant: "日本語", en: "Japanese" },
  { value: "한국어", zh: "한국어", hant: "한국어", en: "Korean" },
];

/** 按界面语言取输出语言下拉的显示标签 */
function outputLangLabel(lang: string, o: (typeof OUTPUT_LANGS)[number]): string {
  if (lang === "en-US") return o.en;
  if (lang === "zh-TW") return o.hant;
  return o.zh;
}
/** 语速：5% 为阶梯（下拉建议，±30% 封顶），也支持手动输入任意数字 */
const RATES = [
  "-30%", "-25%", "-20%", "-15%", "-10%", "-5%", "+0%",
  "+5%", "+10%", "+15%", "+20%", "+25%", "+30%",
];
/** 音量：edge-tts prosody volume，+20% 为默认（后端合成默认值） */
const VOLUMES = ["+0%", "+10%", "+20%", "+30%", "+50%"];

/** 规范化语速输入：去 % → 钳制 ±100 → 带符号（如 15 → +15%） */
function normalizeRate(v: string): string {
  const n = parseInt(String(v).replace(/[^\d-]/g, ""), 10);
  if (Number.isNaN(n)) return "+0%";
  const clamped = Math.max(-100, Math.min(100, n));
  return clamped > 0 ? `+${clamped}%` : `${clamped}%`;
}

/** 规范化音量输入：去 % → 钳制 0-100 → 带符号（如 30 → +30%） */
function normalizeVolume(v: string): string {
  const n = parseInt(String(v).replace(/[^\d-]/g, ""), 10);
  if (Number.isNaN(n)) return "+20%";
  const clamped = Math.max(0, Math.min(100, n));
  return `+${clamped}%`;
}

/** 输出语言 → locale */
const TARGET_LOCALE: Record<string, string> = {
  "简体中文": "zh-CN",
  "繁體中文": "zh-TW",
  English: "en-US",
  "Español 西班牙语": "es-ES",
  "Français 法语": "fr-FR",
  "Deutsch 德语": "de-DE",
  日本語: "ja-JP",
  한국어: "ko-KR",
};

/** 常用繁体字样本（Tk 版同款启发式） */
const HANT_CHARS =
  "這個說時後來裡為與無沒這樣麼還讓過們對點嗎請開關體學書讀話語聽寫見車門風會愛國問題決發現認識覺應夠臺灣東興歡長";

/** 启发式语言检测：'zh' / 'zh-hant' / 'ja' / 'ko' / 'other'（拉丁等） */
function detectInputLang(text: string): string {
  if (/[\uAC00-\uD7A3]/.test(text)) return "ko";
  if (/[\u3040-\u30FF]/.test(text)) return "ja";
  const han = text.match(/[\u4E00-\u9FFF]/g) ?? [];
  if (han.length === 0 || han.length / Math.max(text.length, 1) < 0.2) return "other";
  const hant = han.filter((ch) => HANT_CHARS.includes(ch)).length;
  return hant / han.length > 0.1 ? "zh-hant" : "zh";
}

/** 是否需要翻译（拉丁语言之间不自动翻译，保守策略，同 Tk 版） */
function needsTranslation(text: string, outputLang: string): boolean {
  const loc = TARGET_LOCALE[outputLang] ?? "zh-CN";
  if (!text.trim()) return false;
  const inLang = detectInputLang(text);
  const outPrimary = loc.split("-", 1)[0].toLowerCase();
  if (inLang === "other") {
    return !["en", "es", "fr", "de"].includes(outPrimary);
  }
  if (inLang.startsWith("zh")) {
    if (outPrimary !== "zh") return true;
    const inHant = inLang === "zh-hant";
    const outHant = loc === "zh-TW" || loc === "zh-HK";
    return inHant !== outHant;
  }
  return inLang !== outPrimary;
}

/** 输出目录记忆（localStorage） */
const OUTPUT_DIR_KEY = "voxecho.tts.outputDir";
// 空字符串 = 未指定，使用后端默认（用户→文档→VoxEcho_tts_out）
const DEFAULT_DIR = "";
// 旧版本硬编码的默认目录：升级后视为"未指定"并清理
const LEGACY_DIR = "D:/Output";

function loadSavedDir(): string {
  try {
    const v = localStorage.getItem(OUTPUT_DIR_KEY);
    return v && v !== LEGACY_DIR ? v : DEFAULT_DIR;
  } catch {
    return DEFAULT_DIR;
  }
}

/** 界面语言 → 输出语言默认值 */
function langToOutputLang(l: string): string {
  if (l === "zh-TW") return "繁體中文";
  if (l === "en-US") return "English";
  return "简体中文";
}

/**
 * 可放大的文本编辑框：右下角 Maximize2 按钮 → 全屏沉浸式编辑弹窗。
 * 弹窗内直接编辑同一个受控 state（value/onChange），收起即同步回原文本框。
 */
function ExpandableTextarea({
  value,
  onChange,
  placeholder,
  className,
  title,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  title: string;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="relative">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={className}
      />
      {/* 右下角放大编辑按钮 */}
      <button
        type="button"
        onClick={() => setExpanded(true)}
        title={t("tts.expand_edit")}
        className="absolute right-3 bottom-3 z-10 rounded-md border border-emerald-500/20 bg-[#090D0A]/80 p-1.5 text-emerald-500/60 backdrop-blur-sm transition-all cursor-pointer hover:bg-emerald-500/20 hover:text-emerald-400"
      >
        <Maximize2 size={14} />
      </button>

      {/* 全屏沉浸式编辑弹窗（高透明黑底蒙版 + 居中弹窗） */}
      {expanded ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="flex h-[85%] w-[92%] max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-2xl">
            {/* 标题栏：左侧标题，右侧收起按钮 */}
            <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-4 py-2.5">
              <span className="truncate text-[14px] font-bold text-text">{title}</span>
              <button
                type="button"
                onClick={() => setExpanded(false)}
                title={t("tts.collapse")}
                className="rounded-md border border-emerald-500/20 p-1.5 text-emerald-500/70 transition-colors cursor-pointer hover:bg-emerald-500/20 hover:text-emerald-400"
              >
                <Minimize2 size={15} />
              </button>
            </div>
            {/* 大字号多行滚动编辑区 */}
            <textarea
              autoFocus
              value={value}
              onChange={(e) => onChange(e.target.value)}
              placeholder={placeholder}
              className="w-full flex-1 resize-none bg-transparent px-5 py-3 text-[16px] leading-relaxed text-text outline-none placeholder:text-muted"
            />
            {/* 底部操作栏：完成按钮 */}
            <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border/60 px-4 py-2.5">
              <span className="mr-auto truncate text-[11.5px] text-muted">
                {value.length} {t("tts.char_count")}
              </span>
              <Btn variant="primary" onClick={() => setExpanded(false)}>
                {t("tts.done")}
              </Btn>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 表单控件宽度（px）：按界面语言区分（英文标签更长，需要单独调整）。
 * 必须是静态 Tailwind 类名字面量（JIT 扫描源码生成，动态拼接无效）。 */
function formWidths(lang: string) {
  if (lang === "en-US") {
    return {
      lang: "w-[90px]",
      rate: "w-[80px]",
      volume: "w-[80px]",
      voice: "w-[186px]",
      cols: "grid-cols-[auto_90px_auto_80px_auto_80px_auto_186px]",
    };
  }
  return {
    lang: "w-[110px]",
    rate: "w-[80px]",
    volume: "w-[80px]",
    voice: "w-[192px]",
    cols: "grid-cols-[auto_110px_auto_80px_auto_80px_auto_192px]",
  };
}

export default function LongTextTTS({
  onOpenProvider,
}: {
  /** 需要 LLM 但 Provider 未配置时，通知上层弹出 Provider 设置 */
  onOpenProvider?: () => void;
}) {
  const { t, lang } = useI18n();
  const fw = formWidths(lang);
  const [text, setText] = useState("");
  const [outputLang, setOutputLang] = useState(() => langToOutputLang(lang));
  const [rate, setRate] = useState("+0%");
  const [volume, setVolume] = useState("+20%");
  const [voice, setVoice] = useState("");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceGroups, setVoiceGroups] = useState<Map<string, Voice[]>>(new Map());
  const [preview, setPreview] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false); // 翻译/试听/合成中：禁用按钮防重复
  const [outputDir, setOutputDir] = useState(loadSavedDir);
  const [defaultDir, setDefaultDir] = useState(""); // 后端默认目录（用户→文档→VoxEcho_tts_out）
  const [lastPath, setLastPath] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const outputLangTouched = useRef(false); // 用户手动改过输出语言后不再自动跟随界面语言

  // 界面语言切换时，若用户未手动改过，输出语言默认跟随
  useEffect(() => {
    if (!outputLangTouched.current) {
      setOutputLang(langToOutputLang(lang));
    }
  }, [lang]);

  // 拉取后端默认输出目录（用于未指定时展示）
  useEffect(() => {
    let disposed = false;
    getTtsDefaultDir()
      .then((dir) => {
        if (!disposed) setDefaultDir(dir);
      })
      .catch(() => {});
    return () => {
      disposed = true;
    };
  }, []);

  // 拉取音色清单
  useEffect(() => {
    let disposed = false;
    getVoices()
      .then((res) => {
        if (disposed) return;
        setVoices(res.voices);
        setVoiceGroups(groupVoicesByLocale(res.voices));
        setStatus(`🟢 ${t("tts.ready")} | ${t("tts.voices_loaded", { n: res.voices.length })}`);
        // 不自动选中任何音色：默认保持空，由用户显式选择（避免默认落到 zh-CN-Xiaoxiao）
      })
      .catch(() => {
        if (!disposed) setStatus(`🔴 ${t("tts.status_voices_failed")}`);
      });
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 按输出语言筛选音色（Tk 版 refresh_voices_by_lang 同款合并逻辑）：
  // - 简体中文 = zh-CN + 辽宁 + 陕西方言；繁體中文 = zh-TW + zh-HK（繁中+粤语）
  // - 其他语言 = 合并同语言所有地区变体（如 en-US/en-GB/en-AU…）
  // - English → US > GB 优先；西班牙语 → 西班牙 > 墨西哥优先
  const filteredVoices = useCallback(() => {
    const primary = (TARGET_LOCALE[outputLang] ?? "zh-CN").split("-", 1)[0].toLowerCase();
    let list: Voice[] = [];
    if (primary === "zh") {
      const keys =
        outputLang === "简体中文"
          ? ["zh-CN", "zh-CN-liaoning", "zh-CN-shaanxi"]
          : ["zh-TW", "zh-HK"];
      for (const k of keys) list = list.concat(voiceGroups.get(k) ?? []);
    } else {
      for (const [k, v] of voiceGroups) {
        if (k.split("-", 1)[0].toLowerCase() === primary) list = list.concat(v);
      }
    }
    // 去重（按 shortName）
    const seen = new Set<string>();
    const unique = list.filter((v) => {
      if (seen.has(v.shortName)) return false;
      seen.add(v.shortName);
      return true;
    });
    const base = unique.length > 0 ? unique : voices;
    // 地区优先级
    const prio: Record<string, number> =
      primary === "en"
        ? { "en-US": 0, "en-GB": 1 }
        : primary === "es"
          ? { "es-ES": 0, "es-MX": 1 }
          : {};
    return [...base].sort((a, b) => {
      const pa = prio[a.locale] ?? 99;
      const pb = prio[b.locale] ?? 99;
      return pa - pb;
    });
  }, [outputLang, voiceGroups, voices]);

  const voiceOptions = filteredVoices().map((v) => ({
    value: v.shortName,
    label: voiceLabel(v, lang),
  }));

  /** 显示路径：指定过 → 显示指定值；否则显示上次生成位置目录或后端默认目录 */
  const shownDir = outputDir
    ? outputDir
    : lastPath
      ? lastPath.replace(/\/[^/]+$/, "")
      : defaultDir || DEFAULT_DIR;

  /** Provider 是否可用：需要 LLM 时先查 API Key，未配置 → 弹设置窗口并提示 */
  const ensureProviderReady = async (): Promise<boolean> => {
    try {
      const cfg = await getConfig();
      if (cfg.provider?.has_api_key) return true;
    } catch {
      return true; // 读配置失败不阻塞流程
    }
    onOpenProvider?.();
    setStatus(`⚠️ ${t("tts.status_provider_first")}`);
    return false;
  };

  /** 预览：一致 → 原文；不一致 → LLM 翻译 */
  const handlePreview = async () => {
    if (!text.trim()) {
      setStatus(`⚠️ ${t("tts.status_input_first")}`);
      return;
    }
    if (busy) return;
    const need = needsTranslation(text, outputLang);
    if (!need) {
      setPreview(text);
      setStatus(`🟢 ${t("tts.status_no_translate")}`);
      return;
    }
    // 需要 LLM 翻译：Provider 未配置时先弹出设置
    if (!(await ensureProviderReady())) return;
    setBusy(true);
    setStatus(t("tts.status_translating"));
    try {
      const res = await ttsTranslate(text, outputLang);
      setPreview(res.translated);
      setStatus(`🟢 ${t("tts.status_translated")}`);
    } catch (e) {
      setStatus(`❌ ${t("tts.status_translate_failed", { msg: (e as Error).message })}`);
    } finally {
      setBusy(false);
    }
  };

  /** 试听：一致 → 输入文本前 20 字；不一致 → 必须已有翻译预览 */
  const handleSpeak = async () => {
    if (!text.trim()) {
      setStatus(`⚠️ ${t("tts.status_input_first")}`);
      return;
    }
    if (!voice) {
      setStatus(`⚠️ ${t("tts.status_voice_first")}`);
      return;
    }
    if (busy) return;
    const need = needsTranslation(text, outputLang);
    let src: string;
    if (need) {
      // 试听依赖翻译预览：Provider 未配置时先弹出设置
      if (!(await ensureProviderReady())) return;
      if (!preview.trim()) {
        setStatus(`⚠️ ${t("tts.status_listen_translate_first")}`);
        return;
      }
      src = preview;
    } else {
      src = text;
    }
    const snippet = src.slice(0, 20);
    if (!snippet.trim()) return;
    setBusy(true);
    setStatus(t("tts.status_speaking", { text: snippet }));
    try {
      const blob = await ttsSpeak({ text: snippet, voice, rate, volume });
      const url = URL.createObjectURL(blob);
      if (audioRef.current) audioRef.current.src = url;
      audioRef.current?.play().catch(() => {});
      setStatus(`🟢 ${t("tts.status_listening", { text: snippet })}`);
    } catch (e) {
      setStatus(`❌ ${t("tts.status_speak_failed", { msg: (e as Error).message })}`);
    } finally {
      setBusy(false);
    }
  };

  /** 生成：不一致时要求先预览确认翻译；合成期间按钮禁用 */
  const handleGenerate = async () => {
    if (!text.trim()) {
      setStatus(`⚠️ ${t("tts.status_input_first")}`);
      return;
    }
    if (!voice) {
      setStatus(`⚠️ ${t("tts.status_voice_first")}`);
      return;
    }
    if (busy) return;
    const need = needsTranslation(text, outputLang);
    const src = preview.trim() || text.trim();
    if (need && !preview.trim()) {
      // 生成依赖翻译预览：Provider 未配置时先弹出设置
      if (!(await ensureProviderReady())) return;
      setStatus(`⚠️ ${t("tts.status_translate_first")}`);
      return;
    }
    if (need && !(await ensureProviderReady())) return;
    setBusy(true);
    setStatus(t("tts.status_generating"));
    try {
      const res = await ttsGenerate({
        text: src,
        voice,
        rate,
        volume,
        output_dir: outputDir || undefined,
      });
      setLastPath(res.path);
      setStatus(`🟢 ${t("tts.status_generated", { seg: res.segments, kb: (res.bytes / 1024).toFixed(0) })}`);
    } catch (e) {
      setStatus(`❌ ${t("tts.status_generate_failed", { msg: (e as Error).message })}`);
    } finally {
      setBusy(false);
    }
  };

  const handleOpenOutput = async () => {
    const ok = await openOutputDir(shownDir);
    if (!ok) setStatus(`⚠️ ${t("tts.status_dir_missing", { dir: shownDir })}`);
  };

  const handlePickDir = async () => {
    const picked = await pickOutputDir(outputDir || undefined);
    if (picked) {
      setOutputDir(picked);
      try {
        localStorage.setItem(OUTPUT_DIR_KEY, picked);
      } catch {
        /* 存储不可用时静默 */
      }
      setStatus(`🟢 ${t("tts.status_dir_picked", { dir: picked })}`);
    }
  };

  return (
    <div className="flex flex-col gap-2.5">
      {/* 输入文本框 */}
      <Card>
        <div className="mb-2 flex items-center gap-1.5">
          <AudioLines size={14} className="text-accent2" />
          <span className="text-[14.95px] font-bold text-text">{t("tts.input_title")}</span>
        </div>
        <ExpandableTextarea
          value={text}
          onChange={setText}
          placeholder={t("tts.input_placeholder")}
          title={t("tts.input_title")}
          className="h-[125px] w-full resize-none rounded-xl border border-border bg-input px-3 py-2 text-[13.8px] text-text outline-none placeholder:text-muted focus:border-accent"
        />
        {/* 表单：输出语言 / 语速 / 音量 / 音色 同一行（列宽按界面语言锁定） */}
        <div className={`mt-1.5 grid items-center gap-x-2 ${fw.cols}`}>
          <span className="text-[13.8px] text-mid">{t("tts.output_lang")}</span>
          <EditableSelect
            value={outputLang}
            onChange={(v) => {
              outputLangTouched.current = true;
              setOutputLang(v);
            }}
            options={OUTPUT_LANGS.map((l) => ({ value: l.value, label: outputLangLabel(lang, l) }))}
            className={fw.lang}
            editable={false}
          />
          <span className="text-[13.8px] text-mid">{t("tts.rate")}</span>
          <EditableSelect
            value={rate}
            onChange={setRate}
            onBlur={() => setRate(normalizeRate(rate))}
            options={RATES.map((r) => ({ value: r, label: r }))}
            className={fw.rate}
            title={t("tts.rate_title")}
          />
          <span className="text-[13.8px] text-mid">{t("tts.volume")}</span>
          <EditableSelect
            value={volume}
            onChange={setVolume}
            onBlur={() => setVolume(normalizeVolume(volume))}
            options={VOLUMES.map((v) => ({ value: v, label: v }))}
            className={fw.volume}
            title={t("tts.volume_title")}
          />
          <span className="text-[13.8px] text-mid">{t("tts.voice")}</span>
          <EditableSelect
            value={voice}
            onChange={setVoice}
            options={voiceOptions}
            className={fw.voice}
            editable={false}
            menuAlign="right"
          />
        </div>
      </Card>

      {/* 输出预览 */}
      <Card>
        <div className="mb-2 flex items-center gap-1.5">
          <Wand2 size={14} className="text-accent2" />
          <span className="text-[14.95px] font-bold text-text">{t("tts.output_title")}</span>
        </div>
        <ExpandableTextarea
          value={preview}
          onChange={setPreview}
          placeholder={t("tts.output_placeholder")}
          title={t("tts.output_title")}
          className="h-[125px] w-full resize-none rounded-xl border border-border bg-input px-3 py-2 text-[13.8px] text-text outline-none placeholder:text-muted focus:border-accent"
        />
      </Card>

      {/* 底部操作栏 */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Btn variant="ghost" onClick={handlePreview} disabled={busy}>
              <Wand2 size={12} />
              {t("tts.preview")}
            </Btn>
            <Btn variant="ghost" onClick={handleSpeak} disabled={busy}>
              <Play size={12} />
              {t("tts.listen")}
            </Btn>
            <Btn variant="primary" onClick={handleGenerate} disabled={busy}>
              <Zap size={13} />
              {busy ? t("tts.busy") : t("tts.generate")}
            </Btn>
          </div>
          <div className="flex items-center gap-2">
            <Btn variant="ghost" onClick={handlePickDir} title={t("tts.pick_title")}>
              <FolderInput size={12} />
              {t("tts.pick")}
            </Btn>
            <Btn variant="ghost" onClick={handleOpenOutput} title={t("tts.open_title")}>
              <FolderOpen size={12} />
              {t("tts.open")}
            </Btn>
          </div>
        </div>
        {/* 状态栏：左侧状态文字，右侧当前输出文件夹 */}
        <div className="mt-2.5 flex items-center justify-between gap-3">
          <span
            className={`truncate text-[11.5px] ${
              busy ? "text-accent2" : "text-muted"
            }`}
          >
            {status || "　"}
          </span>
          <span
            className="shrink-0 truncate text-[11.5px] text-muted"
            title={shownDir}
          >
            {t("tts.out_dir")}: {shownDir}
          </span>
        </div>
      </Card>

      {/* 隐藏的试听播放器 */}
      <audio ref={audioRef} className="hidden" />
    </div>
  );
}
