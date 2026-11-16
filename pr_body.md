### 🔍 AI 根因診斷
config.yaml 檔案第一行第二十四個字元附近存在 YAML 語法錯誤（例如 Tab 與空白混用、冒號後缺少空格或引號未正確閉合），導致 yaml.safe_load 解析失敗並拋出 mapping values are not allowed here 異常。

### 🛠️ 修復策略
在 main.py 的 config.yaml 載入前加上檢查與錯誤提示防護，並在儲存或檢查 config 讀取時進行嚴格的格式驗證；另外提供一份乾淨的預設配置初始化機制，確保工作流程即使在設定檔有誤時也不會直接崩潰。