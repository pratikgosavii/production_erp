"""Small HTTP helpers shared by the apps."""
from django.http import Http404
from django.shortcuts import get_object_or_404


def posted_object_or_404(klass, pk, **kwargs):
    """Look a record up by a primary key that arrived in a POST.

    get_object_or_404() turns a missing row into a 404, but it does not turn a
    pk that is not a number into one: the field raises ValueError first and the
    operator gets a crash page. A blank hidden field is the usual way this
    happens -- a stale page, or a form submitted before the JavaScript that
    fills it has run -- and "no such record" is what that means.
    """
    pk = (pk or '').strip() if isinstance(pk, str) else pk
    if pk in (None, ''):
        raise Http404('No record was named.')
    try:
        return get_object_or_404(klass, pk=pk, **kwargs)
    except (ValueError, TypeError) as exc:
        raise Http404('%r is not a record number.' % (pk,)) from exc
