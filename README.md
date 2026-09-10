# 麻醉科文獻月報

每個月自動從 PubMed 抓取麻醉、疼痛、重症相關的重要文獻，用 AI 整理成中文重點與趨勢綜述，發布成一個網頁。

完全不需要寫程式，也不需要自己的電腦開著，全部在 GitHub 上跑。

---

## 一次性設定（大約 30 分鐘）

### 第 1 步：申請 AI 的 API key（免費）

預設用 Google Gemini 的免費層，不用綁信用卡。

1. 用 Google 帳號登入 <https://aistudio.google.com>
2. 左側選 API keys → Create API key
3. 把那串複製下來先貼在記事本

免費層有速率限制（每分鐘、每天的請求數上限），本程式已經在每批之間自動等待，
一個月只跑一次、二十幾篇文章，完全在免費範圍內。

注意：免費層送出的內容 Google 可能會用來改進產品。本程式只會送出 PubMed 的公開摘要，
不會有任何病人資料，所以沒有問題。但也請你不要把病歷內容貼進這個系統。

**想改用 Claude（付費，摘要品質較好）**：到 <https://console.anthropic.com> 儲值 5 美金並建立 key，
然後把 `config.json` 改成 `"provider": "anthropic"`、`"model": "claude-sonnet-5"`，
第 5 步的密鑰名稱改存 `ANTHROPIC_API_KEY`。每月大約 0.2 到 0.5 美金。

### 第 2 步：申請 NCBI API key（選用，但建議）

1. 到 <https://account.ncbi.nlm.nih.gov> 註冊
2. 右上角帳號 → Account settings → API Key Management → 產生一組

沒有也能跑，只是抓資料速度限制比較嚴格。

### 第 3 步：建立 GitHub 專案

1. 到 <https://github.com> 註冊帳號
2. 右上角 `+` → New repository
3. Repository name 填 `anesthesia-digest`
4. 選 **Public**（Public 才能免費用 GitHub Pages）
5. 勾選 Add a README file
6. 按 Create repository

### 第 4 步：把檔案上傳上去

在專案頁面按 `Add file` → `Upload files`，把本資料夾裡的所有東西整包拖進去，包含：

```
monthly_digest.py
config.json
requirements.txt
README.md
docs/index.html
.github/workflows/monthly.yml
```

注意：`.github` 是隱藏資料夾。如果拖曳時看不到它，改用 `Add file` → `Create new file`，
在檔名欄輸入 `.github/workflows/monthly.yml`（斜線會自動變成資料夾），再把該檔內容貼進去。

拖完後捲到最下面按 `Commit changes`。

### 第 5 步：把金鑰存進 GitHub

在專案頁面 → `Settings` → 左欄 `Secrets and variables` → `Actions` → `New repository secret`。

依序新增（Name 要一字不差）：

| Name | Secret |
|---|---|
| `GEMINI_API_KEY` | 第 1 步拿到的那串 |
| `NCBI_API_KEY` | 第 2 步拿到的（沒有就跳過） |
| `NCBI_EMAIL` | 你的 email |

（如果你選了 Claude，第一列改成 `ANTHROPIC_API_KEY`。）

存在這裡是加密的，別人看不到，也不會出現在網頁上。

### 第 6 步：允許 Actions 寫入檔案

`Settings` → 左欄 `Actions` → `General` → 捲到 Workflow permissions →
選 **Read and write permissions** → `Save`。

沒做這步，程式跑完會無法把網頁存回去。

### 第 7 步：打開網站

`Settings` → 左欄 `Pages` → Source 選 `Deploy from a branch` →
Branch 選 `main`、資料夾選 `/docs` → `Save`。

等一兩分鐘，網址會顯示在同一頁，長得像
`https://你的帳號.github.io/anesthesia-digest/`

### 第 8 步：手動跑第一次

專案頁面 → 上方 `Actions` 分頁 → 左欄點 `產生月報` → 右邊 `Run workflow` →
（月份欄可留空，代表上個月）→ 按綠色 `Run workflow`。

大約 3 到 6 分鐘。跑完後重新整理你的網站網址就會看到月報。

設定到此結束。之後每個月 6 號早上會自動更新，你不用再做任何事。

---

## 日常調整

### 改期刊清單

編輯 `config.json`。在 GitHub 上點該檔案 → 右上鉛筆圖示 → 改完按 Commit。

- `core_journals`：專科期刊，這些期刊的文章全部納入候選
- `general_journals`：綜合期刊，只納入符合麻醉／重症主題的文章
- `max_articles`：每月要看幾篇（預設 24，想輕鬆一點可改 15）

期刊名稱要用 PubMed 的縮寫。查法：到 PubMed 搜尋該期刊任一篇文章，看引用格式裡的期刊縮寫，
或到 <https://www.ncbi.nlm.nih.gov/nlmcatalog/journals> 查。

### 補做過去的月份

Actions → 產生月報 → Run workflow → 在月份欄填 `2026-05` 之類的，按執行。

### 換模型

`config.json` 裡的 `provider` 和 `model` 兩個欄位。

| 想要 | provider | model | 費用 |
|---|---|---|---|
| 免費 | `gemini` | `gemini-2.5-flash` | 0 元 |
| 摘要品質更好 | `anthropic` | `claude-sonnet-5` | 每月約 0.2–0.5 美金 |
| 分析最深入 | `anthropic` | `claude-opus-5` | 每月約 0.5–1 美金 |

模型名稱偶爾會更新，若跑失敗顯示找不到模型，到
<https://ai.google.dev/gemini-api/docs/models> 或
<https://docs.claude.com/en/docs/about-claude/models> 查目前的名稱換上去。

### 如果連免費層都不想用

把 `max_articles` 調小、`provider` 留 `gemini` 就好，一個月一次的用量離免費上限很遠。
真的想完全不碰 AI，也可以請我改成「只列出標題、期刊、研究類型與連結」的純清單版本，
那樣完全不需要任何 API key，但就沒有中文摘要和趨勢綜述了。

---

## 運作方式

1. 用 PubMed E-utilities API 依「期刊 + 出版月份」搜尋
2. 排除社論、讀者投書、勘誤等非原始研究
3. 依文獻類型評分（指引與統合分析最高，其次隨機對照試驗），取分數最高的 N 篇
4. 分批送給 Claude，產生每篇的中文結論與臨床意義，並歸類次專科
5. 再送一次，產生當月趨勢綜述
6. 產生靜態 HTML 放到 `docs/`，由 GitHub Pages 發布

只會抓取和儲存 PubMed 的公開摘要與連結，不會下載全文，避免版權問題。

## 注意

AI 摘要可能會有誤讀或過度簡化。這份月報的定位是幫你決定「這個月哪幾篇值得花時間讀原文」，
不能取代閱讀原文，臨床決策前務必回頭看原始論文。
