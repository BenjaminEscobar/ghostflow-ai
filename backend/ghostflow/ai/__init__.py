from ghostflow.ai.annotate import WorkflowAnnotation, annotate_workflow
from ghostflow.ai.base import ModelProvider, ModelProviderError
from ghostflow.ai.meta import MetaModelProvider
from ghostflow.ai.mock import MockModelProvider
from ghostflow.config import Settings


def provider_from_settings(settings: Settings) -> ModelProvider:
    if settings.ai_provider == "meta":
        if settings.meta_api_key is None:
            raise ValueError("GHOSTFLOW_AI_PROVIDER=meta requires GHOSTFLOW_META_API_KEY")
        return MetaModelProvider(
            api_key=settings.meta_api_key,
            base_url=settings.meta_api_base_url,
            model=settings.meta_model,
            timeout_s=settings.meta_timeout_s,
        )
    return MockModelProvider()


__all__ = [
    "MetaModelProvider",
    "MockModelProvider",
    "ModelProvider",
    "ModelProviderError",
    "WorkflowAnnotation",
    "annotate_workflow",
    "provider_from_settings",
]
