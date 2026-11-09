"""One way to paginate a list screen, so every screen does it the same way.

The screens here are filtered and searched before they are paged — the Sales
Manager picks "Flagged", Accounts picks "Needs Approval", everyone types in the
search box. A page link that drops those parameters silently throws the filter
away and shows the user page 2 of something else, which is the usual way
hand-rolled pagination goes wrong. ``querystring`` below carries every other
parameter through, so only ``page`` ever changes.

Rows arrive as plain lists as often as querysets (several views decorate their
rows in Python first), and Paginator handles both.
"""

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

#: Rows per page. One number, not a per-screen guess: the screens are all the
#: same shape — a wide table somebody scans — and a page that ends at a
#: different place on each one is harder to work with, not easier.
PER_PAGE = 25


def paginate(request, rows, per_page=PER_PAGE, param='page'):
    """Return (page, context) for ``rows``.

    ``page`` is a normal Django Page, so templates iterate it exactly as they
    iterated the full list before. A junk or out-of-range ``?page=`` lands on
    the first or last page rather than raising — a bad URL should not be a
    500, and a filter that just shrank below the current page number is a
    thing that happens every time somebody types in the search box.
    """
    paginator = Paginator(rows, per_page)
    raw = request.GET.get(param)
    try:
        page = paginator.page(raw)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    return page, {
        'page': page,
        'paginator': paginator,
        'is_paginated': paginator.num_pages > 1,
        'total_rows': paginator.count,
        'page_param': param,
        'querystring': _querystring(request, param),
    }


def _querystring(request, param):
    """Everything in the query string except the page number, ready to append.

    Returns '' or 'filter=flagged&q=veeransh&' — trailing separator included so
    a template can write href="?{{ querystring }}page=2" without worrying about
    whether anything came before it.
    """
    params = request.GET.copy()
    params.pop(param, None)
    # Drop blanks so an empty search box does not leave "q=&" on every link.
    for key in [k for k, v in params.lists() if not any(v)]:
        del params[key]
    encoded = params.urlencode()
    return f'{encoded}&' if encoded else ''
