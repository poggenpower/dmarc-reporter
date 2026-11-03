from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    query = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.filter
def country_flag(country_code):
    country_code = (country_code or "").upper()
    if len(country_code) != 2 or not country_code.isascii() or not country_code.isalpha():
        return "🌐"
    return "".join(chr(ord(letter) + 127397) for letter in country_code)
