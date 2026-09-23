from django import template

from apps.accounts.permissions import display_name

register = template.Library()


@register.filter
def who(user):
    """显示操作人：企业用户显示企业名称，管理员显示账号。"""
    return display_name(user)


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None
