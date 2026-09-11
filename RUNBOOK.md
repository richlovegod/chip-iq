# chip-iq 每日健檢與維修手冊（RUNBOOK）

給「每天檢查一次站台、壞了就修」的人或 AI 看。目前由排程中的 Claude Code 在每個交易日隔天台北早上執行；交接後誰接手都照這份做。

站台本身的更新由 GitHub Actions 負責（`.github/workflows/daily.yml`，台北 18:17、20:47，隔天 06:00 補跑班），三道守門員沒過就不發佈。這份手冊處理的是「守門員擋下了、或排程根本沒跑成」之後的事。

## 一、什麼叫「正常」

- **預期資料日** ＝ 台北今天之前最近的一個週一～週五（遇國定假日往前推）
- 線上下列資料的最新日期都等於預期資料日：
  - `quote_daily.json` 的 series 最後一筆
  - `broker_daily.json` 的 `date_to`
  - `universe.json` 與 `peers.json` 的 `as_of`
  - `partners.json` 各家的 `as_of`（日本、韓國休市日與台灣不同，差 1 個交易日可接受）
- `meta.json` 的 `generated_at` 在過去 24 小時內
- 過去 24 小時的排程至少一次 success
- workflow 是 active（GitHub 會停用連續 60 天沒活動的 repo 的排程）

## 二、檢查步驟（照順序）

1. **本機 repo 狀態**：`git status`。有 `data/`、`cache/` 以外的未提交修改 → 有人正在改程式，**不要 pull、不要改**，只做唯讀檢查並記錄。乾淨 → `git pull --rebase origin main`。
2. **時間一律用台北時間**。Windows 的 Git Bash 沒有時區資料，`TZ=Asia/Taipei date` 會默默回傳 UTC；用 Python：`datetime.now(timezone(timedelta(hours=8)))`。
3. **排程紀錄**：`gh run list --workflow=daily.yml --limit 12 --json databaseId,event,status,conclusion,createdAt`，createdAt 換成台北時間。班次對應：前一交易日 18:17 之後的第一個 schedule run ＝ 18:17 班、第二個 ＝ 20:47 班；check job 的 log 印出「昨晚已更新」或「補跑」的那個 ＝ 06:00 班。
4. **有 queued／in_progress 的 run** → `gh run watch <id> --exit-status` 等它跑完再判斷（一次約 7～10 分鐘），不要另外觸發。
5. **線上資料**：`curl -H "Cache-Control: no-cache"` 抓 `https://richlovegod.github.io/chip-iq/data/{meta,quote_daily,broker_daily,universe,peers,partners}.json`，比對第一節。
6. **本機跑守門員**（pull 之後本機資料就是線上資料）：
   ```bash
   python scripts/verify_broker.py
   python scripts/verify_partners.py
   python scripts/verify_partners.py --self-test
   python scripts/verify_site.py
   ```
7. **workflow 狀態**：`gh workflow list`。daily.yml 不是 active → `gh workflow enable daily.yml`。

## 三、判斷與處理

| 狀況 | 處理 |
|---|---|
| 全部正常 | 只記一行 |
| 有失敗的 run，但之後有一班成功、資料是新的 | `gh run view <id> --log-failed` 記下原因。已知暫時性原因 → 不動 |
| 資料沒到預期資料日，最近的 run 都失敗 | 讀失敗 log 分類。暫時性 → `gh workflow run daily.yml`，`gh run watch` 到跑完，再驗一次線上 |
| 資料沒到預期資料日，run 都成功 | 先查是不是國定假日：`https://www.tpex.org.tw/www/zh-tw/emerging/dailyList?date=YYYY/MM/DD&response=json` 那天沒有 EMdss004 就是休市 → 正常 |
| 失敗原因是程式問題（守門員誤擋、來源格式改了、解析錯、Actions 版本淘汰） | 依第四節修程式 |
| 守門員正確擋下了錯的資料（來源真的給錯） | **不要放寬守門員。** 查來源，能換端點或修解析就修；不能就記錄 🔴 等人 |

**已知的暫時性原因**（log 裡的關鍵字）：`暫停使用`（TWSE 尖峰時段停用全市場查詢）、`HTTP Error 5xx`、`IncompleteRead`、`TimeoutError`、`JSONDecodeError`、`比上一版 … 舊`（Yahoo 偶發回舊資料，程式已自動沿用上一版）。

**重新觸發的時間限制**：台北 **13:30～14:30 不要觸發**（TWSE 停用全市場查詢，實測到 14:05 仍擋）。盤中到 18:00 前觸發會拿到日期不一致的快照，全站檢查只警告、照常發佈，可以接受但不理想。

## 四、修程式的規則

**可以做**：修抓資料腳本、守門員、前端的 bug；補回漏抓的資料（跑對應的 fetch 腳本）；GitHub Actions 版本被淘汰時升級。

**修完必須全部做到才能 push**：

1. 第二節第 6 步的四道檢查全過。
2. **改到守門員時**，要用情境測試證明兩件事：誤擋的情況現在通過、**真的錯誤仍然擋下**（複製一份資料、注入錯誤、跑檢查、還原）。2026-09 的 C9、C11 修正就是這樣做的，看 `git log`。
3. 版本號三處一起改：`data/versions.json` 最前面加一筆（只修 bug → 第三位進位；寫症狀、原因、修法）、`index.html` 頁首與頁尾的版本號。
4. commit 訊息寫「為什麼」，不只寫改了什麼。
5. push 後手動觸發一次（避開 13:30～14:30），跑到綠，確認線上資料與版本號。

**不可以做**：

- **為了讓檢查通過而放寬守門員**——除非第 2 點能證明那是誤擋
- 手動改 `data/*.json` 的數字，或手動 commit 資料假裝更新過
- 改 `data/partners_ref.json` 的股數與公司事件（人工查證維護），改黃金樣本（`broker_fixture.json`、`verify_partners.py` 的 GOLDEN／GOLDEN_CAP）
- 刪 `cache/`、force push、改寫 git 歷史
- 寄信或對外發訊息

**同一個問題修兩次還是失敗 → 停手**，把兩次嘗試與 log 記錄下來，標 🔴 等人。需要人拍板的事（換資料源、改排程架構、要不要外部觸發）只寫建議，不自己做。

## 五、紀錄

每次都記：日期（台北）、結論（✅ 正常／🟡 有狀況已處理／🔴 需要人）、前一交易日各班的實際開跑時間與結果、線上資料日。有處理的另寫：症狀、原因、做了什麼、commit 或 run 連結。紀錄放在哪由執行者的排程設定指定。
