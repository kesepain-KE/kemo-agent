"""运行态领域服务的兼容组合入口。"""

from web.services.overview import OverviewServiceMixin
from web.services.runtime_status import RuntimeStatusServiceMixin, _usage_cache_tokens


class RuntimeServiceMixin(RuntimeStatusServiceMixin, OverviewServiceMixin):
    """Compose runtime status and overview behavior for WebRunService."""


__all__ = ["RuntimeServiceMixin", "_usage_cache_tokens"]

