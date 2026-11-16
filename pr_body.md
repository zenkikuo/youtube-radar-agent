### 🔍 AI 根因診斷
在處理批次頻道摘要（BatchChannelPreviews）的回傳結果時，程式碼直接使用迴圈 `for item in batch_res.items:`。當 `batch_res` 因某種原因（例如 Pydantic 解析異常或模型回傳結構不完全符合）回傳 `None` 時，存取 `.items` 屬性會引發 'NoneType' object has not attribute 'items'，或者在後續從 `batch_res.items` 取回的 `item` 為空時導致 `item.video_id` 引發 'NoneType' object is not subscriptable / not accessible。此外，`call_gemini_smart` 回傳時若未正確驗證，也可能導致呼叫端接收到 None 物件。

### 🛠️ 修復策略
在 main.py 的批次處理與雷達審閱迴圈中，加入對 `batch_res` 與 `review` 的型態檢查與防禦性編碼（確認 `batch_res` 不為 None 且具備 `items` 屬性才進行走訪；確保每次取得的 `item` 或 `review` 不為 None），避免因為 None 物件呼叫屬性而導致主流程崩潰。