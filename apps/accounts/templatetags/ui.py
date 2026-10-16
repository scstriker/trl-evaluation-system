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
