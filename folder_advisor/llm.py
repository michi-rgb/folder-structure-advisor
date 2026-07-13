"""Azure OpenAI クライアント（Azure CLI 認証）。

API キーは使わない。`az login` 済みの Azure CLI 資格情報から
azure-identity がトークンを取得する（Cognitive Services OpenAI User 以上の
ロールが必要）。トークンは自動更新されるため長時間実行でも失効しない。

必要な環境変数:
  AZURE_OPENAI_ENDPOINT    例 https://<resource>.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT  デプロイ名（モデル名ではない）
任意:
  AZURE_OPENAI_API_VERSION 既定 2024-10-21
  AZURE_OPENAI_API_KEY     設定時のみキー認証にフォールバック（非推奨）
"""
from __future__ import annotations

import json
import os

DEFAULT_API_VERSION = "2024-10-21"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class LLMConfigError(RuntimeError):
    pass


def build_client():
    """(client, deployment) を返す。設定不足・依存不足は LLMConfigError。"""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
    if not endpoint or not deployment:
        raise LLMConfigError(
            "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT を設定してください。"
        )
    try:
        from openai import AzureOpenAI
    except ImportError as e:
        raise LLMConfigError("`pip install openai azure-identity` を実行してください。") from e

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION)
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    if api_key:
        client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        return client, deployment

    try:
        from azure.identity import AzureCliCredential, get_bearer_token_provider
    except ImportError as e:
        raise LLMConfigError("`pip install azure-identity` を実行してください。") from e
    token_provider = get_bearer_token_provider(AzureCliCredential(), TOKEN_SCOPE)
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )
    return client, deployment


def chat_json(system: str, user: str, temperature: float = 0.2) -> dict:
    """1 回のチャット呼び出しで JSON 応答を得る。

    通信量削減のため呼び出しは提案生成につき 1 回のみ。失敗はそのまま送出し、
    呼び出し側（propose.py）がルールベースにフォールバックする。
    """
    client, deployment = build_client()
    resp = client.chat.completions.create(
        model=deployment,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)
