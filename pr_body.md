### 🔍 AI 根因診斷
在 call_gemini_smart 函式中，當 Google GenAI SDK 呼叫失敗或模型回應不符合預期（例如回傳型態非預期、或某些情況下回傳了 None），response.parsed 可能為 None。當後續程式碼直接嘗試對回傳結果進行屬性存取或解構時（例如 batch_res.items 或 review.score），就會引發 'NoneType' object is not subscriptable 或屬性錯誤，導致腳本崩潰。

### 🛠️ 修復策略
在 call_gemini_smart 函式中增加對 response.parsed 是否為 None 的防禦性檢查；同時在主流程呼叫模型後檢查回傳物件是否有效，若為 None 則主動拋出例外或略過，避免型態錯誤導致未預期中斷。