### 🔍 AI 根因診斷
在調用 call_gemini_smart 函數時，如果所有的備援模型皆回傳無效或失敗，該函數會主動拋出 QuotaExhaustedError 例外。然而，在主程序中的 except Exception as e 區塊中，會將此例外捕獲並印出，導致 report['error_count'] += 1，最後進入 if report['error_count'] > 0: sys.exit(1)，使任務被當作嚴重錯誤終止。此外，程式碼中未對 webhook 的環境變數鍵值進行安全取值，可能導致 NoneType 物件無法進行字串操作或其他未預期的 NoneType 存取錯誤。

### 🛠️ 修復策略
將 QuotaExhaustedError 獨立在 except 區塊中捕捉並進行優雅降級（Graceful degradation），不增加 error_count，避免直接觸發 sys.exit(1)；同時在取得 webhook URL 時加入防呆判斷，確保傳入 requests 的 URL 不會是 None 或空值造成崩潰。