"""Azure OpenAI / Mistral API クライアント。

プロバイダ選択は CLI 引数 `--llm-provider`（azure | mistral、既定 azure）で行う
（cli.py 参照）。API キー・モデル名などの秘匿情報のみ環境変数から読み取る。

--- Azure OpenAI（既定・Azure CLI 認証、API キー不要）---
必要な環境変数:
  AZURE_OPENAI_ENDPOINT    例 https://<resource>.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT  デプロイ名（モデル名ではない）
任意:
  AZURE_OPENAI_API_VERSION 既定 2024-10-21
  AZURE_OPENAI_API_KEY     設定時のみキー認証にフォールバック（非推奨）
API キーは使わない。`az login` 済みの Azure CLI 資格情報から
azure-identity がトークンを取得する（Cognitive Services OpenAI User 以上の
ロールが必要）。トークンは自動更新されるため長時間実行でも失効しない。

--- Mistral API ---
必要な環境変数（Windows のシステム環境変数として設定する想定）:
  MISTRAL_API_KEY  API キー
  MISTRAL_MODEL    モデル名（例 mistral-large-latest）
"""
from __future__ import annotations

import json
import os

DEFAULT_API_VERSION = "2024-10-21"
DEFAULT_PROVIDER = "azure"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class LLMConfigError(RuntimeError):
    pass


def _estimate_tokens(text: str) -> int:
    """概算トークン数（正確なトークナイザは使わない簡易見積もり）。

    ASCII は 4 文字/トークン、非 ASCII（日本語等）は 1 文字/トークン目安で計算。
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other_chars = len(text) - ascii_chars
    return ascii_chars // 4 + other_chars


def _confirm_send(system: str, user: str, provider: str) -> None:
    """送信前に推定トークン数を表示し、y/n で確認する。'n' なら LLMConfigError。"""
    est = _estimate_tokens(system) + _estimate_tokens(user)
    print(f"[llm] プロバイダ: {provider} / 推定トークン数: 約 {est:,} トークン")
    answer = input("[llm] この内容を LLM に送信しますか？ (y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        raise LLMConfigError("ユーザーが送信をキャンセルしました。")


def _build_azure_client():
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


def _build_mistral_client():
    """(client, model) を返す。設定不足・依存不足は LLMConfigError。"""
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    model = os.environ.get("MISTRAL_MODEL", "").strip()
    if not api_key or not model:
        raise LLMConfigError("MISTRAL_API_KEY / MISTRAL_MODEL を設定してください。")
    try:
        from mistralai import Mistral
    except ImportError as e:
        raise LLMConfigError("`pip install mistralai` を実行してください。") from e
    client = Mistral(api_key=api_key)
    return client, model


def chat_json(system: str, user: str, temperature: float = 0.2, provider: str = DEFAULT_PROVIDER) -> dict:
    """1 回のチャット呼び出しで JSON 応答を得る。

    通信量削減のため呼び出しは提案生成につき 1 回のみ。失敗はそのまま送出し、
    呼び出し側（propose.py）がルールベースにフォールバックする（ユーザーが
    送信を拒否した場合も同様にフォールバックする）。
    provider は CLI 引数 `--llm-provider`（azure | mistral）から渡される。
    """
    provider = (provider or DEFAULT_PROVIDER).strip().lower()
    _confirm_send(system, user, provider)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if provider == "azure":
        client, deployment = _build_azure_client()
        resp = client.chat.completions.create(
            model=deployment,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=messages,
        )
        content = resp.choices[0].message.content or "{}"
    elif provider == "mistral":
        client, model = _build_mistral_client()
        resp = client.chat.complete(
            model=model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=messages,
        )
        content = resp.choices[0].message.content or "{}"
    else:
        raise LLMConfigError(
            f"--llm-provider の値が不正です: {provider!r}（azure か mistral を指定してください）"
        )
    return json.loads(content)
