"""folder_advisor — フォルダ体系を取得し、LLM で改善提案するツール。

- 対象: ローカルフォルダ / OneDrive（同期フォルダ or Microsoft Graph API）
- 収集: メタデータのみ（ファイル内容は読まない・送らない）
- LLM: Azure OpenAI（Azure CLI 認証。API キー不要）
"""

__version__ = "2.0.0"
