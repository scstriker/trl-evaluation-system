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


@register.filter
def can_delete_project(user, project):
    from apps.evaluations.services import can_delete_project as allowed

    return allowed(user, project)


@register.filter
def split_words(text):
    return text.split()


@register.filter
def get_field(form, name):
    return form[name]


@register.filter
def levels_of(round_items):
    """专家评审轮次覆盖的级别，例如“1~3”。"""
    from apps.evaluations.services import format_levels

    return format_levels(sorted({ri.check_item.level for ri in round_items.select_related("check_item")}))
