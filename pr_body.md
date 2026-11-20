### 🔍 AI 根因診斷
The error `'NoneType' object is not subscriptable` occurred because `call_gemini_smart` returned `None` when API quotas or parsing issues occurred, but the code did not check if the `review` variable was `None` before attempting to access its attributes like `review.score` inside the radar processing loop.

### 🛠️ 修復策略
Add a safety check right after calling `call_gemini_smart` for the radar review to ensure `review` is not `None` before checking its `score` attribute, preventing the `NoneType` subscriptable or attribute access error.