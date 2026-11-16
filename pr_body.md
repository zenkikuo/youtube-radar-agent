### 🔍 AI 根因診斷
config.yaml 檔案第一行第二十四個字元（或其附近結構）包含不合法的 YAML 對應值格式錯誤（例如未正確縮排、冒號後缺少空格或字串引用不全），導致 PyYAML 解析失敗並丟出 mapping values are not allowed here 異常。

### 🛠️ 修復策略
在 main.py 的 YAML 讀取防護邏輯中加入更詳細的例外捕獲與提示，並確保即使 config.yaml 格式錯誤也能提供友善的錯誤診斷指引，同時由於此錯誤源自外部設定檔語法，程式碼本身已妥善捕捉 yaml.YAMLError，確保環境部署時檢查 config.yaml 的格式正確性即可。