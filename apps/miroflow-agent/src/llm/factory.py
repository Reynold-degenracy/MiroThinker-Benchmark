# Copyright (c) 2025 MiroMind
# This source code is licensed under the MIT License.

from typing import Optional

from omegaconf import DictConfig, OmegaConf

from ..logging.task_logger import TaskLog
from .providers.agenthub_client import AgentHubClient
from .providers.anthropic_client import AnthropicClient
from .providers.openai_client import OpenAIClient


def ClientFactory(
    run_id: str,
    api_session_id: str,
    cfg: DictConfig,
    task_log: Optional[TaskLog] = None,
    **kwargs,
):
    """
    Automatically select provider and create LLM client based on configuration
    """
    provider = cfg.llm.provider
    config = OmegaConf.merge(cfg, kwargs)

    client_creators = {
        "anthropic": lambda: AnthropicClient(
            run_id=run_id,
            api_session_id=api_session_id,
            task_log=task_log,
            cfg=config,
        ),
        "qwen": lambda: OpenAIClient(
            run_id=run_id,
            api_session_id=api_session_id,
            task_log=task_log,
            cfg=config,
        ),
        "openai": lambda: OpenAIClient(
            run_id=run_id,
            api_session_id=api_session_id,
            task_log=task_log,
            cfg=config,
        ),
        # AgentHub-routed providers (use agenthub AutoLLMClient)
        "agenthub": lambda: AgentHubClient(
            run_id=run_id,
            api_session_id=api_session_id,
            task_log=task_log,
            cfg=config,
        ),
        # "gemini": lambda: AgentHubClient(
        #     run_id=run_id, api_session_id=api_session_id, task_log=task_log, cfg=config
        # ),
        # "glm": lambda: AgentHubClient(
        #     run_id=run_id, api_session_id=api_session_id, task_log=task_log, cfg=config
        # ),
    }

    factory = client_creators.get(provider)
    if not factory:
        raise ValueError(f"Unsupported provider: {provider}")

    return factory()
