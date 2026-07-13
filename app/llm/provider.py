"""LLM provider 抽象層（ADR-0002）。

- ``bedrock``：ChatBedrockConverse（比賽正式環境）。憑證走標準 AWS credential
  chain，嚴禁寫死金鑰（Learner Lab 憑證每 session 輪換）。
- ``stub``：回傳 None，下游改走 scripted 模式（賽前開發 + demo fallback）。
"""

from __future__ import annotations

from app import config


def get_chat_model():
    """回傳 LangChain chat model；stub 模式回傳 None。"""
    if config.LLM_PROVIDER == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=config.BEDROCK_MODEL_ID,
            region_name=config.BEDROCK_REGION,
            temperature=0.2,
            max_tokens=1500,
        )
    return None


def get_bedrock_runtime():
    """多模態判讀用的 bedrock-runtime client；stub 模式回傳 None。"""
    if config.LLM_PROVIDER == "bedrock":
        import boto3

        return boto3.client("bedrock-runtime", region_name=config.BEDROCK_REGION)
    return None
