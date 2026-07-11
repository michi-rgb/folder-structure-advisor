"""LLM 補助（Azure OpenAI）。

任意機能。接続情報が無い／`openai` 未インストール／API 失敗のいずれの場合も
例外を投げず `None` を返し、呼び出し側はルールベース結果のみで完結する。

送信するのは **ファイル名・フォルダ名のリストのみ**。ファイル内容は一切送らない
（社外秘リスク低減）。

必要な環境変数：
  AZURE_OPENAI_ENDPOINT     例) https://<resource>.openai.azure.com/
  AZURE_OPENAI_API_KEY
  AZURE_OPENAI_DEPLOYMENT   デプロイ名（モデル名ではなくデプロイ名）
  AZURE_OPENAI_API_VERSION  例) 2024-10-21
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AzureConfig:
    endpoint: str
    api_key: str
    deployment: str
    api_version: str

    @classmethod
    def from_env(cls) -> Optional["AzureConfig"]:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
        if endpoint and api_key and deployment:
            return cls(endpoint, api_key, deployment, api_version)
        return None


class LLMHelper:
    """Azure OpenAI による分類・命名規約提案の補助。"""

    def __init__(self, config: Optional[AzureConfig] = None) -> None:
        self.config = config or AzureConfig.from_env()
        self._client = None
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.config is not None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.config:
            return None
        try:
            from openai import AzureOpenAI  # 遅延 import（未インストールでも本体は動く）
        except ImportError:
            self.last_error = "openai パッケージが未インストールです（pip install openai）"
            return None
        try:
            self._client = AzureOpenAI(
                azure_endpoint=self.config.endpoint,
                api_key=self.config.api_key,
                api_version=self.config.api_version,
            )
        except Exception as exc:  # noqa: BLE001 - 接続失敗は握って None
            self.last_error = f"AzureOpenAI クライアント初期化失敗: {exc}"
            return None
        return self._client

    def _chat_json(self, system: str, user: str) -> Optional[dict]:
        client = self._get_client()
        if client is None or self.config is None:
            return None
        try:
            resp = client.chat.completions.create(
                model=self.config.deployment,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:  # noqa: BLE001 - API 失敗はフォールバック
            self.last_error = f"Azure OpenAI 呼び出し失敗: {exc}"
            return None

    # -- 公開メソッド --------------------------------------------------------
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
        data = self._chat_json(system, user)
        if not isinstance(data, dict):
            return None
        # 値が文字列のものだけ採用。
        return {k: v for k, v in data.items() if isinstance(v, str)}

    def suggest_naming_convention(
        self, sample_names: list[str], categories: list[str]
    ) -> Optional[dict]:
        """命名規約・フォルダ体系の提案を返す（自由記述の構造化 JSON）。"""
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
        data = self._chat_json(system, user)
        return data if isinstance(data, dict) else None
