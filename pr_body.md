### 🔍 AI 根因診斷
在處理批次頻道預覽結果時，若 Gemini 回傳的 items 陣列中包含 None 物件（例如 batch_res.items 為 [None] 或其中某個元素為 None），程式碼直接呼叫 getattr(item, "video_id", None) 會引發 AttributeError: 'NoneType' object has no attribute 'video_id'。雖然外層有 try...except Exception 捕捉並印出 '❌ 執行過程發生未預期錯誤: 'NoneType' object is not subscriptable'（或因其他地方對 None 進行下標操作引發），根本原因在於對 AI 回傳的結構體或其內部清單元素未做嚴格的型別與空值防護。

### 🛠️ 修復策略
1. 確保 call_gemini_smart 回傳的解析物件以及其內部列表元素在存取前都進行非空檢查（if item is not None and getattr(item, "video_id", None):）。
2. 確保迴圈與屬性存取具備防禦性編程（Defensive Programming），避免任一 API 回傳不合預期的 None 型別導致整個主線腳本崩潰或直接跳入未預期錯誤捕捉中。