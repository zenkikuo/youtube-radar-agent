### 🔍 AI 根因診斷
在 `fetch_transcript` 函數的 `except` 區塊中，存在一個未閉合的三引號字串 `return """`。這導致 Python 直譯器將其後的所有程式碼（包括後續的函數定義和主執行邏輯）都視為該字串的一部分，直到檔案結束。當直譯器最終遇到 `if report["error_count"] > 0:` 這樣的語句時，由於它仍在一個未閉合的字串內部，因此拋出了 `SyntaxError: invalid syntax`。GitHub Actions 的錯誤日誌可能將整個腳本壓縮成一行，導致錯誤顯示在「line 1」。

### 🛠️ 修復策略
將 `fetch_transcript` 函數中 `except` 區塊的 `return """` 修改為 `return ""`。這樣，當字幕抓取失敗時，函數將返回一個空的字串，而不是導致整個腳本的語法錯誤。