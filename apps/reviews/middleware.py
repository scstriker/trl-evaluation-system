import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "trl-deadline-check"


class DeadlineCheckMiddleware:
    """已登录用户访问页面时顺带执行期限检查，每 10 分钟最多一次。

    单机部署不依赖定时任务；检查失败只记日志，不影响页面。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            getattr(settings, "DEADLINE_CHECK_ENABLED", True)
            and getattr(request, "user", None) is not None
            and request.user.is_authenticated
            and cache.add(CACHE_KEY, 1, getattr(settings, "DEADLINE_CHECK_INTERVAL", 600))
        ):
            try:
                from apps.reviews.deadlines import check_deadlines

                check_deadlines()
            except Exception:  # noqa: BLE001
                logger.exception("期限检查失败")
        return self.get_response(request)
