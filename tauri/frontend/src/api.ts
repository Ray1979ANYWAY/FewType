/**
 * VoxEcho 前端通讯层（阶段二）
 *
 * 与后端 FastAPI（tauri/backend/api/main.py，端口 5010）的全部交互：
 * - HTTP：配置 / 音色 / TTS / 翻译
 * - WebSocket：语音输入（录音状态 / 识别结果 / 上屏日志）
 *
 * 后端地址优先级：VITE_API_BASE 环境变量 > 开发代理（同源 /api）> 默认本机 5010。
 * Tauri 生产壳内通过 tauri.conf.json 注入 API_BASE。
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  "http://127.0.0.1:5010";

export const WS_BASE: string = API_BASE.replace(/^http/, "ws");

/* ============================================================
 * 类型定义（与后端 Pydantic 模型一一对应）
 * ============================================================ */

export interface ProviderConfig {
  platform: string;
  /** GET 返回遮罩视图，不含完整 key */
  has_api_key?: boolean;
  api_key_masked?: string;
  has_llm_key?: boolean;
  llm_key_masked?: string;
  /** POST /api/config 提交时传入（更新密钥） */
  api_key?: string;
  llm_key?: string;
  asr_model: string;
  llm_model: string;
  base_url: string;
  llm_base_url: string;
}

export interface SttStyle {
  name: string;
  prompt: string;
}

export interface AppConfig {
  autostart: boolean;
  first_run_done: boolean;
  ui_lang: string;
  stt_hotkey: string;
  stt_auto_commit: boolean;
  stt_mode: string;
  stt_custom_style: string;
  stt_translate: boolean;
  stt_target_lang: string;
  tts_output_dir: string;
  provider: ProviderConfig;
  stt_styles: SttStyle[];
}

export interface Voice {
  shortName: string;
  friendlyName: string;
  gender: string;
  locale: string;
  status: string;
}

export interface VoicesResponse {
  voices: Voice[];
  updatedAt: number;
}

export interface TTSFileResult {
  path: string;
  filename: string;
  bytes: number;
  segments: number;
  voice: string;
}

