/**
 * 语音服务配置弹窗（对应 Tk 版 open_provider_dialog）
 * - 平台切换（Groq / 火山引擎 / 硅基流动 / 自定义，按优先级排序），每平台独立记忆已填字段
 * - 内置平台 URL 只读显示（灰置，用户无需填写）；自定义平台可编辑
 * - 火山：ASR 为 WebSocket 流式（无 URL）+ 方舟 LLM Key（双 Key），ASR 模型为内置资源 ID
 * - 抓取模型列表 / 测试连接（调后端 /api/provider/*）
 * - API Key 粘贴自动清洗（sanitizeApiKey），LLM Key 同样提供清洗粘贴
 * - 各平台 Key 用 localStorage 持久化记忆（输过跑通的平台，下次选回 Key 仍在）
 */
import { useEffect, useRef, useState } from "react";
import {
  RefreshCw,
  Plug,
  Link as LinkIcon,
  ExternalLink,
  Save,
} from "lucide-react";
import {
  getConfig,
  updateConfig,
  providerModels,
  providerTest,
  sanitizeApiKey,
  ProviderPayload,
} from "../api";
import { Modal, Select, ComboInput, Btn } from "./ui";
import { useI18n } from "../i18n";

interface PlatformDef {
  nameKey: string;
  signup_url: string;
  base_url: string;
  default_asr: string;
  default_llm: string;
  llm_pinned: string[];
  needs_proxy: boolean;
  custom: boolean;
}

// 与后端 provider.PROVIDERS 对齐的元数据（前端展示用）；顺序即平台优先级
const PLATFORMS: Record<string, PlatformDef> = {
  groq: {
    nameKey: "provider.plat_groq",
    signup_url: "https://console.groq.com/keys",
    base_url: "https://api.groq.com/openai/v1",
    default_asr: "whisper-large-v3-turbo",
    default_llm: "openai/gpt-oss-120b",
    llm_pinned: ["openai/gpt-oss-120b", "groq/compound", "qwen/qwen3.8-27b"],
    needs_proxy: true,
    custom: false,
  },
  volcengine: {
    nameKey: "provider.plat_volcengine",
    signup_url: "https://console.volcengine.com/speech/new/",
    base_url: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
    default_asr: "volc.seedasr.sauc.duration",
    default_llm: "deepseek-v4-flash-260425",
    llm_pinned: [
      "deepseek-v4-flash-260425",
      "doubao-seed-2-0-mini-260428",
      "doubao-seed-2-0-lite-260428",
      "doubao-seed-evolving",
    ],
    needs_proxy: false,
    custom: false,
  },
  siliconflow: {
    nameKey: "provider.plat_siliconflow",
    signup_url: "https://cloud.siliconflow.cn",
    base_url: "https://api.siliconflow.cn/v1",
    default_asr: "whisper-large-v3-turbo",
    default_llm: "deepseek-ai/DeepSeek-V4-Flash",
    llm_pinned: ["deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V3.2"],
    needs_proxy: false,
    custom: false,
  },
  custom: {
    nameKey: "provider.plat_custom",
    signup_url: "",
    base_url: "",
    default_asr: "",
    default_llm: "",
    llm_pinned: [],
    needs_proxy: false,
    custom: true,
  },
};

const VOLC_ASR_IDS = [
  "volc.seedasr.sauc.duration",
  "volc.seedasr.sauc.concurrent",
  "volc.bigasr.sauc.duration",
  "volc.bigasr.sauc.concurrent",
];

const FALLBACK_ASR = ["whisper-large-v3-turbo", "whisper-large-v3", "FunAudioLLM/SenseVoiceSmall"];
const FALLBACK_LLM = [
  "llama-3.3-70b-versatile",
  "DeepSeek-V3",
  "Qwen/Qwen2.5-72B-Instruct",
  "Doubao-1.5-pro",
];

