### 🔍 AI 根因診斷
config.yaml 的第一行第二十四個字元附近（或整個檔案的縮排/格式）包含了 YAML 不允許的字元或格式錯誤，導致 yaml.safe_load 拋出 `mapping values are not allowed here` 異常。

### 🛠️ 修復策略
在讀取 config.yaml 時加入更具防禦性的錯誤處理與日誌提示，並確保 YAML 內容在解析失敗時不會直接崩潰，同時提供預設防呆結構或清楚指示使用者檢查 YAML 語法。