### 🔍 AI 根因診斷
YAML syntax error or malformed structure in config.yaml (indicated by yaml.scanner.ScannerError: mapping values are not allowed here at line 1, column 24). However, as an architectural best practice, main.py should also be robustly protected against any file loading or YAML parsing exceptions, gracefully alerting and exiting instead of crashing abruptly.

### 🛠️ 修復策略
Wrap the config file loading block in a try-except statement inside main.py to catch yaml.YAMLError or FileNotFoundError, logging a clean error message and exiting safely with sys.exit(1).