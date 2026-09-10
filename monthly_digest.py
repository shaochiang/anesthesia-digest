#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文獻月報產生器
1. 從 PubMed 抓取上個月指定期刊的文章
2. 依研究類型評分，挑出最重要的幾篇
3. 用 Claude 產生中文重點摘要與當月趨勢綜述
4. 輸出成靜態網頁到 docs/
"""

import os
import re
import sys
import json
import time
import html
import datetime
import pathlib
import calendar
import xml.etree.ElementTree as ET

import requests

ROOT = pathlib.Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "").strip()
NCBI_KEY = os.environ.get("NCBI_API_KEY", "").strip()

# 依研究類型給分，分數高的優先入選
TYPE_SCORES = {
    "Practice Guideline": 10,
    "Guideline": 10,
    "Consensus Development Conference": 8,
    "Meta-Analysis": 8,
    "Randomized Controlled Trial": 7,
    "Systematic Review": 6,
    "Multicenter Study": 3,
    "Clinical Trial": 3,
    "Observational Study": 1,
    "Review": 1,
}

# 這些類型不是原始研究，直接排除
EXCLUDE_TYPES = {
    "Editorial", "Comment", "Letter", "News", "Published Erratum",
    "Retraction of Publication", "Biography", "Historical Article",
    "Newspaper Article", "Autobiography", "Bibliography",
}


# ---------------------------------------------------------------- 工具


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def target_month():
    """回傳要處理的 (年, 月)。可用環境變數 TARGET_MONTH=2026-08 指定。"""
    v = os.environ.get("TARGET_MONTH", "").strip()
    if v:
        y, m = v.split("-")
        return int(y), int(m)
    first_of_this_month = datetime.date.today().replace(day=1)
    last_month = first_of_this_month - datetime.timedelta(days=1)
    return last_month.year, last_month.month


def ncbi_params(extra):
    p = dict(extra)
    if NCBI_EMAIL:
        p["email"] = NCBI_EMAIL
        p["tool"] = "anesthesia-monthly-digest"
    if NCBI_KEY:
        p["api_key"] = NCBI_KEY
    return p


# ---------------------------------------------------------------- PubMed


def esearch(term, mindate, maxdate, retmax=400):
    params = ncbi_params({
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": retmax,
        "datetype": "pdat",
        "mindate": mindate,
        "maxdate": maxdate,
    })
    r = requests.get(f"{EUTILS}/esearch.fcgi", params=params, timeout=60)
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    time.sleep(0.4)
    return ids


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def efetch(pmids):
    """分批抓回文章詳細內容"""
    out = []
    for i in range(0, len(pmids), 100):
        chunk = pmids[i:i + 100]
        params = ncbi_params({
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
            "rettype": "abstract",
        })
        r = requests.post(f"{EUTILS}/efetch.fcgi", data=params, timeout=120)
        r.raise_for_status()
        out.extend(parse_pubmed_xml(r.text))
        time.sleep(0.4)
    return out


def parse_pubmed_xml(xml_text):
    root = ET.fromstring(xml_text)
    articles = []
    for art in root.findall(".//PubmedArticle"):
        pmid = _text(art.find(".//MedlineCitation/PMID"))
        title = _text(art.find(".//Article/ArticleTitle"))

        parts = []
        for ab in art.findall(".//Article/Abstract/AbstractText"):
            label = ab.get("Label")
            body = _text(ab)
            if not body:
                continue
            parts.append(f"{label}: {body}" if label else body)
        abstract = "\n".join(parts)

        journal = (_text(art.find(".//Journal/ISOAbbreviation"))
                   or _text(art.find(".//Journal/Title")))
        year = _text(art.find(".//Journal/JournalIssue/PubDate/Year"))

        pubtypes = [_text(p) for p in art.findall(".//PublicationTypeList/PublicationType")]

        doi = ""
        for aid in art.findall(".//ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)

        authors = []
        for a in art.findall(".//AuthorList/Author")[:3]:
            ln = _text(a.find("LastName"))
            ini = _text(a.find("Initials"))
            if ln:
                authors.append(f"{ln} {ini}".strip())

        if not pmid or not title or not abstract:
            continue
        articles.append({
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "year": year,
            "pubtypes": pubtypes,
            "doi": doi,
            "authors": authors,
        })
    return articles


def score(article, core_set):
    if set(article["pubtypes"]) & EXCLUDE_TYPES:
        return -1
    s = 0
    for t in article["pubtypes"]:
        s += TYPE_SCORES.get(t, 0)
    if article["journal"] in core_set:
        s += 2
    if len(article["abstract"]) > 900:
        s += 1
    return s


def collect(year, month):
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    mindate, maxdate = first.strftime("%Y/%m/%d"), last.strftime("%Y/%m/%d")

    core = CFG["core_journals"]
    general = CFG["general_journals"]

    q_core = "(" + " OR ".join(f'"{j}"[Journal]' for j in core) + \
             ") AND hasabstract AND english[Language]"
    q_gen = "(" + " OR ".join(f'"{j}"[Journal]' for j in general) + ") AND " + \
            CFG["general_topic_filter"] + " AND hasabstract AND english[Language]"

    ids = esearch(q_core, mindate, maxdate) + esearch(q_gen, mindate, maxdate)
    ids = list(dict.fromkeys(ids))
    log(f"PubMed 找到 {len(ids)} 篇候選文章")
    if not ids:
        return []

    arts = efetch(ids)
    core_set = set(core)
    scored = [(score(a, core_set), a) for a in arts]
    scored = [(s, a) for s, a in scored if s > 0]
    scored.sort(key=lambda x: -x[0])
    picked = [a for _, a in scored[:CFG["max_articles"]]]
    log(f"篩選後保留 {len(picked)} 篇")
    return picked


# ---------------------------------------------------------------- Claude


PROVIDER = CFG.get("provider", "gemini").lower()
MODEL = CFG["model"]
_anthropic_client = None


class FatalLLMError(Exception):
    """設定錯誤，重試也沒用，直接停下來"""


def _call_gemini(system, user, max_tokens):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise FatalLLMError(
            "找不到 GEMINI_API_KEY。請到專案 Settings → Secrets and variables "
            "→ Actions → Repository secrets，確認有一組名稱正好是 GEMINI_API_KEY 的密鑰。")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent")
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=180)

    if r.status_code == 429:
        raise RuntimeError("撞到免費層速率限制（429），稍後重試")
    if r.status_code in (400, 401, 403, 404):
        detail = r.text[:400].replace(key, "***")
        hint = ""
        if r.status_code == 404:
            hint = (f"\n  → 模型名稱「{MODEL}」可能已不存在。到 "
                    "https://ai.google.dev/gemini-api/docs/models 查目前可用的名稱，"
                    "改 config.json 的 model 欄位。")
        elif r.status_code in (400, 401, 403):
            hint = ("\n  → 金鑰無效或沒有權限。到 https://aistudio.google.com "
                    "重新產生一組，再更新 GitHub 的 GEMINI_API_KEY。"
                    "常見原因是複製時多帶了空白或換行。")
        raise FatalLLMError(f"Gemini 回傳 HTTP {r.status_code}：{detail}{hint}")

    r.raise_for_status()
    data = r.json()
    if not data.get("candidates"):
        raise RuntimeError(f"回應沒有內容：{str(data)[:300]}")
    cand = data["candidates"][0]
    if "content" not in cand:
        raise RuntimeError(f"回應被中止（{cand.get('finishReason')}）")
    return "".join(p.get("text", "") for p in cand["content"]["parts"])


def _call_anthropic(system, user, max_tokens):
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic()
    resp = _anthropic_client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


SUMMARY_SYSTEM = """你是一位資深麻醉科主治醫師，負責替科內同仁導讀當月重要文獻。
你會收到數篇英文論文的標題與摘要，請為每一篇產出繁體中文的重點整理。