export interface TranslateResult {
  translated: string;
  target_lang: string;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface ExtensionStatus {
  online: boolean;
  last_heartbeat: number;
  seconds_since_last: number | null;
}

/* ============================================================
 * HTTP 基础封装
 * ============================================================ */

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* 非 JSON 响应，保留状态码 */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/* ============================================================
 * 健康 & 扩展状态
 * ============================================================ */

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export async function getExtensionStatus(): Promise<ExtensionStatus> {
  return request<ExtensionStatus>("/extension_status");
}

/* ============================================================
 * 配置
 * ============================================================ */

export async function getConfig(): Promise<AppConfig> {
  return request<AppConfig>("/api/config");
}

/**
 * 更新配置（白名单合并，服务端校验）。
 * 传入 provider.api_key / llm_key 时更新密钥；留空表示不修改。
 */
export async function updateConfig(
  patch: Partial<AppConfig>
): Promise<AppConfig> {
  return request<AppConfig>("/api/config", {
    method: "POST",
    body: JSON.stringify(patch),
  });
}

/* ============================================================
 * 音色清单
 * ============================================================ */

export async function getVoices(): Promise<VoicesResponse> {
  return request<VoicesResponse>("/api/voices");
}

/** 按语言分组音色，返回 locale -> Voice[] 映射 */
export function groupVoicesByLocale(voices: Voice[]): Map<string, Voice[]> {
  const map = new Map<string, Voice[]>();
  for (const v of voices) {
    const loc = v.locale || v.shortName.split("-")[0] || "other";
    const list = map.get(loc) ?? [];
    list.push(v);
    map.set(loc, list);
  }
  return map;
}

/* ---------------- 音色展示名精简（去掉 Microsoft / Online Natural / 语言名等冗余） ---------------- */

/** locale → 中文地区名 */
const LOCALE_ZH: Record<string, string> = {
  "zh-CN": "中国大陆",
  "zh-CN-liaoning": "中国辽宁",
  "zh-CN-shaanxi": "中国陕西",
  "zh-TW": "中国台湾",
  "zh-HK": "中国香港",
  "en-US": "美国",
  "en-GB": "英国",
  "en-AU": "澳大利亚",
  "en-IN": "印度",
  "en-CA": "加拿大",
  "en-NZ": "新西兰",
  "en-IE": "爱尔兰",
  "en-SG": "新加坡",
  "en-ZA": "南非",
  "en-PH": "菲律宾",
  "es-ES": "西班牙",
  "es-MX": "墨西哥",
  "es-US": "美国(西)",
  "es-AR": "阿根廷",
  "es-CO": "哥伦比亚",
  "fr-FR": "法国",
  "fr-CA": "加拿大(法)",
  "fr-CH": "瑞士(法)",
  "fr-BE": "比利时(法)",
  "de-DE": "德国",
  "de-AT": "奥地利",
  "de-CH": "瑞士(德)",
  "ja-JP": "日本",
  "ko-KR": "韩国",
  "pt-BR": "巴西",
  "pt-PT": "葡萄牙",
  "it-IT": "意大利",
  "ru-RU": "俄罗斯",
  "ar-EG": "埃及(阿)",
  "ar-SA": "沙特(阿)",
  "hi-IN": "印度(印地)",
  "nl-NL": "荷兰",
  "pl-PL": "波兰",
  "tr-TR": "土耳其",
  "sv-SE": "瑞典",
  "da-DK": "丹麦",
  "fi-FI": "芬兰",
  "nb-NO": "挪威",
  "cs-CZ": "捷克",
  "th-TH": "泰国",
  "vi-VN": "越南",
  "id-ID": "印尼",
  "ms-MY": "马来",
  "uk-UA": "乌克兰",
  "el-GR": "希腊",
  "hu-HU": "匈牙利",
  "he-IL": "以色列(希)",
  "ro-RO": "罗马尼亚",
  "sk-SK": "斯洛伐克",
  "bg-BG": "保加利亚",
  "hr-HR": "克罗地亚",
  "lt-LT": "立陶宛",
  "sl-SI": "斯洛文尼亚",
  "et-EE": "爱沙尼亚",
  "lv-LV": "拉脱维亚",
  "ca-ES": "加泰罗尼亚",
  "af-ZA": "南非(阿非)",
  "sw-KE": "肯尼亚(斯瓦)",
  "am-ET": "埃塞俄比亚(阿姆)",
  "zu-ZA": "南非(祖鲁)",
  "xh-ZA": "南非(科萨)",
  "fil-PH": "菲律宾(他加禄)",
  "cy-GB": "威尔士",
  "ga-IE": "爱尔兰(盖尔)",
  "is-IS": "冰岛",
  "sr-RS": "塞尔维亚",
  "mk-MK": "北马其顿",
  "sq-AL": "阿尔巴尼亚",
  "bs-BA": "波斯尼亚",
  "mn-MN": "蒙古",
  "kk-KZ": "哈萨克",
  "uz-UZ": "乌兹别克",
  "fa-IR": "伊朗(波斯)",
  "ur-PK": "巴基斯坦(乌尔都)",
  "ta-IN": "印度(泰米尔)",
  "te-IN": "印度(泰卢固)",
  "kn-IN": "印度(卡纳达)",
  "ml-IN": "印度(马拉雅拉姆)",
  "gu-IN": "印度(古吉拉特)",
  "mr-IN": "印度(马拉地)",
  "pa-IN": "印度(旁遮普)",
  "bn-IN": "印度(孟加拉)",
  "ne-NP": "尼泊尔",
  "si-LK": "斯里兰卡(僧伽罗)",
  "km-KH": "柬埔寨",
  "lo-LA": "老挝",
  "my-MM": "缅甸",
};

/** locale → 英文地区名（英文界面用；未收录回退 locale 码） */
const LOCALE_EN: Record<string, string> = {
  "zh-CN": "China",
  "zh-CN-liaoning": "Liaoning (China)",
  "zh-CN-shaanxi": "Shaanxi (China)",
  "zh-TW": "Taiwan",
  "zh-HK": "Hong Kong",
  "en-US": "United States",
  "en-GB": "United Kingdom",
  "en-AU": "Australia",
  "en-IN": "India",
  "en-CA": "Canada",
  "en-NZ": "New Zealand",
  "en-IE": "Ireland",
  "en-SG": "Singapore",
  "en-ZA": "South Africa",
  "en-PH": "Philippines",
  "es-ES": "Spain",
  "es-MX": "Mexico",
  "es-US": "Spanish (US)",
  "es-AR": "Argentina",
  "es-CO": "Colombia",
  "fr-FR": "France",
  "fr-CA": "French (Canada)",
  "fr-CH": "French (Switzerland)",
  "fr-BE": "French (Belgium)",
  "de-DE": "Germany",
  "de-AT": "Austria",
  "de-CH": "German (Switzerland)",
  "ja-JP": "Japan",
  "ko-KR": "South Korea",
  "pt-BR": "Brazil",
  "pt-PT": "Portugal",
  "it-IT": "Italy",
  "ru-RU": "Russia",
  "ar-EG": "Egypt",
  "ar-SA": "Saudi Arabia",
  "hi-IN": "Hindi (India)",
  "nl-NL": "Netherlands",
  "pl-PL": "Poland",
  "tr-TR": "Türkiye",
  "sv-SE": "Sweden",
  "da-DK": "Denmark",
  "fi-FI": "Finland",
  "nb-NO": "Norway",
  "cs-CZ": "Czechia",
  "th-TH": "Thailand",
  "vi-VN": "Vietnam",
  "id-ID": "Indonesia",
  "ms-MY": "Malay",
  "uk-UA": "Ukraine",
  "el-GR": "Greece",
  "hu-HU": "Hungary",
  "he-IL": "Hebrew (Israel)",
  "ro-RO": "Romania",
  "sk-SK": "Slovakia",
  "bg-BG": "Bulgaria",
  "hr-HR": "Croatia",
  "lt-LT": "Lithuania",
  "sl-SI": "Slovenia",
  "et-EE": "Estonia",
  "lv-LV": "Latvia",
  "ca-ES": "Catalan",
  "af-ZA": "Afrikaans (South Africa)",
  "sw-KE": "Swahili (Kenya)",
  "am-ET": "Amharic (Ethiopia)",
  "zu-ZA": "Zulu",
  "xh-ZA": "Xhosa",
  "fil-PH": "Filipino",
  "cy-GB": "Welsh",
  "ga-IE": "Irish",
  "is-IS": "Icelandic",
  "sr-RS": "Serbian",
  "mk-MK": "Macedonian",
  "sq-AL": "Albanian",
  "bs-BA": "Bosnian",
  "mn-MN": "Mongolian",
  "kk-KZ": "Kazakh",
  "uz-UZ": "Uzbek",
  "fa-IR": "Persian",
  "ur-PK": "Urdu",
  "ta-IN": "Tamil (India)",
  "te-IN": "Telugu (India)",
  "kn-IN": "Kannada (India)",
  "ml-IN": "Malayalam (India)",
  "gu-IN": "Gujarati (India)",
  "mr-IN": "Marathi (India)",
  "pa-IN": "Punjabi (India)",
  "bn-IN": "Bengali (India)",
  "ne-NP": "Nepali",
  "si-LK": "Sinhala",
  "km-KH": "Khmer",
  "lo-LA": "Lao",
  "my-MM": "Burmese",
};

/** 从 shortName / friendlyName 提取角色名：zh-CN-XiaoxiaoNeural → Xiaoxiao */
export function voiceRoleName(v: Voice): string {
  const sn = (v.shortName || "").trim();
  // 从 friendlyName 提取（"Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)"）
  let fn = (v.friendlyName || "").trim();
  fn = fn.replace(/^Microsoft\s*/i, "");
  fn = fn.replace(/\s*Online\s*(\(Natural\))?/gi, "");
  fn = fn.replace(/\s*\([^)]*\)/g, " ");      // 去掉括号片段
  fn = fn.replace(/\s*-\s*[^-]*$/i, "");       // 去掉尾部 "- Chinese (Mainland)" 等
  fn = fn.trim();
  // 清理常见尾部后缀（friendlyName 残留）
  fn = fn.replace(/\b(Neural|Natural|Multilingual|Online|Standard)\b/gi, "").trim();
  if (fn) return fn;
  // 回退：从 ShortName 提取角色段
  const parts = sn.split("-");
  if (parts.length >= 3) return parts.slice(2).join("-").replace(/Neural$/i, "");
  return sn;
}

