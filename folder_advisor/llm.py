"""LLM 補助（Azure OpenAI / Mistral 切替対応）。

任意機能。接続情報が無い／SDK 未インストール／API 失敗のいずれの場合も例外を
投げず `None` を返し、呼び出し側はルールベース結果のみで完結する。

送信するのは **ファイル名・フォルダ名のリストのみ**。ファイル内容は一切送らない
（社外秘リスク低減）。

プロバイダ選択（`--llm-provider` / provider_name）:
  auto    ... 認証情報が揃っている方を自動選択（Azure を優先、無ければ Mistral）
  azure   ... Azure OpenAI を使用
  mistral ... Mistral を使用

必要な環境変数:
  [Azure OpenAI]
    AZURE_OPENAI_ENDPOINT     例) https://<resource>.openai.azure.com/
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_DEPLOYMENT   デプロイ名（モデル名ではなくデプロイ名）
    AZURE_OPENAI_API_VERSION  例) 2024-10-21
  [Mistral]
    MISTRAL_API_KEY
    MISTRAL_MODEL             省略時 mistral-large-latest
    MISTRAL_BASE_URL          （任意）自ホスト/Azure AI 上の Mistral 用エンドポイント
"""

from __future__ import annotations

import json
import os
from typing import Optional


class LLMProvider:
    """LLM プロバイダの共通インターフェース。"""

    name = "none"

    @property
    def available(self) -> bool:  # 認証情報が揃っているか
        return False

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        """system/user を渡して JSON オブジェクトを得る。失敗時 None。"""
        return None


class AzureOpenAIProvider(LLMProvider):
    name = "azure"

    def __init__(self) -> None:
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        self.deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
        self._client = None
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if not self.available:
            return None
        try:
            from openai import AzureOpenAI
        except ImportError:
            self.last_error = "openai パッケージが未インストールです（pip install openai）"
            return None
        try:
            self._client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"AzureOpenAI 初期化失敗: {exc}"
            return None
        return self._client

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        client = self._client_or_none()
        if client is None:
            return None
        try:
            resp = client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Azure OpenAI 呼び出し失敗: {exc}"
            return None


class MistralProvider(LLMProvider):
    name = "mistral"

    def __init__(self) -> None:
        self.api_key = os.getenv("MISTRAL_API_KEY", "").strip()
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest").strip()
        self.base_url = os.getenv("MISTRAL_BASE_URL", "").strip() or None
        self._client = None
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if not self.available:
            return None
        try:
            from mistralai import Mistral
        except ImportError:
            self.last_error = "mistralai パッケージが未インストールです（pip install mistralai）"
            return None
        try:
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["server_url"] = self.base_url
            self._client = Mistral(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Mistral 初期化失敗: {exc}"
            return None
        return self._client

    def chat_json(self, system: str, user: str) -> Optional[dict]:
        client = self._client_or_none()
        if client is None:
            return None
        try:
            resp = client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            return json.loads(resp.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"Mistral 呼び出し失敗: {exc}"
            return None


def get_provider(provider_name: str = "auto") -> LLMProvider:
    """プロバイダ名から実装を返す。auto は認証情報が揃っている方を選ぶ。"""
    name = (provider_name or "auto").lower()
    if name == "azure":
        return AzureOpenAIProvider()
    if name == "mistral":
        return MistralProvider()
    # auto: Azure を優先し、無ければ Mistral。どちらも無ければ Azure（未認証）を返す。
    azure = AzureOpenAIProvider()
    if azure.available:
        return azure
    mistral = MistralProvider()
    if mistral.available:
        return mistral
    return azure


class LLMHelper:
    """分類・命名規約提案の補助。内部でプロバイダに委譲する。"""

    def __init__(
        self, provider_name: str = "auto", provider: Optional[LLMProvider] = None
    ) -> None:
        self.provider = provider or get_provider(provider_name)

    @property
    def available(self) -> bool:
        return self.provider.available

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def last_error(self) -> Optional[str]:
        return getattr(self.provider, "last_error", None)

    def suggest_categories(self, names: list[str]) -> Optional[dict[str, str]]:
        """ファイル名リストから {ファイル名: カテゴリ} の意味分類案を返す。"""
        if not self.available or not names:
            return None
        sample = names[:200]  # 送信量を抑える
        system = (
            "あなたは社内文書のファイル管理の専門家です。"
            "与えられた日本語のファイル名リストを、資料の種別で分類してください。"
            "出力は JSON オブジェクトで、キーをファイル名、値をカテゴリ名（日本語・簡潔）としてください。"
            "ファイル内容は与えられません。名前から推測してください。"
        )
        user = "ファイル名リスト:\n" + "\n".join(f"- {n}" for n in sample)
        data = self.provider.chat_json(system, user)
        if not isinstance(data, dict):
            return None
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def suggest_naming_convention(
        self, sample_names: list[str], categories: list[str]
    ) -> Optional[dict]:
        """命名規約・フォルダ体系の提案を返す（構造化 JSON）。"""
        if not self.available:
            return None
        system = (
            "あなたは社内のファイル整理ルール策定を支援する専門家です。"
            "与えられたファイル名の傾向とカテゴリ一覧をもとに、"
            "命名規約案とフォルダ体系案を提案してください。"
            "出力は JSON で、キー naming_rule（命名規約の文字列）、"
            "folder_policy（フォルダ体系の方針の文字列）、"
            "examples（改善後ファイル名の例の配列）を含めてください。"
        )
        user = (
            "カテゴリ一覧: " + ", ".join(categories) + "\n\n"
            "ファイル名の例:\n" + "\n".join(f"- {n}" for n in sample_names[:100])
        )
        data = self.provider.chat_json(system, user)
        return data if isinstance(data, dict) else None
