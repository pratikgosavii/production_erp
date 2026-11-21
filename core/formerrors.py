"""One way of telling the operator which field a form is complaining about.

There were four conventions before this: accounts dropped the field name and
sent one toast per error, so an empty Repeat Order produced seven identical
"This field is required." toasts with nothing to say which seven; design
showed only the first complaint; masters printed the raw database column; and
sales had its own helper that never saw the item rows at all. A message that
does not name the field leaves the operator hunting, which on a form the size
of a quotation means hunting a long way.

A source is a form, a formset, or a (form_or_formset, prefix) pair. In a
prefix, {n} becomes the 1-based row number, so a formset says which row:

    form_error_text(form, (item_formset, 'Item {n}'))
    -> 'Item 2 Quantity: Enter a whole number.'
"""
from django.contrib import messages


def field_label(form, name):
    """What the operator reads above the box, not the database column."""
    if name in ('__all__', None, ''):
        return ''
    field = form.fields.get(name)
    label = getattr(field, 'label', None) if field else None
    return str(label) if label else name.replace('_', ' ').capitalize()


def _bits(form, prefix):
    for name, errors in form.errors.items():
        label = field_label(form, name)
        head = ' '.join(x for x in (prefix, label) if x)
        text = ' '.join(str(e) for e in errors)
        yield f'{head}: {text}' if head else text


def _walk(source):
    """Yield 'where: what' lines for a form, a formset, or a pair."""
    prefix = ''
    if isinstance(source, (tuple, list)):
        source, prefix = source[0], source[1] or ''
    if source is None:
        return
    if hasattr(source, 'forms'):                      # a formset
        for err in source.non_form_errors():
            yield f'{prefix}: {err}'.lstrip(': ') if prefix else str(err)
        for i, form in enumerate(source.forms, start=1):
            # Rows the operator deleted, and the spare blank rows every
            # formset carries, are not theirs to answer for.
            if not form.errors or getattr(form, 'cleaned_data', {}).get('DELETE'):
                continue
            yield from _bits(form, prefix.replace('{n}', str(i)))
    else:
        yield from _bits(source, prefix.replace('{n}', ''))


def form_error_text(*sources, prefixes=None):
    """'Quantity: Enter a whole number. · Item 2 Rate / unit: Enter a number.'"""
    prefixes = prefixes or {}
    out = []
    for source in sources:
        if not isinstance(source, (tuple, list)) and id(source) in prefixes:
            source = (source, prefixes[id(source)])
        out.extend(_walk(source))
    return ' · '.join(x for x in out if x)


def _forms_of(source):
    if isinstance(source, (tuple, list)):
        source = source[0]
    if source is None:
        return []
    return list(source.forms) if hasattr(source, 'forms') else [source]


def error_field_names(*sources):
    """The posted names of every field that objected, for the page to mark.

    These are html_names (`items-1-tiers-0-rate_per_unit`), so the page can
    find the box the operator typed in without the template having to repeat
    an {% if %} against every field it renders.
    """
    names = []
    for source in sources:
        for form in _forms_of(source):
            for name in form.errors:
                if name == '__all__':
                    continue
                try:
                    names.append(form[name].html_name)
                except KeyError:
                    continue
    return names


def report_form_errors(request, *sources, prefix='', fallback='', prefixes=None):
    """Put a form's complaints on screen as one message that names its fields.

    Returns True when something was reported, so a caller can tell the
    difference between "the form objected" and "something else went wrong".
    """
    text = form_error_text(*sources, prefixes=prefixes)
    if not text:
        if not fallback:
            return False
        text = fallback
    messages.error(request, f'{prefix} — {text}' if prefix else text)
    return True