/** 地区名（按界面语言：中文/繁中用中文名，英文界面用英文名，回退 locale 码） */
export function voiceRegionName(v: Voice, lang?: string): string {
  if (lang === "en-US") return LOCALE_EN[v.locale] ?? v.locale ?? "";
  return LOCALE_ZH[v.locale] ?? v.locale ?? "";
}

/** 精简后的音色展示名：♀ Xiaoxiao · 中国大陆（语言随界面） */
export function voiceLabel(v: Voice, lang?: string): string {
  const gender = v.gender === "Female" ? "♀" : v.gender === "Male" ? "♂" : "♪";
  const role = voiceRoleName(v);
  const region = voiceRegionName(v, lang);
  return `${gender} ${role}${region ? ` · ${region}` : ""}`;
}

/* ============================================================
 * TTS
 * ============================================================ */

export interface TTSPayload {
  text: string;
  voice?: string;
  rate?: string;
  volume?: string;
  output_dir?: string;
}

/** 长文本 TTS：分段合成，返回音频文件路径 */
export async function ttsGenerate(
  payload: TTSPayload
): Promise<TTSFileResult> {
  return request<TTSFileResult>("/api/tts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 短文本试听：直接拿 MP3 blob（<audio> 可播放） */
export async function ttsSpeak(
  payload: TTSPayload
): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/tts/speak`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return await res.blob();
}

/** LLM 翻译（长文本转语音前置） */
export async function ttsTranslate(
  text: string,
  targetLang: string
): Promise<TranslateResult> {
  return request<TranslateResult>("/api/tts/translate", {
    method: "POST",
    body: JSON.stringify({ text, target_lang: targetLang }),
  });
}

/* ============================================================
 * Provider（连接测试 / 模型抓取）
 * ============================================================ */

export interface ProviderPayload {
  platform: string;
  api_key?: string;
  llm_key?: string;
  asr_model: string;
  llm_model: string;
  base_url: string;
  llm_base_url: string;
}

export interface ProviderTestResult {
  ok: boolean;
  message: string;
}

export interface ProviderModelsResult {
  ok: boolean;
  asr?: string[];
  llm?: string[];
  message?: string;
}

/** 测试连接（可传未保存的临时字段，空字段回退配置） */
export async function providerTest(
  payload: ProviderPayload
): Promise<ProviderTestResult> {
  return request<ProviderTestResult>("/api/provider/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 抓取并分类模型列表（ASR / LLM） */
export async function providerModels(
  payload: ProviderPayload
): Promise<ProviderModelsResult> {
  return request<ProviderModelsResult>("/api/provider/models", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 清洗 API Key（与后端 sanitize_api_key 一致：仅保留 ASCII 字母数字 - _ =） */
export function sanitizeApiKey(key: string): string {
  return (key || "").replace(/[^a-zA-Z0-9\-_=]/g, "");
}

/* ============================================================
 * 系统对话框（/api/dialog/*）
 * ============================================================ */

/** 弹出系统文件夹选择对话框，返回所选路径（取消返回空串） */
export async function pickOutputDir(initial?: string): Promise<string> {
  const res = await request<{ path: string }>("/api/dialog/pick-dir", {
    method: "POST",
    body: JSON.stringify({ initial: initial ?? "" }),
  });
  return res.path ?? "";
}

/** 用资源管理器打开目录（或定位文件所在目录） */
export async function openOutputDir(path: string): Promise<boolean> {
  const res = await request<{ ok: boolean }>("/api/dialog/open-dir", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
  return res.ok ?? false;
}

/* ============================================================
 * 运行日志（/api/logs，日志抽屉轮询源）
 * ============================================================ */

/** 拉取后端最近运行日志（快照） */
export async function getLogs(): Promise<string[]> {
  const res = await request<{ lines?: string[] }>("/api/logs");
  return res.lines ?? [];
}

/** 翻译确认：提交用户确认/修改后的原文，后端据此翻译上屏；cancel=true 放弃本次 */
export async function confirmSpeech(
  text: string,
  source: string,
  cancel = false,
): Promise<boolean> {
  try {
    const res = await request<{ ok: boolean }>("/api/speech/confirm", {
      method: "POST",
      body: JSON.stringify({ text, source, cancel }),
    });
    return res.ok ?? false;
  } catch {
    return false;
  }
}

/* ============================================================
 * WS 语音输入（/ws/speech-input）
 * ============================================================ */

export type SpeechState =
  | "listening"
  | "transcribing"
  | "polishing"
  | "confirming"
  | "committing"
  | "idle"
  | "error";

export interface SpeechStatusEvent {
  type: "status";
  state: SpeechState;
  message: string;
}
export interface SpeechPartialEvent {
  type: "partial";
  text: string;
}
export interface SpeechFinalEvent {
  type: "final";
  text: string;
}
export interface SpeechCommitEvent {
  type: "commit";
  text: string;
}
/** 转写完成、等待用户确认原文（翻译模式）：前端显示可编辑确认框 */
export interface SpeechConfirmEvent {
  type: "confirm";
  text: string;
  source: "hotkey" | "ws";
  mode?: string;
  target_lang?: string;
}
export interface SpeechLogEvent {
  type: "log";
  message: string;
}
export interface SpeechErrorEvent {
  type: "error";
  message: string;
}
export interface SpeechReadyEvent {
  type: "ready";
  version: string;
}
export interface SpeechPongEvent {
  type: "pong";
}

export type SpeechEvent =
  | SpeechStatusEvent
  | SpeechPartialEvent
  | SpeechFinalEvent
  | SpeechCommitEvent
  | SpeechConfirmEvent
  | SpeechLogEvent
  | SpeechErrorEvent
  | SpeechReadyEvent
  | SpeechPongEvent;

export interface SpeechStartConfig {
  mode?: "verbatim" | "fluent" | "formal" | "custom";
  custom_prompt?: string;
  translate?: boolean;
  target_lang?: string;
}

export interface SpeechClientOptions {
  /** 收到任意事件回调（含 ready / pong / log） */
  onEvent?: (ev: SpeechEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: unknown) => void;
}

/**
 * 语音输入 WebSocket 客户端。
 *
 * 用法：
 *   const client = new SpeechClient({ onEvent: (ev) => ... });
 *   await client.connect();
 *   client.start({ mode: "fluent", translate: false });
 *   ...按住说话...
 *   client.stop();       // 松开结束
 *   client.abort();      // 中止
 *   client.close();
 */
export class SpeechClient {
  private ws: WebSocket | null = null;
  private readonly opts: SpeechClientOptions;
  private manualClose = false;

  constructor(opts: SpeechClientOptions = {}) {
    this.opts = opts;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  connect(): Promise<void> {
    this.manualClose = false;
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${WS_BASE}/ws/speech-input`);
      this.ws = ws;

      ws.onopen = () => {
        this.opts.onOpen?.();
        resolve();
      };
      ws.onerror = (e) => {
        this.opts.onError?.(e);
        reject(new Error("WebSocket 连接失败"));
      };
      ws.onclose = () => {
        if (!this.manualClose) this.opts.onClose?.();
      };
      ws.onmessage = (msg) => {
        try {
          const ev = JSON.parse(msg.data as string) as SpeechEvent;
          this.opts.onEvent?.(ev);
        } catch {
          /* 忽略无法解析的帧 */
        }
      };
    });
  }

  private send(payload: Record<string, unknown>) {
    if (!this.connected) throw new Error("WebSocket 未连接");
    this.ws!.send(JSON.stringify(payload));
  }

  start(config: SpeechStartConfig = {}) {
    this.send({ type: "start", config });
  }

  stop() {
    this.send({ type: "stop" });
  }

  abort() {
    this.send({ type: "abort" });
  }

  ping() {
    this.send({ type: "ping" });
  }

  close() {
    this.manualClose = true;
    this.ws?.close();
    this.ws = null;
  }
}