要求：
- 用台灣臨床慣用的中文醫學術語，藥名、量表名等可保留英文原文。
- bottom_line 必須是「這篇研究得到什麼結論」，一句話講完，要具體（帶上關鍵數據或方向），不要空泛。
- clinical_relevance 兩到三句，說明這對日常臨床作業的意義，包含侷限性或還不能改變作法的地方。
- 誠實反映證據強度，不要誇大。摘要裡沒有的東西不要自己補。

只輸出 JSON 陣列，不要有任何前後說明文字、不要用 markdown code fence。
每個元素格式：
{"pmid":"...","title_zh":"中文標題","bottom_line":"...","clinical_relevance":"...","subspecialty":"...","evidence":"..."}

subspecialty 只能從這個清單選一個：SUBSPECIALTIES
evidence 從這些選一個：指引、統合分析、隨機對照試驗、系統性回顧、觀察性研究、綜論、其他
"""


def call_llm(system, user, max_tokens=4000):
    fn = _call_gemini if PROVIDER == "gemini" else _call_anthropic
    for attempt in range(3):
        try:
            return fn(system, user, max_tokens)
        except FatalLLMError:
            raise
        except Exception as e:
            wait = 15 * (attempt + 1)
            log(f"呼叫失敗（第 {attempt + 1}/3 次，{wait} 秒後重試）：{e}")
            time.sleep(wait)
    raise RuntimeError("連續三次呼叫失敗")


def preflight():
    """開跑前先測一次連線，設定有問題就立刻停，不要浪費十幾分鐘"""
    log(f"檢查模型連線（provider={PROVIDER}, model={MODEL}）…")
    try:
        fn = _call_gemini if PROVIDER == "gemini" else _call_anthropic
        fn("你是一個測試用的助手。", "請只回覆兩個字：正常", 50)
        log("模型連線正常")
    except FatalLLMError as e:
        log("=" * 60)
        log("設定有問題，無法繼續：")
        log(str(e))
        log("=" * 60)
        sys.exit(1)
    except Exception as e:
        log(f"連線測試失敗但可能是暫時性問題，仍繼續嘗試：{e}")


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    start = min([i for i in (t.find("["), t.find("{")) if i != -1], default=-1)
    if start > 0:
        t = t[start:]
    return json.loads(t)


def summarize(articles):
    system = SUMMARY_SYSTEM.replace(
        "SUBSPECIALTIES", "、".join(CFG["subspecialties"]))
    results = {}
    bs = CFG["batch_size"]
    for i in range(0, len(articles), bs):
        batch = articles[i:i + bs]
        blocks = []
        for a in batch:
            blocks.append(
                f"PMID: {a['pmid']}\n期刊: {a['journal']}\n"
                f"文獻類型: {', '.join(a['pubtypes']) or 'N/A'}\n"
                f"標題: {a['title']}\n摘要: {a['abstract'][:3000]}"
            )
        user = "請整理以下 %d 篇文章：\n\n%s" % (len(batch), "\n\n---\n\n".join(blocks))
        log(f"摘要中… ({i + 1}-{i + len(batch)}/{len(articles)})")
        try:
            for item in parse_json(call_llm(system, user)):
                results[str(item.get("pmid"))] = item
        except Exception as e:
            log(f"這批解析失敗，略過：{e}")
        if i + bs < len(articles):
            time.sleep(CFG.get("sleep_between_batches", 8))

    merged = []
    for a in articles:
        s = results.get(a["pmid"])
        if not s:
            continue
        a = dict(a)
        a.update({
            "title_zh": s.get("title_zh", a["title"]),
            "bottom_line": s.get("bottom_line", ""),
            "clinical_relevance": s.get("clinical_relevance", ""),
            "subspecialty": s.get("subspecialty", CFG["subspecialties"][-1]),
            "evidence": s.get("evidence", "其他"),
        })
        merged.append(a)
    return merged


TRENDS_SYSTEM = """你是資深麻醉科主治醫師兼期刊導讀人。
根據本月入選文獻的重點，寫出當月的趨勢綜述。

