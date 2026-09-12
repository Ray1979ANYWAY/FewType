// ---- 文本分块算法（平台无关，Play Books 和 Koodo 共用）----
// "一个字"怎么算：中日韩文字符（含谚文/假名）每个算 1 个（标点不算）；
// 拉丁字母连续的一串（不管多少个字母）算 1 个；阿拉伯数字连续的一串或
// 其他符号断开，一整串算 1 个；阿拉伯数字同理，中间的小数点"."不算分隔符（避免
// 3.1415926 这种数字被从中间切开）。这套定义只服务中日韩文和拉丁字符文字的电子书，
// 其它文种不特别处理——跟 Ray 核对过，用他给的例子验证过。

export function isCJKChar(ch) {
  return /[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]/.test(ch);
}
export function isLatinLetter(ch) {
  return /[A-Za-z]/.test(ch);
}
export function isAsciiDigit(ch) {
  return /[0-9]/.test(ch);
}

// 每一块最少攒够这么多"字"再考虑切——中文对话经常三五个字一个句号，纯按标点切
// 会把文本切得很碎；改成"先攒字数，字数够了再找标点收尾"，块的大小更均匀。
export const MIN_CHUNK_WORDS = 30;
// 大标点：真正的句子终结符。小标点：字数攒够之后，也允许在这些地方收尾（不用非等到大标点）。
const MAJOR_PUNCT = "。！？…";
// 半角句号/问号/叹号/分号也纳入可切标点：韩文（尤其古典小说）断句主要用半角 "."，
// 英文长句如果只有半角句号没有逗号也需要在此收尾。数字中间的小数点不会误切——
// 因为 isAsciiDigit 分支会先把 "3.14" 整体消费掉，只有非数字上下文里的 "." 才会走到这里。
const MINOR_PUNCT = "，,、：:；—.?;!";
// 标点后面如果紧跟着收尾引号/括号（"这是什么？"这种），一并吃进当前这句，
// 不然引号会甩到下一句开头，变成开头带一个孤零零引号的畸形句子送去合成。
const CLOSING_WRAP = "\"'\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\uff08\uff09()";

function isAnyPunct(ch) {
  return MAJOR_PUNCT.includes(ch) || MINOR_PUNCT.includes(ch) || CLOSING_WRAP.includes(ch);
}

export function endsWithAnyPunctuation(text) {
  const t = text.trim();
  if (!t) return true;
  const chars = Array.from(t);
  return isAnyPunct(chars[chars.length - 1]);
}

// 核心切块算法：从头开始攒"字"（按上面的定义），攒到 >= MIN_CHUNK_WORDS 之前，
// 不管中间出现多少标点都不切；攒够之后，见到下一个标点（大标点或小标点都行）就在那里切，
// 标点后面的收尾引号一起带走。
// 返回 { chunks, trailing }：chunks 是已经切好、可以直接送出去的块；trailing 是扫到文本
// 末尾还没凑够条件触发切割的剩余部分（可能为 null）——是不是要留成"续接前缀"由调用方决定
// （Play Books 用它拼接跨页的 X 区，Koodo 不需要跨页续接，可以直接把 trailing 当成
// 最后一块的一部分处理，因为整章内容一次性就全部拿到了，没有"这一页还没读完、下一页
// 还没来"这种等待场景）。
export function chunkTextByWords(text) {
  const chars = Array.from(text.trim());
  const n = chars.length;
  const chunks = [];
  let start = 0;
  let i = 0;
  let unitsSinceStart = 0;

  while (i < n) {
    const c = chars[i];
    if (isCJKChar(c)) {
      unitsSinceStart++;
      i++;
    } else if (isLatinLetter(c)) {
      while (i < n && isLatinLetter(chars[i])) i++;
      unitsSinceStart++;
    } else if (isAsciiDigit(c)) {
      while (i < n) {
        if (isAsciiDigit(chars[i])) {
          i++;
          continue;
        }
        if (chars[i] === "." && isAsciiDigit(chars[i + 1])) {
          i++; // 小数点不算分隔符
          continue;
        }
        break;
      }
      unitsSinceStart++;
    } else {
      const isPunct = MAJOR_PUNCT.includes(c) || MINOR_PUNCT.includes(c);
      if (isPunct && unitsSinceStart >= MIN_CHUNK_WORDS) {
        let end = i + 1;
        while (end < n && CLOSING_WRAP.includes(chars[end])) end++;
        const piece = chars.slice(start, end).join("").trim();
        if (piece) chunks.push(piece);
        start = end;
        i = end;
        unitsSinceStart = 0;
        continue;
      }
      i++;
    }
  }

  let trailing = null;
  if (start < n) {
    const t = chars.slice(start).join("").trim();
    trailing = t.length > 0 ? t : null;
  }
  return { chunks, trailing };
}
