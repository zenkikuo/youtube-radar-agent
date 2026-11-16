### 🔍 AI 根因診斷 (Root Cause)
精準根因分析：`main.py` 檔案中 `fetch_transcript` 函數的 `except` 區塊存在 `SyntaxError`。具體來說，約第 100 行的 `return """` 是一個未終止的三引號字串，導致 Python 直譯器在解析腳本時失敗，無法啟動程式。儘管錯誤日誌指向 `line 1`，這通常是解析器在遇到無法恢復的語法錯誤時的通用行為，實際問題點在於 `fetch_transcript` 函數的錯誤處理邏輯。

### 🛠️ 修復策略
修復策略：將 `main.py` 中 `fetch_transcript` 函數 `except` 區塊的 `return """` 修改為 `return ""`。這將確保在字幕抓取失敗時，函數能正確返回一個空字串，避免語法錯誤，並允許程式繼續執行。其他在 PR body 中提到的 Gemini API 額度限制、`time.sleep` 時間以及 `config.yaml` 中的 `handle` 問題，經檢查已在目前的 `main.py` 和 `config.yaml` 中得到修復，無需再次改動。