只輸出 JSON 物件，不要 markdown code fence，格式：
{"opening":"...","themes":[{"title":"主題名稱","body":"..."}]}

- opening：一段 80 到 120 字的開場，講本月文獻整體給人的印象。
- themes：三到五個主題。title 精簡有訊息量，body 100 到 180 字，要點名是哪些研究在支持這個觀察。
- 只根據提供的內容推論，不要引入清單以外的研究，不要編造數據。
- 繁體中文，台灣臨床用語。"""


def make_trends(articles):
    lines = [
        f"- [{a['evidence']}｜{a['subspecialty']}｜{a['journal']}] "
        f"{a['title_zh']}：{a['bottom_line']}"
        for a in articles
    ]
    user = "本月入選文獻如下：\n\n" + "\n".join(lines)
    log("撰寫當月趨勢綜述…")
    try:
        return parse_json(call_llm(TRENDS_SYSTEM, user, max_tokens=3000))
    except Exception as e:
        log(f"趨勢綜述失敗：{e}")
        return {"opening": "", "themes": []}


# ---------------------------------------------------------------- 網頁

CSS = """
:root{
  --paper:#EDEFF0; --card:#FFFFFF; --ink:#16232B; --muted:#5D6B73;
  --accent:#0E5B67; --rule:#CDD5D8;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Noto Sans TC",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:17px; line-height:1.75; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:44rem;margin:0 auto;padding:2.5rem 1.25rem 5rem}
a{color:var(--accent)}
.masthead{border-bottom:2px solid var(--ink);padding-bottom:1rem;margin-bottom:2.5rem}
.masthead .name{font-size:.95rem;letter-spacing:.02em;color:var(--muted);margin:0}
.masthead h1{
  font-family:"Noto Serif TC",Georgia,serif; font-weight:700;
  font-size:2.6rem; line-height:1.15; margin:.4rem 0 0;
}
.masthead .count{margin:.6rem 0 0;color:var(--muted);font-size:.95rem}
.overview{margin-bottom:3rem}
.overview .opening{
  font-family:"Noto Serif TC",Georgia,serif; font-size:1.2rem;
  line-height:1.85; margin:0 0 2rem;
}
.theme{border-left:3px solid var(--accent);padding-left:1.1rem;margin:1.6rem 0}
.theme h3{margin:0 0 .35rem;font-size:1.05rem}
.theme p{margin:0;color:#33434C}
h2.section{
  font-family:"Noto Serif TC",Georgia,serif;font-size:1.35rem;
  margin:3rem 0 1.2rem;padding-bottom:.4rem;border-bottom:1px solid var(--rule);
}
article.paper{background:var(--card);padding:1.4rem 1.5rem;margin:0 0 1rem;border-radius:2px}
article.paper .meta{font-size:.82rem;color:var(--muted);margin:0 0 .5rem}
article.paper .tag{
  display:inline-block;background:var(--accent);color:#fff;
  padding:.1rem .5rem;border-radius:2px;margin-right:.5rem;font-size:.78rem;
}
article.paper h3{
  font-family:"Noto Serif TC",Georgia,serif;font-size:1.15rem;
  line-height:1.5;margin:0 0 .7rem;
}
article.paper .bottom{margin:0 0 .7rem;font-weight:500}
article.paper .rel{margin:0 0 .9rem;color:#42525B;font-size:.96rem}
article.paper .src{font-size:.85rem;margin:0}
article.paper .orig{color:var(--muted);font-size:.82rem;margin:.5rem 0 0}
.foot{margin-top:4rem;padding-top:1.2rem;border-top:1px solid var(--rule);
  color:var(--muted);font-size:.85rem}
ul.months{list-style:none;padding:0;margin:0}
ul.months li{border-bottom:1px solid var(--rule);padding:.9rem 0}
ul.months a{font-family:"Noto Serif TC",Georgia,serif;font-size:1.3rem;text-decoration:none}
@media(max-width:600px){.masthead h1{font-size:2rem}.wrap{padding:1.75rem 1rem 4rem}}
"""

HEAD = """<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700&display=swap" rel="stylesheet">
<style>{css}</style>
</head><body><div class="wrap">
"""

FOOT = """<p class="foot">內容由 PubMed 摘要經 AI 整理，僅供快速導讀，臨床決策前請閱讀原文。<br>
產生時間：{ts}</p>
</div></body></html>
"""


def e(s):
    return html.escape(str(s or ""))


def render_month(year, month, articles, trends):
    label = f"{year} 年 {month} 月"
    out = [HEAD.format(title=f"{label}麻醉科文獻月報", css=CSS)]
    out.append('<header class="masthead">')
    out.append(f'<p class="name">{e(CFG["site_title"])}　·　<a href="index.html">回月份總覽</a></p>')
    out.append(f"<h1>{e(label)}</h1>")
    out.append(f'<p class="count">本月入選 {len(articles)} 篇</p>')
    out.append("</header>")

    if trends.get("opening") or trends.get("themes"):
        out.append('<section class="overview">')
        if trends.get("opening"):
            out.append(f'<p class="opening">{e(trends["opening"])}</p>')
        for t in trends.get("themes", []):
            out.append('<div class="theme">'
                       f'<h3>{e(t.get("title"))}</h3>'
                       f'<p>{e(t.get("body"))}</p></div>')
        out.append("</section>")

    for sub in CFG["subspecialties"]:
        group = [a for a in articles if a.get("subspecialty") == sub]
        if not group:
            continue
        out.append(f'<h2 class="section">{e(sub)}</h2>')
        for a in group:
            authors = "、".join(a["authors"])
            if len(a["authors"]) >= 3:
                authors += " 等"
            link = (f"https://doi.org/{a['doi']}" if a["doi"]
                    else f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/")
            out.append('<article class="paper">')
            out.append(f'<p class="meta"><span class="tag">{e(a["evidence"])}</span>'
                       f'{e(a["journal"])}　{e(authors)}</p>')
            out.append(f'<h3>{e(a["title_zh"])}</h3>')
            out.append(f'<p class="bottom">{e(a["bottom_line"])}</p>')
            out.append(f'<p class="rel">{e(a["clinical_relevance"])}</p>')
            out.append(f'<p class="src"><a href="{e(link)}" target="_blank" rel="noopener">'
                       f'讀原文</a>　·　<a href="https://pubmed.ncbi.nlm.nih.gov/{e(a["pmid"])}/" '
                       f'target="_blank" rel="noopener">PubMed {e(a["pmid"])}</a></p>')
            out.append(f'<p class="orig">{e(a["title"])}</p>')
            out.append("</article>")

    out.append(FOOT.format(ts=f"{datetime.datetime.now():%Y-%m-%d %H:%M}"))
    return "".join(out)


def render_index():
    files = sorted(
        [p for p in DOCS.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].html")],
        reverse=True)
    out = [HEAD.format(title=CFG["site_title"], css=CSS)]
    out.append('<header class="masthead">')
    out.append(f'<p class="name">{e(CFG["site_subtitle"])}</p>')
    out.append(f'<h1>{e(CFG["site_title"])}</h1></header>')
    if not files:
        out.append("<p>還沒有任何月報。到 Actions 頁面手動執行一次就會出現。</p>")
    out.append('<ul class="months">')
    for p in files:
        y, m = p.stem.split("-")
        out.append(f'<li><a href="{p.name}">{int(y)} 年 {int(m)} 月</a></li>')
    out.append("</ul>")
    out.append(FOOT.format(ts=f"{datetime.datetime.now():%Y-%m-%d %H:%M}"))
    return "".join(out)


# ---------------------------------------------------------------- 主流程


def main():
    year, month = target_month()
    log(f"開始產生 {year}-{month:02d} 月報")
    DOCS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    preflight()

    articles = collect(year, month)
    if not articles:
        log("這個月沒有抓到文章，結束。")
        return

    articles = summarize(articles)
    if not articles:
        log("摘要全部失敗，結束。")
        sys.exit(1)

    trends = make_trends(articles)

    stem = f"{year}-{month:02d}"
    (DATA / f"{stem}.json").write_text(
        json.dumps({"articles": articles, "trends": trends},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (DOCS / f"{stem}.html").write_text(
        render_month(year, month, articles, trends), encoding="utf-8")
    (DOCS / "index.html").write_text(render_index(), encoding="utf-8")
    log(f"完成：docs/{stem}.html")


if __name__ == "__main__":
    main()