interface Fields {
  platform: string;
  api_key: string;
  llm_key: string;
  asr: string;
  llm: string;
  url: string;
  llm_url: string;
}

/** 各平台 Key 的本地记忆（localStorage）：切换/重开弹窗都保留已输入 Key */
const KEYS_STORAGE = "fewtype_provider_keys";
// 改名 VoxEcho→FewType 前的旧 key：升级后自动继承已填 Key（写入时仍写新 key，自动迁移）
const KEYS_STORAGE_LEGACY = "voxecho_provider_keys";

function loadSavedKeys(): Record<string, { api_key?: string; llm_key?: string }> {
  try {
    return JSON.parse(localStorage.getItem(KEYS_STORAGE) ?? localStorage.getItem(KEYS_STORAGE_LEGACY) ?? "{}");
  } catch {
    return {};
  }
}

function saveKeysToStorage(
  plat: string,
  keys: { api_key?: string; llm_key?: string }
) {
  try {
    const all = loadSavedKeys();
    all[plat] = { ...(all[plat] ?? {}), ...keys };
    localStorage.setItem(KEYS_STORAGE, JSON.stringify(all));
  } catch {
    /* localStorage 不可用时静默 */
  }
}

export default function ProviderDialog({
  open,
  onClose,
  onSaved,
  notice,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
  /** 自动弹出原因提示（如"热键触发但未配置 API Key"），显示数秒后自动消失 */
  notice?: string | null;
}) {
  const { t } = useI18n();
  const [showNotice, setShowNotice] = useState(false);
  const [platform, setPlatform] = useState("groq");
  const [fields, setFields] = useState<Fields>({
    platform: "groq", api_key: "", llm_key: "", asr: "", llm: "", url: "", llm_url: "",
  });
  const [asrOptions, setAsrOptions] = useState<string[]>([]);
  const [llmOptions, setLlmOptions] = useState<string[]>([]);
  const [hint, setHint] = useState("");
  const [testMsg, setTestMsg] = useState("");
  const [testOk, setTestOk] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const keyInputRef = useRef<HTMLInputElement | null>(null);

  // 打开时加载配置 + 初始化
  useEffect(() => {
    if (!open) return;
    setTestMsg("");
    setTestOk(null);
    setMsg("");
    getConfig()
      .then((cfg) => {
        const pc = cfg.provider;
        const saved = loadSavedKeys();
        const savedKeys = saved[pc.platform || "groq"] ?? {};
        // 优先回填 localStorage 记忆的 Key（用户输过的直接可用，不因后端掩码态而清空）
        const initFields: Fields = {
          platform: pc.platform || "groq",
          api_key: savedKeys.api_key ?? "",
          llm_key: savedKeys.llm_key ?? "",
          asr: pc.asr_model || "",
          llm: pc.llm_model || "",
          url: pc.base_url || "",
          llm_url: pc.llm_base_url || "",
        };
        setPlatform(initFields.platform);
        setFields(initFields);
        applyPlatformDefaults(initFields.platform, initFields);
      })
      .catch((e) => setMsg(t("provider.msg_load_failed", { msg: (e as Error).message })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 自动弹出原因提示：打开且带 notice 时显示，4 秒后自动消失
  useEffect(() => {
    if (!open || !notice) {
      setShowNotice(false);
      return;
    }
    setShowNotice(true);
    const timer = setTimeout(() => setShowNotice(false), 4000);
    return () => clearTimeout(timer);
  }, [open, notice]);

  // 自动弹出原因提示：打开且带 notice 时显示，4 秒后自动消失
  useEffect(() => {
    if (!open || !notice) {
      setShowNotice(false);
      return;
    }
    setShowNotice(true);
    const timer = setTimeout(() => setShowNotice(false), 4000);
    return () => clearTimeout(timer);
  }, [open, notice]);

  const defn = () => PLATFORMS[platform] ?? PLATFORMS.groq;

  const applyPlatformDefaults = (plat: string, f: Fields) => {
    const d = PLATFORMS[plat];
    const next = { ...f, platform: plat };
    if (d.custom) {
      // 自定义：模型与 URL 手填
      next.url = f.url || "";
      next.asr = f.asr || "";
      next.llm = f.llm || "";
    } else if (plat === "volcengine") {
      next.url = t("provider.volc_url_note");
      next.llm_url = "https://ark.cn-beijing.volces.com/api/v3";
      next.asr = f.asr || d.default_asr;
      next.llm = f.llm || d.default_llm;
    } else {
      next.url = d.base_url;
      next.llm_url = "";
      next.asr = f.asr || d.default_asr;
      next.llm = f.llm || d.default_llm;
    }
    // 选项列表
    if (plat === "volcengine") {
      setAsrOptions(Array.from(new Set([next.asr, ...VOLC_ASR_IDS])));
    } else {
      setAsrOptions(Array.from(new Set([next.asr || d.default_asr, ...FALLBACK_ASR])));
    }
    setLlmOptions(
      Array.from(new Set([next.llm || d.default_llm, ...(d.llm_pinned ?? []), ...FALLBACK_LLM]))
    );
    // 平台提示
    const hints: Record<string, string> = {
      groq: d.needs_proxy ? t("provider.hint_groq") : "",
      siliconflow: t("provider.hint_siliconflow"),
      volcengine: t("provider.hint_volcengine"),
      custom: t("provider.hint_custom"),
    };
    setHint(hints[plat] ?? "");
    setFields(next);
  };

  const switchPlatform = (plat: string) => {
    // 记忆当前平台已填的 Key
    saveKeysToStorage(platform, {
      api_key: fields.api_key || undefined,
      llm_key: fields.llm_key || undefined,
    });
    setPlatform(plat);
    // 恢复目标平台已记忆的 Key（若配置里有 masked key 则保持空，由后端持有）
    const saved = loadSavedKeys()[plat] ?? {};
    const cached: Fields = {
      platform: plat, api_key: saved.api_key ?? "", llm_key: saved.llm_key ?? "",
      asr: "", llm: "", url: "", llm_url: "",
    };
    applyPlatformDefaults(plat, cached);
  };

  const set = (k: keyof Fields, v: string) => {
    setFields((prev) => {
      const next = { ...prev, [k]: v };
      // Key 输入即时记忆
      if (k === "api_key" || k === "llm_key") {
        saveKeysToStorage(platform, { [k]: v || undefined });
      }
      return next;
    });
  };

  const buildPayload = (): ProviderPayload => ({
    platform,
    api_key: fields.api_key || undefined,
    llm_key: fields.llm_key || undefined,
    asr_model: fields.asr || "",
    llm_model: fields.llm || "",
    base_url: fields.url || "",
    llm_base_url: fields.llm_url || "",
  });

  const handleFetch = async () => {
    setBusy(true);
    setMsg(t("provider.msg_fetching"));
    try {
      const res = await providerModels(buildPayload());
      if (!res.ok) {
        setMsg(`❌ ${t("provider.msg_fetch_failed", { msg: res.message ?? "" })}`);
        return;
      }
      const curAsr = fields.asr;
      const curLlm = fields.llm;
      if (platform === "volcengine") {
        setAsrOptions(Array.from(new Set([curAsr, ...(res.asr ?? VOLC_ASR_IDS)])));
      } else {
        setAsrOptions(Array.from(new Set([curAsr, ...(res.asr ?? [])])));
      }
      setLlmOptions(Array.from(new Set([curLlm, ...(defn().llm_pinned ?? []), ...(res.llm ?? [])])));
      setMsg(`✅ ${t("provider.msg_fetched")}`);
    } catch (e) {
      setMsg(`❌ ${t("provider.msg_fetch_failed", { msg: (e as Error).message })}`);
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    setTestMsg(t("provider.msg_testing"));
    setTestOk(null);
    try {
      const res = await providerTest(buildPayload());
      setTestMsg(res.message);
      setTestOk(res.ok);
    } catch (e) {
      setTestMsg((e as Error).message);
      setTestOk(false);
    } finally {
      setBusy(false);
    }
  };

  const handlePaste = async () => {
    try {
      const raw = await navigator.clipboard.readText();
      const clean = sanitizeApiKey(raw.trim());
      if (!clean) {
        setMsg(t("provider.msg_paste_empty"));
        return;
      }
      set("api_key", clean);
      setMsg(`✅ ${t("provider.msg_pasted")}`);
    } catch {
      setMsg(t("provider.msg_paste_err"));
    }
  };

  const handlePasteLlm = async () => {
    try {
      const raw = await navigator.clipboard.readText();
      const clean = sanitizeApiKey(raw.trim());
      if (!clean) {
        setMsg(t("provider.msg_paste_empty"));
        return;
      }
      set("llm_key", clean);
      setMsg(`✅ ${t("provider.msg_pasted")}`);
    } catch {
      setMsg(t("provider.msg_paste_err"));
    }
  };

  const handleSave = async () => {
    if (platform !== "custom" && !fields.api_key) {
      setMsg(t("provider.msg_key_first"));
      return;
    }
    setBusy(true);
    try {
      const payload: ProviderPayload = {
        platform,
        asr_model: fields.asr.trim(),
        llm_model: fields.llm.trim(),
        base_url: fields.url.trim(),
        llm_base_url: fields.llm_url.trim(),
      };
      // 只有用户输入了新 key 才提交（masked 情况下 fields.api_key 为空）
      if (fields.api_key) payload.api_key = sanitizeApiKey(fields.api_key);
      if (fields.llm_key) payload.llm_key = sanitizeApiKey(fields.llm_key);
      await updateConfig({ provider: payload });
      // 保存成功 → 更新记忆（只写非空 Key；空 Key 表示后端持掩码 Key，不清空已有记忆）
      const all = loadSavedKeys();
      const cur = all[platform] ?? {};
      if (fields.api_key) cur.api_key = fields.api_key;
      if (fields.llm_key) cur.llm_key = fields.llm_key;
      all[platform] = cur;
      localStorage.setItem(KEYS_STORAGE, JSON.stringify(all));
      setMsg(`✅ ${t("provider.msg_saved")}`);
      onSaved?.();
      setTimeout(onClose, 400);
    } catch (e) {
      setMsg(`❌ ${t("provider.msg_save_failed", { msg: (e as Error).message })}`);
    } finally {
      setBusy(false);
    }
  };

  const d = defn();
  const isCustom = platform === "custom";
  const isVolc = platform === "volcengine";
  const showLlmKey = isVolc || isCustom;

  const inputCls =
    "w-full rounded-lg border border-border bg-input px-3 py-0.5 text-[13.8px] leading-none text-text outline-none placeholder:text-muted focus:border-accent";
  const readonlyCls =
    "w-full rounded-lg border border-border/60 bg-bg px-3 py-0.5 text-[12.65px] leading-none text-[#5A6560] outline-none";
  const lblCls = "mb-1 block text-[12.65px] text-mid";

  const body = (
    <>
      {/* 平台选择 + 注册链接 */}
      <div className="mb-4 flex items-center justify-between">
        <Select
          value={platform}
          onChange={switchPlatform}
          options={Object.entries(PLATFORMS).map(([v, p]) => ({ value: v, label: t(p.nameKey) }))}
        />
        {d.signup_url ? (
          <a
            href={d.signup_url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-[12.65px] text-accent2 hover:underline"
          >
            <ExternalLink size={11} />
            {t("provider.get_key")}
          </a>
        ) : null}
      </div>
      {hint ? (
        <p className="mb-3 flex items-center gap-1.5 text-[12.65px] text-muted">
          <LinkIcon size={11} className="text-accent2/70" />
          {hint}
        </p>
      ) : null}

      <div className="flex flex-col gap-2.5">
        {/* ASR 行 */}
        <div>
          <label className={lblCls}>
            {isVolc ? t("provider.asr_url_builtin") : isCustom ? t("provider.asr_url_custom") : t("provider.asr_url")}
          </label>
          <input
            value={fields.url}
            onChange={(e) => set("url", e.target.value)}
            readOnly={!isCustom}
            className={isCustom ? inputCls : readonlyCls}
          />
        </div>
        <div>
          <label className={lblCls}>{t("provider.asr_key")}</label>
          <div className="flex items-center gap-2">
            <input
              ref={keyInputRef}
              type="password"
              value={fields.api_key}
              onChange={(e) => set("api_key", e.target.value)}
              placeholder={fields.api_key ? "" : `${t("provider.paste")} API Key…`}
              className={inputCls}
            />
            <Btn variant="ghost" onClick={handlePaste}>
              {t("provider.paste")}
            </Btn>
          </div>
        </div>
        <div>
          <label className={lblCls}>{t("provider.asr_model")}</label>
          <ComboInput
            value={fields.asr}
            onChange={(v) => set("asr", v)}
            placeholder={t("provider.asr_model_placeholder")}
            options={asrOptions}
            className="w-full"
          />
        </div>

        {/* LLM 行 */}
        {showLlmKey ? (
          <div>
            <label className={lblCls}>
              {isVolc ? t("provider.llm_key_volc") : t("provider.llm_key")}
            </label>
            <div className="flex items-center gap-2">
              <input
                type="password"
                value={fields.llm_key}
                onChange={(e) => set("llm_key", e.target.value)}
                placeholder={fields.llm_key ? "" : `${t("provider.paste")} LLM Key…`}
                className={inputCls}
              />
              <Btn variant="ghost" onClick={handlePasteLlm}>
                {t("provider.paste")}
              </Btn>
            </div>
          </div>
        ) : null}
        {isCustom ? (
          <div>
            <label className={lblCls}>{t("provider.llm_url")}</label>
            <input
              value={fields.llm_url}
              onChange={(e) => set("llm_url", e.target.value)}
              className={inputCls}
            />
          </div>
        ) : null}
        <div>
          <label className={lblCls}>{t("provider.llm_model")}</label>
          <ComboInput
            value={fields.llm}
            onChange={(v) => set("llm", v)}
            placeholder={t("provider.llm_model_placeholder")}
            options={llmOptions}
            className="w-full"
          />
        </div>
      </div>

      {/* 操作栏 */}
      <div className="mt-4 flex items-center gap-2">
        <Btn variant="ghost" onClick={handleFetch} disabled={busy}>
          <RefreshCw size={12} className={busy ? "animate-spin" : ""} />
          {t("provider.fetch")}
        </Btn>
        <Btn variant="ghost" onClick={handleTest} disabled={busy}>
          <Plug size={12} />
          {t("provider.test")}
        </Btn>
        <div className="flex-1" />
        <Btn variant="primary" onClick={handleSave} disabled={busy}>
          <Save size={12} />
          {t("provider.save")}
        </Btn>
      </div>

      {/* 状态区 */}
      <div className="mt-2 flex flex-col gap-0.5">
        {testMsg ? (
          <p
            className={`text-[12.65px] ${
              testOk === null ? "text-muted" : testOk ? "text-accent2" : "text-[#ff6b6b]"
            }`}
          >
            {testMsg}
          </p>
        ) : null}
        {msg ? <p className="text-[12.65px] text-mid">{msg}</p> : null}
      </div>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} title={t("provider.title")} width={560}>
      {showNotice && notice ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-[12.65px] leading-snug text-amber-200">
          <span className="shrink-0 text-amber-300">ℹ️</span>
          <span>{notice}</span>
        </div>
      ) : null}
      {body}
    </Modal>
  );
}
