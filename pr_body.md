### 🔍 AI 根因診斷
config.yaml 檔案第一行第二十四個字元附近（或 YAML 結構本身）存在解析錯誤。由於 PyYAML 在讀取含有不合法縮排、字串引號未閉合、或格式錯誤的 config.yaml 時會拋出 ScannerError（對應錯誤訊息 mapping values are not allowed here），導致主程式在啟動時崩潰。這通常是由於 GitHub Actions 執行環境中的 workflow 未正確寫入 config.yaml 檔案，或者 YAML 內容格式有誤所致。

### 🛠️ 修復策略
在 main.py 中載入 config.yaml 時增加防禦性處理與詳細的錯誤提示。同時，確保腳本若找不到 config.yaml 或檔案解析失敗時，能提供清晰的除錯指引並安全終止，避免因未捕獲的例外導致直接崩潰。另外，在程式碼頂部確保所有標準函式庫與 PyYAML 正確相容。